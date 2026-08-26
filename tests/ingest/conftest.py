"""Shared fixtures for ingest tests.

Locally, runs against a dedicated cricket_training_test database on the
same docker-compose Postgres instance, never the real cricket_training
training database - truncating the real corpus on every test run would be
a catastrophic (if accidental) way to lose Session 5's bulk load.
Migrations are applied to it via the same `supabase db push` mechanism
supabase/apply_migrations.py uses, so its schema is always the real one,
not a hand-maintained copy.

In CI (the `CI` env var GitHub Actions sets), ci.yml's postgres service is
already a fresh, disposable container with migrations pre-applied by the
workflow itself - these tests use LOCAL_DATABASE_URL directly there, no
second database or npx needed.
"""

import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

NPX = shutil.which("npx")


def _swap_dbname(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


def _local_database_url() -> str:
    # Real env vars (how ci.yml provides it) take precedence, matching
    # pydantic-settings' own env-file-vs-environment precedence.
    local_url = os.environ.get("LOCAL_DATABASE_URL") or dotenv_values(ENV_PATH).get("LOCAL_DATABASE_URL")
    if not local_url:
        pytest.skip(f"LOCAL_DATABASE_URL not set (checked environment and {ENV_PATH})")
    return local_url


@pytest.fixture(scope="session")
def test_db_url() -> str:
    local_url = _local_database_url()

    if os.environ.get("CI"):
        return local_url

    if NPX is None:
        pytest.skip("npx not found on PATH")
    test_url = _swap_dbname(local_url, "cricket_training_test")
    admin_url = _swap_dbname(local_url, "postgres")

    with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("cricket_training_test",))
        if cur.fetchone() is None:
            cur.execute('CREATE DATABASE "cricket_training_test"')

    subprocess.run(
        [NPX, "supabase", "db", "push", "--db-url", test_url],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return test_url


TABLES_TO_RESET = (
    "unresolved_entities",
    "predictions",
    "match_states",
    "deliveries",
    "elo_ratings",
    "matches",
    "player_aliases",
    "team_aliases",
    "venue_aliases",
    "players",
    "teams",
    "venues",
)


@pytest.fixture()
def conn(test_db_url):
    connection = psycopg.connect(test_db_url, autocommit=True)
    with connection.cursor() as cur:
        cur.execute(f"TRUNCATE {', '.join(TABLES_TO_RESET)} RESTART IDENTITY CASCADE")
    yield connection
    connection.close()
