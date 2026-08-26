"""Verifies the local training DB and Supabase have byte-for-byte identical
public schemas, as SPEC.md section 5 requires ("applied to both ... so the
two schemas never diverge") - as a test, not an eyeball.

Local-only, like the exact-match reproduction test: it needs live
credentials to both databases, which CI doesn't have. Run manually after
every `python supabase/apply_migrations.py` call:

    pytest tests/db/test_schema_parity.py -v

Reads api/.env via python-dotenv rather than a shell `source` - a real
rotated Supabase password broke `source api/.env` outright earlier in this
repo's history (special shell characters get parsed, not just substituted).
"""

from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

COLUMNS_QUERY = """
    SELECT table_name, column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema = 'public'
    ORDER BY table_name, ordinal_position
"""

INDEXES_QUERY = """
    SELECT indexname, indexdef
    FROM pg_indexes
    WHERE schemaname = 'public'
    ORDER BY indexname
"""

CONSTRAINTS_QUERY = """
    SELECT conname, pg_get_constraintdef(oid)
    FROM pg_constraint
    WHERE connamespace = 'public'::regnamespace
    ORDER BY conname
"""


def _env() -> dict[str, str | None]:
    if not ENV_PATH.exists():
        pytest.skip(f"{ENV_PATH} not found - schema parity check needs both databases configured")
    return dotenv_values(ENV_PATH)


def _snapshot(db_url: str) -> dict[str, list[tuple]]:
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        snapshot = {}
        for name, query in (
            ("columns", COLUMNS_QUERY),
            ("indexes", INDEXES_QUERY),
            ("constraints", CONSTRAINTS_QUERY),
        ):
            cur.execute(query)
            snapshot[name] = cur.fetchall()
        return snapshot


@pytest.fixture(scope="module")
def snapshots() -> tuple[dict[str, list[tuple]], dict[str, list[tuple]]]:
    env = _env()
    local_url = env.get("LOCAL_DATABASE_URL")
    supabase_url = env.get("SUPABASE_SESSION_POOLER_URL")
    if not local_url or not supabase_url:
        pytest.skip("LOCAL_DATABASE_URL and SUPABASE_SESSION_POOLER_URL must both be set in api/.env")
    return _snapshot(local_url), _snapshot(supabase_url)


def _diff(local: list[tuple], supabase: list[tuple]) -> str:
    local_set, supabase_set = set(local), set(supabase)
    only_local = sorted(local_set - supabase_set)
    only_supabase = sorted(supabase_set - local_set)
    lines = []
    if only_local:
        lines.append("Only in local training DB:")
        lines.extend(f"  {row}" for row in only_local)
    if only_supabase:
        lines.append("Only in Supabase:")
        lines.extend(f"  {row}" for row in only_supabase)
    return "\n".join(lines)


@pytest.mark.parametrize("snapshot_key", ["columns", "indexes", "constraints"])
def test_schemas_are_identical(snapshots, snapshot_key):
    local_snapshot, supabase_snapshot = snapshots
    local_rows = local_snapshot[snapshot_key]
    supabase_rows = supabase_snapshot[snapshot_key]

    assert local_rows == supabase_rows, (
        f"public schema '{snapshot_key}' differ between local training DB and Supabase:\n"
        f"{_diff(local_rows, supabase_rows)}"
    )
