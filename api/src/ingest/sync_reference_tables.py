"""Syncs the small reference tables from local training Postgres to
Supabase (SPEC.md section 2.1 / Phase 0 session 4 assumption 7).

Only venues/teams/players/player_aliases/team_aliases/venue_aliases -
never deliveries/matches/match_states (the full bulk corpus stays local
only; at ~3.7M rows it would blow well past Supabase's 500MB free tier -
see the session 5 plan's Decision 1) and never unresolved_entities
(nothing in serving ever queries it).

Run once, after the local bulk load completes - not incrementally per
match. Upserts by primary key, so it's safe to re-run.

Usage: python -m ingest.sync_reference_tables
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

# Order matters: aliases reference their parent table's id.
TABLES = (
    ("venues", "venue_id", ("venue_id", "name", "city", "country")),
    ("teams", "team_id", ("team_id", "name", "short_name")),
    ("players", "player_id", ("player_id", "canonical_name", "batting_hand", "bowling_style", "dob")),
    ("venue_aliases", "alias_id", ("alias_id", "venue_id", "source", "source_name", "source_id")),
    ("team_aliases", "alias_id", ("alias_id", "team_id", "source", "source_name", "source_id")),
    ("player_aliases", "alias_id", ("alias_id", "player_id", "source", "source_name", "source_id")),
)


def _env_urls() -> tuple[str, str]:
    env = dotenv_values(ENV_PATH)
    local_url = env.get("LOCAL_DATABASE_URL")
    supabase_url = env.get("SUPABASE_SESSION_POOLER_URL")
    if not local_url or not supabase_url:
        sys.exit(f"LOCAL_DATABASE_URL and SUPABASE_SESSION_POOLER_URL must both be set in {ENV_PATH}")
    return local_url, supabase_url


def sync_table(local_conn, supabase_conn, table: str, id_column: str, columns: tuple[str, ...]) -> int:
    column_list = ", ".join(columns)
    with local_conn.cursor() as cur:
        cur.execute(f"SELECT {column_list} FROM {table} ORDER BY {id_column}")
        rows = cur.fetchall()
    if not rows:
        return 0

    placeholders = ", ".join(["%s"] * len(columns))
    update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != id_column)
    upsert_sql = (
        f"INSERT INTO {table} ({column_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({id_column}) DO UPDATE SET {update_clause}"
    )
    with supabase_conn.cursor() as cur:
        cur.executemany(upsert_sql, rows)
        # Keep the sequence ahead of the highest synced id, so future
        # inserts made directly against Supabase (e.g. Phase 2's live
        # worker) never collide with a synced row.
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{id_column}'), "
            f"GREATEST((SELECT MAX({id_column}) FROM {table}), 1))"
        )
    supabase_conn.commit()
    return len(rows)


def run() -> None:
    local_url, supabase_url = _env_urls()
    with psycopg.connect(local_url) as local_conn, psycopg.connect(supabase_url) as supabase_conn:
        for table, id_column, columns in TABLES:
            count = sync_table(local_conn, supabase_conn, table, id_column, columns)
            print(f"synced {count} rows -> {table}")


if __name__ == "__main__":
    run()
