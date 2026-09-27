"""Syncs the small reference tables from local training Postgres to
Supabase (SPEC.md section 2.1 / Phase 0 session 4 assumption 7).

Two groups of tables, synced differently.

REFERENCE (venues/teams/players/*_aliases): the entity universe. Upserted by
primary key, with the sequence bumped afterwards so live inserts on Supabase
never collide with a synced row.

DERIVED (venue_asof_summary/elo_asof_summary, Phase 2 session 3): the as-of
summaries features/as_of.py reads at serving time. Replaced wholesale rather
than upserted - they are rebuilt from scratch by features.asof_summary, a
rebuild can shrink them, and an upsert would leave orphan rows behind that
nothing would ever notice. Their content hash is verified on the Supabase
side after the push, and only then recorded in reference_sync_state; the
worker refuses to serve if that hash stops matching (features/as_of.py's
assert_reference_fresh).

Never deliveries/matches/match_states/elo_ratings (the full bulk corpus
stays local only; at ~3.7M rows it would blow well past Supabase's 500MB
free tier - see the session 5 plan's Decision 1), and never
unresolved_entities (nothing in serving ever queries it). elo_ratings in
particular stays local because its match_id references matches; the derived
elo_asof_summary is what crosses instead.

Run after the local bulk load and after `python -m features.asof_summary
rebuild` - not incrementally per match. Safe to re-run.

Usage: python -m ingest.sync_reference_tables
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from db.defaults import SUPABASE_ID_FLOOR, SUPABASE_LIVE_ALIAS_FLOOR
from features.asof_summary import DERIVED_TABLES, DerivedTable, content_hash
from features.feature_ledger import assert_corpus_caught_up

# Kept in its own module rather than appended to asof_summary's tuple: these
# are not as-of summaries, they are career aggregates, and the two are
# rebuilt by different commands. Both cross the same way.
from features.player_summary import PLAYER_DERIVED_TABLES

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

# Order matters: aliases reference their parent table's id.
TABLES = (
    ("venues", "venue_id", ("venue_id", "name", "city", "country")),
    # full_member is in this tuple deliberately. Anything absent from it is
    # never copied, so the flag would read correctly on the corpus and
    # silently revert to the column DEFAULT of FALSE on Supabase the next
    # time reference tables sync - the exact DIVERGENCE shape
    # docs/column-census.md exists to surface.
    ("teams", "team_id", ("team_id", "name", "short_name", "full_member")),
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


ALIAS_TABLES = ("venue_aliases", "team_aliases", "player_aliases")


class AliasCollision(RuntimeError):
    """A corpus alias would overwrite a different alias on Supabase."""


def _refuse_alias_overwrites(supabase_conn, table: str, columns: tuple[str, ...], rows) -> None:
    """Never overwrite an alias row this sync did not create.

    An upsert by alias_id is only safe when the id means the same alias on
    both sides. The same (source, source_name) under the same id is the same
    alias - the entity it points at may be re-pointed by a review decision,
    which is exactly what the sync exists to carry. A DIFFERENT (source,
    source_name) is a different alias that another allocator gave this id,
    and replacing it would erase it without a trace. Refused, with nothing
    written.
    """
    index = {c: i for i, c in enumerate(columns)}
    local = {r[index["alias_id"]]: (r[index["source"]], r[index["source_name"]]) for r in rows}
    with supabase_conn.cursor() as cur:
        cur.execute(
            f"SELECT alias_id, source, source_name FROM {table} WHERE alias_id = ANY(%s)", (list(local),)
        )
        clashes = [(a, local[a], (src, name)) for a, src, name in cur.fetchall() if local[a] != (src, name)]
    if clashes:
        detail = "; ".join(f"{a}: corpus {c} vs Supabase {s}" for a, c, s in clashes[:5])
        raise AliasCollision(
            f"{table}: {len(clashes)} alias id(s) mean different aliases on the corpus and Supabase "
            f"({detail}). Nothing was written. See db/defaults.py's alias bands."
        )


def align_alias_counters(local_conn, supabase_conn) -> dict[str, tuple[int, int]]:
    """Put each alias allocator's counter in its band. Idempotent.

    Supabase's sequences (the live worker's) go to the live band, never
    lower. The corpus's sequences are moved past any id Supabase already uses
    below the live band - the live worker's rows from before the bands
    existed - so the corpus can never hand one of them out again.
    Returns {table: (supabase next, corpus next)}.
    """
    result = {}
    for table in ALIAS_TABLES:
        with supabase_conn.cursor() as cur:
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'alias_id'), GREATEST("
                f"(SELECT MAX(alias_id) FROM {table} WHERE alias_id >= %s AND alias_id < %s), %s - 1))",
                (SUPABASE_LIVE_ALIAS_FLOOR, SUPABASE_ID_FLOOR, SUPABASE_LIVE_ALIAS_FLOOR),
            )
            supabase_next = cur.fetchone()[0] + 1
            cur.execute(f"SELECT coalesce(MAX(alias_id), 0) FROM {table} WHERE alias_id < %s", (SUPABASE_LIVE_ALIAS_FLOOR,))
            below_live = cur.fetchone()[0]
        with local_conn.cursor() as cur:
            cur.execute(f"SELECT pg_get_serial_sequence('{table}', 'alias_id')")
            sequence = cur.fetchone()[0]
            cur.execute(f"SELECT last_value FROM {sequence}")
            local_last = cur.fetchone()[0]
            if local_last < below_live:
                cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'alias_id'), %s)", (below_live,))
                local_last = below_live
        result[table] = (supabase_next, local_last + 1)
    supabase_conn.commit()
    local_conn.commit()
    return result


def sync_table(local_conn, supabase_conn, table: str, id_column: str, columns: tuple[str, ...]) -> int:
    column_list = ", ".join(columns)
    with local_conn.cursor() as cur:
        cur.execute(f"SELECT {column_list} FROM {table} ORDER BY {id_column}")
        rows = cur.fetchall()
    if not rows:
        return 0
    if table in ALIAS_TABLES:
        _refuse_alias_overwrites(supabase_conn, table, columns, rows)

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
        # worker) never collide with a synced row - but BELOW
        # SUPABASE_ID_FLOOR. Rows in that band are the daily Cricsheet job's
        # first-seen teams and venues; a plain MAX would move the sequence
        # into the band and let any serial insert allocate there.
        if table not in ALIAS_TABLES:
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{id_column}'), "
                f"GREATEST((SELECT MAX({id_column}) FROM {table} WHERE {id_column} < %s), 1))",
                (SUPABASE_ID_FLOOR,),
            )
    supabase_conn.commit()
    if table in ALIAS_TABLES:
        # The live worker allocates aliases from this sequence: its band.
        align_alias_counters(local_conn, supabase_conn)
    return len(rows)


class SyncVerificationFailed(RuntimeError):
    """The pushed copy does not hash to what the local copy hashes to."""


def sync_derived_table(local_conn, supabase_conn, table: DerivedTable) -> int:
    """Replace-and-verify. Returns the row count pushed.

    The hash is computed on BOTH sides and compared before anything is
    recorded in reference_sync_state. If the push was partial, the row
    simply doesn't get written, and the worker's startup check then fails
    loudly on a missing or mismatched hash rather than serving half a table.
    """
    column_list = ", ".join(table.columns)
    with local_conn.cursor() as cur:
        cur.execute(f"SELECT {column_list} FROM {table.name} ORDER BY {table.order_by}")
        rows = cur.fetchall()
    local_hash, local_count = content_hash(local_conn, table)

    placeholders = ", ".join(["%s"] * len(table.columns))
    with supabase_conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table.name}")
        if rows:
            cur.executemany(
                f"INSERT INTO {table.name} ({column_list}) VALUES ({placeholders})", rows
            )

    remote_hash, remote_count = content_hash(supabase_conn, table)
    if (remote_hash, remote_count) != (local_hash, local_count):
        supabase_conn.rollback()
        raise SyncVerificationFailed(
            f"{table.name}: local {local_count} rows / {local_hash}, "
            f"Supabase {remote_count} rows / {remote_hash} after push - rolled back"
        )

    with supabase_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO reference_sync_state
                (table_name, content_hash, row_count, rebuilt_at, synced_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (table_name) DO UPDATE SET
                content_hash = EXCLUDED.content_hash,
                row_count = EXCLUDED.row_count,
                rebuilt_at = EXCLUDED.rebuilt_at,
                synced_at = EXCLUDED.synced_at
            """,
            (table.name, local_hash, local_count, _local_rebuilt_at(local_conn, table), datetime.now(timezone.utc)),
        )
    supabase_conn.commit()

    # Stamp the local row too, so `synced_at IS NULL` locally means exactly
    # "rebuilt since the last successful sync".
    with local_conn.cursor() as cur:
        cur.execute(
            "UPDATE reference_sync_state SET synced_at = %s WHERE table_name = %s",
            (datetime.now(timezone.utc), table.name),
        )
    local_conn.commit()
    return local_count


def _local_rebuilt_at(local_conn, table: DerivedTable):
    with local_conn.cursor() as cur:
        cur.execute(
            "SELECT rebuilt_at FROM reference_sync_state WHERE table_name = %s", (table.name,)
        )
        row = cur.fetchone()
    if row is None:
        sys.exit(
            f"no local reference_sync_state row for {table.name} - "
            f"run `python -m features.asof_summary rebuild` first"
        )
    return row[0]


def run() -> None:
    local_url, supabase_url = _env_urls()
    with psycopg.connect(local_url) as local_conn, psycopg.connect(supabase_url) as supabase_conn:
        # The daily Cricsheet job rebuilds the summaries on Supabase from a
        # ledger that may be ahead of this corpus; pushing now would undo it.
        assert_corpus_caught_up(local_conn, supabase_conn)
        align_alias_counters(local_conn, supabase_conn)
        for table, id_column, columns in TABLES:
            count = sync_table(local_conn, supabase_conn, table, id_column, columns)
            print(f"synced {count} rows -> {table}")
        for derived in (*DERIVED_TABLES, *PLAYER_DERIVED_TABLES):
            count = sync_derived_table(local_conn, supabase_conn, derived)
            print(f"synced {count} rows -> {derived.name} (hash verified on Supabase)")


if __name__ == "__main__":
    run()
