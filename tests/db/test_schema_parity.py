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

WHAT THIS COVERS, and what it does not. Stated because a gate's NAME
routinely overstates its breadth, and this one did: until 2026-09-18 it was
called a schema-parity test while enumerating only columns, indexes and
constraints. Row-level security, grants and publication membership were
outside it - so the two databases diverged completely on all three and the
"schemas are identical" gate stayed green. Hosted Supabase grants `anon`
SELECT on public tables by default, which meant thirteen tables were
world-readable there and unreadable locally, including 25,290 rows of
elo_asof_summary and 18,468 of player_aliases. Found by
web/scripts/check-anon-access.mjs's negative control, not by this test.

Now covered: columns, indexes, constraints, RLS enablement, policies, table
grants for anon/authenticated, and supabase_realtime publication membership.

Still NOT covered, deliberately and explicitly:
  - REPLICA IDENTITY. Irrelevant for INSERT-only subscriptions (the WAL
    record carries the full new tuple), load-bearing the moment anyone
    subscribes to UPDATE or DELETE. No decision is recorded anywhere, so
    there is nothing to assert yet.
  - Column-level grants, triggers, functions, sequences, extensions.
  - Row DATA. That is tests/db/test_asof_parity.py's job.
  - Whether a policy's USING clause is *correct*, only that it matches
    across the two databases.
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

# Whether RLS is switched on, per table. An RLS-enabled table with no policy
# denies every non-bypassing role, which is the intended default for anything
# a browser must not read.
RLS_QUERY = """
    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
    ORDER BY c.relname
"""

POLICIES_QUERY = """
    SELECT tablename, policyname, cmd, roles::text, qual, with_check
    FROM pg_policies
    WHERE schemaname = 'public'
    ORDER BY tablename, policyname
"""

# The privilege that actually granted the access RLS was assumed to be
# gating. Restricted to the two browser-facing roles: `postgres`,
# `service_role` and the Supabase internal roles legitimately differ between
# a local Docker instance and a hosted project, so comparing every grantee
# would produce noise that trains people to ignore this test.
GRANTS_QUERY = """
    SELECT table_name, grantee, privilege_type
    FROM information_schema.role_table_grants
    WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated')
    ORDER BY table_name, grantee, privilege_type
"""

# A table with perfect policies that is not in the publication delivers
# nothing, and from the browser that looks identical to a policy problem.
PUBLICATION_QUERY = """
    SELECT pubname, tablename
    FROM pg_publication_tables
    WHERE schemaname = 'public'
    ORDER BY pubname, tablename
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
            ("rls", RLS_QUERY),
            ("policies", POLICIES_QUERY),
            ("grants", GRANTS_QUERY),
            ("publication", PUBLICATION_QUERY),
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


@pytest.mark.parametrize(
    "snapshot_key",
    ["columns", "indexes", "constraints", "rls", "policies", "grants", "publication"],
)
def test_schemas_are_identical(snapshots, snapshot_key):
    local_snapshot, supabase_snapshot = snapshots
    local_rows = local_snapshot[snapshot_key]
    supabase_rows = supabase_snapshot[snapshot_key]

    assert local_rows == supabase_rows, (
        f"public schema '{snapshot_key}' differ between local training DB and Supabase:\n"
        f"{_diff(local_rows, supabase_rows)}"
    )
