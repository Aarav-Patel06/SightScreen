"""Shared fixtures for tests that need a writable, truncatable database.

Moved here from tests/ingest/conftest.py (Phase 1 session 2) - Phase 1's
as-of poison-pill tests (tests/features/test_venue_stats.py, an added test in
tests/features/test_elo.py) need to INSERT a synthetic future match, which
must never happen against the real, hand-verified cricket_training corpus.
This fixture was already exactly what ingest's tests needed for the same
reason; promoting it to the shared tests/ root conftest (rather than
duplicating it in a second directory) makes it available to any test
directory instead of just tests/ingest/.

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

REPO_ROOT = Path(__file__).resolve().parent.parent
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
    "reference_sync_state",
    "venue_asof_summary",
    "elo_asof_summary",
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
