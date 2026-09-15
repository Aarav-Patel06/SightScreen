"""Rebuilds the as-of summary tables the serving path reads (SPEC.md
section 15's 2026-09-10 decision, Phase 2 session 3).

Two derived tables, same shape - (entity, date, cumulative value) - built
from the local corpus and pushed to Supabase by
`ingest.sync_reference_tables`. They exist so the live worker can compute
`elo_diff`, `venue_chase_win_rate` and `venue_avg_first_innings` without
`matches`, `match_states`, `deliveries` or `elo_ratings`, none of which go
to Supabase (section 2.1).

Idempotent and total: both rebuilds DELETE everything and re-derive from
the corpus, so a summary can never carry a stale row from an earlier shape.
That matters more here than for an incremental update, because a summary
that silently disagrees with the corpus is precisely the failure this
session exists to prevent.

Usage (from the api/ directory, with api/.env configured):
    python -m features.asof_summary rebuild

Run it after `features.match_state rebuild` and `features.elo rebuild`, and
before `ingest.sync_reference_tables` - see supabase/SCHEMA.md.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from features.venue_stats import (
    CHASE_PER_MATCH_SELECT,
    CHASE_WIN_EXPR,
    FIRST_INNINGS_PER_MATCH_SELECT,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"


@dataclass(frozen=True)
class DerivedTable:
    """A table that is derived locally and synced, rather than written to.

    `order_by` is the natural key. It does double duty: it makes the content
    hash deterministic, and it is the transfer order for the sync.

    `hash_expr` renders a row to canonical text, column by column, instead of
    the more obvious `t::text`. That is not fussiness - `t::text` is
    SESSION-DEPENDENT for some types, and it bit this sync on its first run:
    Supabase's pooler hands out sessions with `extra_float_digits = 0` while
    local Postgres uses the 12+ default of 1, so an identical float4 rendered
    as '1494.73' on one side and '1494.7292' on the other and the hashes
    disagreed over byte-identical data. That symptom is gone now that
    elo_asof_summary stores NUMERIC (see the migration), but the explicit
    rendering stays: to_char also pins the date against DateStyle, and no
    future column can reintroduce the problem unnoticed.
    """

    name: str
    columns: tuple[str, ...]
    order_by: str
    hash_expr: str


VENUE_SUMMARY = DerivedTable(
    "venue_asof_summary",
    ("venue_id", "effective_date", "chase_wins", "chase_n", "first_inns_runs", "first_inns_n"),
    "venue_id, effective_date",
    "venue_id::text || '|' || to_char(effective_date, 'YYYY-MM-DD') || '|' || "
    "chase_wins::text || '|' || chase_n::text || '|' || "
    "first_inns_runs::text || '|' || first_inns_n::text",
)
ELO_SUMMARY = DerivedTable(
    "elo_asof_summary",
    ("team_id", "format", "effective_date", "rating"),
    "team_id, format, effective_date",
    "team_id::text || '|' || format || '|' || to_char(effective_date, 'YYYY-MM-DD') || '|' || "
    "rating::text",
)
DERIVED_TABLES = (VENUE_SUMMARY, ELO_SUMMARY)


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


# --- Venue summary ---------------------------------------------------------
# One row per (venue, date a match was played there), carrying the cumulative
# counters INCLUDING that date. A lookup then takes the latest row strictly
# before as_of_date, which accumulates exactly the matches with
# start_time < as_of_date - any match in between would itself be a breakpoint.
#
# The date key is (start_time AT TIME ZONE 'UTC')::date, not start_time::date,
# so it does not depend on the session timezone. Asserted equivalent to the
# corpus: no start_time is off midnight, and deliveries.match_date (what
# training keys on) equals it for every row - both checked in
# tests/features/test_asof_summary.py.

VENUE_SUMMARY_REBUILD_SQL = f"""
WITH chase_per_match AS (
    {CHASE_PER_MATCH_SELECT}
), chase_per_day AS (
    SELECT m2.venue_id,
           (m2.start_time AT TIME ZONE 'UTC')::date AS effective_date,
           sum({CHASE_WIN_EXPR}) AS wins,
           count(*) AS n
    FROM chase_per_match
    JOIN matches m2 ON m2.match_id = chase_per_match.match_id
    WHERE m2.venue_id IS NOT NULL
    GROUP BY 1, 2
), first_inns_per_match AS (
    {FIRST_INNINGS_PER_MATCH_SELECT}
    GROUP BY d.match_id
), first_inns_per_day AS (
    SELECT m3.venue_id,
           (m3.start_time AT TIME ZONE 'UTC')::date AS effective_date,
           sum(first_inns_per_match.innings_total) AS runs,
           count(*) AS n
    FROM first_inns_per_match
    JOIN matches m3 ON m3.match_id = first_inns_per_match.match_id
    WHERE m3.venue_id IS NOT NULL
    GROUP BY 1, 2
), breakpoints AS (
    SELECT DISTINCT venue_id, (start_time AT TIME ZONE 'UTC')::date AS effective_date
    FROM matches
    WHERE venue_id IS NOT NULL
), per_day AS (
    SELECT b.venue_id, b.effective_date,
           -- 0.0 not 0: keeps the running sum at numeric scale 1 so that
           -- chase_wins / chase_n reproduces avg()'s numeric_div exactly.
           coalesce(c.wins, 0.0) AS wins, coalesce(c.n, 0) AS chase_n,
           coalesce(f.runs, 0) AS runs, coalesce(f.n, 0) AS first_inns_n
    FROM breakpoints b
    LEFT JOIN chase_per_day c USING (venue_id, effective_date)
    LEFT JOIN first_inns_per_day f USING (venue_id, effective_date)
)
INSERT INTO venue_asof_summary
    (venue_id, effective_date, chase_wins, chase_n, first_inns_runs, first_inns_n)
SELECT venue_id, effective_date,
       sum(wins) OVER w, sum(chase_n) OVER w,
       sum(runs) OVER w, sum(first_inns_n) OVER w
FROM per_day
WINDOW w AS (PARTITION BY venue_id ORDER BY effective_date ROWS UNBOUNDED PRECEDING)
"""


# --- Elo summary -----------------------------------------------------------
# elo_ratings is already an as-of time series; this collapses it to a date
# grain so serving never needs the corpus-bound table.
#
# `, match_id DESC` is the whole subtlety. Every matches.start_time in this
# corpus is exactly midnight, so a team playing twice on one date writes two
# elo_ratings rows with an IDENTICAL as_of. recompute_format walks matches
# `ORDER BY start_time, match_id`, so the highest match_id on a date is that
# date's last match and its rating is the end-of-day rating - the one an
# as-of lookup on any later date should see.
#
# Measured on the real corpus before this was written: 372 tied groups, and
# the pre-session-3 `ORDER BY as_of DESC LIMIT 1` returned the EARLIER
# match's rating for 137 of them, chosen by heap order. That is a real
# non-determinism in the shipped helper, not a new constraint the summary
# introduces; see docs/phase2-session3-asof-sync.md.

ELO_SUMMARY_REBUILD_SQL = """
INSERT INTO elo_asof_summary (team_id, format, effective_date, rating)
SELECT DISTINCT ON (team_id, format, effective_date)
       team_id, format, effective_date, rating::text::numeric
FROM (
    SELECT team_id, format, (as_of AT TIME ZONE 'UTC')::date AS effective_date,
           as_of, match_id, rating
    FROM elo_ratings
) e
ORDER BY team_id, format, effective_date, as_of DESC, match_id DESC
"""

# The cast is `::text::numeric`, deliberately, and NOT the obvious
# `::numeric`. Postgres's float4 -> numeric conversion is hardcoded to
# FLT_DIG (6) significant digits and ignores extra_float_digits entirely:
# it turns 1601.9048 into 1601.9. Verified, not assumed. Going via text uses
# float4out, which honours the setting below and emits the shortest exact
# representation - the same string psycopg has always parsed on the training
# path, so the values the model was fit against are unchanged.
#
# Pinned here, at the one place the cast happens, so a rebuild run through a
# pooler that defaults extra_float_digits to 0 cannot quietly store 1601.9
# where every earlier rebuild stored 1601.9048. Postgres 12+ treats any
# value > 0 as "shortest exact representation".
ELO_REBUILD_FLOAT_DIGITS = "SET extra_float_digits = 1"


def rebuild_venue_summary(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM venue_asof_summary")
        cur.execute(VENUE_SUMMARY_REBUILD_SQL)
        cur.execute("SELECT count(*) FROM venue_asof_summary")
        return cur.fetchone()[0]


def rebuild_elo_summary(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(ELO_REBUILD_FLOAT_DIGITS)
        cur.execute("DELETE FROM elo_asof_summary")
        cur.execute(ELO_SUMMARY_REBUILD_SQL)
        cur.execute("SELECT count(*) FROM elo_asof_summary")
        return cur.fetchone()[0]


# --- Content hashing -------------------------------------------------------

def content_hash(conn, table: DerivedTable) -> tuple[str, int]:
    """(hash, row_count) over a derived table's full contents.

    Same construction as tests/features/test_elo.py's idempotency check:
    md5 of the concatenated per-row md5s in natural-key order. Cheap enough
    to recompute at worker startup. See DerivedTable.hash_expr for why the
    row is rendered column by column rather than with `t::text`.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT coalesce("
            f"  md5(string_agg(md5({table.hash_expr}), '' ORDER BY {table.order_by})), ''), "
            f"count(*) FROM {table.name}"
        )
        return cur.fetchone()


def record_sync_state(conn, table: DerivedTable, *, synced: bool) -> tuple[str, int]:
    """Upserts this table's row in reference_sync_state and returns its hash.

    A rebuild records `synced_at = NULL`: the local copy has moved and the
    Supabase copy has not been proven to match yet. The sync stamps it only
    after re-reading the pushed rows.
    """
    digest, row_count = content_hash(conn, table)
    with conn.cursor() as cur:
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
            (
                table.name,
                digest,
                row_count,
                datetime.now(timezone.utc),
                datetime.now(timezone.utc) if synced else None,
            ),
        )
    return digest, row_count


def rebuild_all(conn) -> dict:
    """Both summaries plus their sync-state rows, in one transaction."""
    venue_rows = rebuild_venue_summary(conn)
    elo_rows = rebuild_elo_summary(conn)
    hashes = {t.name: record_sync_state(conn, t, synced=False)[0] for t in DERIVED_TABLES}
    conn.commit()
    return {
        "venue_asof_summary_rows": venue_rows,
        "elo_asof_summary_rows": elo_rows,
        "content_hashes": hashes,
    }


def rebuild() -> dict:
    start = time.monotonic()
    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
        result = rebuild_all(conn)
    result["elapsed_seconds"] = time.monotonic() - start

    print(f"As-of summaries rebuilt in {result['elapsed_seconds']:.1f}s")
    print(f"  venue_asof_summary: {result['venue_asof_summary_rows']} rows")
    print(f"  elo_asof_summary:   {result['elo_asof_summary_rows']} rows")
    for name, digest in result["content_hashes"].items():
        print(f"  {name} hash {digest}")
    print("Run `python -m ingest.sync_reference_tables` to push these to Supabase.")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="asof_summary")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("rebuild", help="rebuild both as-of summary tables from the corpus")
    args = parser.parse_args(argv)
    if args.command == "rebuild":
        rebuild()


if __name__ == "__main__":
    main()
