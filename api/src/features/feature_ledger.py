"""The per-match inputs of the as-of summaries, and the rebuild from them.

See supabase/migrations/20260926000001_feature_ledger.sql for why the table
exists. In short: the daily Cricsheet Action must advance elo_asof_summary
and venue_asof_summary without the corpus, and the only exact way to do that
is to re-run the SAME rebuild code over every match's inputs. This module is
where "the same" is enforced:

  * LEDGER_SQL derives a ledger row from `matches` plus the two shared
    per-match fragments in features/venue_stats.py - the fragments the venue
    rebuild itself reads. It runs on the corpus (publish) and, unchanged, on
    the runner's throwaway database for newly loaded matches.
  * rebuild_summaries_from_ledger loads ledger rows into a scratch database's
    `matches` + `feature_ledger` and calls the unchanged
    features.elo.recompute_format and features.asof_summary.rebuild_all. The
    only difference from a local rebuild is where the venue rebuild reads its
    two per-match values from.

tests/ingest/test_daily_cricsheet.py proves the result hash-equal to the
local summaries over the whole corpus.

Usage (from api/src):
    python -m features.feature_ledger publish     # corpus -> Supabase
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from db.env import env_value
from features.asof_summary import (
    FIRST_INNINGS_PER_MATCH_GROUPED,
    rebuild_all,
    venue_summary_rebuild_sql,
)
from features.elo import FORMATS, recompute_format
from features.venue_stats import CHASE_PER_MATCH_SELECT

LEDGER_COLUMNS = (
    "match_id",
    "cricsheet_id",
    "format",
    "start_time",
    "team_a",
    "team_b",
    "winner",
    "result_method",
    "status",
    "venue_id",
    "chase_batting_team_won",
    "first_innings_runs",
)

# Every match, not only the ones with a venue or a result: Elo reads every
# complete match of a format, and the venue breakpoints are every match with
# a venue_id. LEFT JOINs, so "no chase row" and "no innings-1 deliveries"
# survive as NULL exactly as the rebuild's inner joins would drop them.
LEDGER_SQL = f"""
    WITH chase AS (
        {CHASE_PER_MATCH_SELECT}
    ), first_inns AS (
        {FIRST_INNINGS_PER_MATCH_GROUPED}
    )
    SELECT m.match_id, m.external_ids->>'cricsheet', m.format, m.start_time,
           m.team_a, m.team_b, m.winner, m.result_method, m.status, m.venue_id,
           chase.batting_team_won, first_inns.innings_total
    FROM matches m
    LEFT JOIN chase ON chase.match_id = m.match_id
    LEFT JOIN first_inns ON first_inns.match_id = m.match_id
    WHERE %(match_ids)s::int[] IS NULL OR m.match_id = ANY(%(match_ids)s::int[])
    ORDER BY m.match_id
"""

# The venue rebuild's two per-match inputs, read from the ledger instead of
# match_states/deliveries. Same column names the shared fragments produce.
LEDGER_VENUE_SUMMARY_SQL = venue_summary_rebuild_sql(
    chase_per_match="""
    SELECT match_id, chase_batting_team_won AS batting_team_won
    FROM feature_ledger
    WHERE chase_batting_team_won IS NOT NULL""",
    first_inns_per_match="""
    SELECT match_id, first_innings_runs AS innings_total
    FROM feature_ledger
    WHERE first_innings_runs IS NOT NULL""",
)

# `matches.competition` is NOT NULL; a ledger row only needs to exist in the
# scratch database for recompute_format and the breakpoints to see it.
_SCRATCH_COMPETITION = "feature_ledger"


def ledger_rows(conn, match_ids: list[int] | None = None) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(LEDGER_SQL, {"match_ids": match_ids})
        return cur.fetchall()


def fetch_ledger(conn) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(LEDGER_COLUMNS)} FROM feature_ledger ORDER BY match_id")
        return cur.fetchall()


def insert_ledger_rows(conn, rows: list[tuple]) -> int:
    """Insert, never update: a ledger row that already exists is left alone
    (idempotent re-runs). Returns how many were new."""
    if not rows:
        return 0
    placeholders = ", ".join(["%s"] * len(LEDGER_COLUMNS))
    inserted = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                f"INSERT INTO feature_ledger ({', '.join(LEDGER_COLUMNS)}) VALUES ({placeholders}) "
                f"ON CONFLICT (match_id) DO NOTHING",
                row,
            )
            inserted += cur.rowcount
    return inserted


def rebuild_summaries_from_ledger(scratch_conn, rows: list[tuple]) -> dict:
    """Rebuild both summaries in `scratch_conn` from ledger rows.

    `scratch_conn` may already hold real `matches` rows (the daily job's newly
    loaded matches); ledger rows for those ids must be in `rows` too, and are
    not re-inserted into `matches`. Commits.
    """
    index = {c: i for i, c in enumerate(LEDGER_COLUMNS)}
    with scratch_conn.cursor() as cur:
        cur.execute("DELETE FROM feature_ledger")
        cur.execute("SELECT match_id FROM matches")
        present = {r[0] for r in cur.fetchall()}
        cur.executemany(
            """
            INSERT INTO matches (match_id, competition, format, start_time, team_a, team_b,
                                 winner, result_method, status, venue_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    r[index["match_id"]],
                    _SCRATCH_COMPETITION,
                    r[index["format"]],
                    r[index["start_time"]],
                    r[index["team_a"]],
                    r[index["team_b"]],
                    r[index["winner"]],
                    r[index["result_method"]],
                    r[index["status"]],
                    r[index["venue_id"]],
                )
                for r in rows
                if r[index["match_id"]] not in present
            ],
        )
        placeholders = ", ".join(["%s"] * len(LEDGER_COLUMNS))
        cur.executemany(
            f"INSERT INTO feature_ledger ({', '.join(LEDGER_COLUMNS)}) VALUES ({placeholders})",
            rows,
        )
    scratch_conn.commit()

    for format_ in FORMATS:
        recompute_format(scratch_conn, format_)
    return rebuild_all(scratch_conn, venue_sql=LEDGER_VENUE_SUMMARY_SQL)


class LedgerAhead(RuntimeError):
    """Supabase's ledger holds matches the local corpus does not."""


def assert_corpus_caught_up(local_conn, supabase_conn) -> None:
    """Refuse a local -> Supabase push while the daily job is ahead.

    The daily Action appends matches to Supabase's ledger and rebuilds the
    summaries from it. A sync from a corpus that has not caught up would
    replace those summaries with older ones, silently undoing every day the
    Action has added. `ingest.daily_cricsheet catchup-local` first.
    """
    with supabase_conn.cursor() as cur:
        cur.execute("SELECT cricsheet_id FROM feature_ledger")
        remote = {r[0] for r in cur.fetchall()}
    with local_conn.cursor() as cur:
        cur.execute("SELECT external_ids->>'cricsheet' FROM matches WHERE external_ids ? 'cricsheet'")
        local = {r[0] for r in cur.fetchall()}
    missing = sorted(remote - local)
    if missing:
        raise LedgerAhead(
            f"Supabase's feature_ledger has {len(missing)} match(es) the local corpus lacks "
            f"(e.g. cricsheet {missing[:5]}). Pushing now would roll the as-of summaries back. "
            f"Run `python -m ingest.daily_cricsheet catchup-local` and rebuild first."
        )


def publish(local_conn, supabase_conn) -> dict:
    """Upsert the corpus ledger to Supabase. Refuses while Supabase is ahead,
    and refuses (via the cricsheet_id UNIQUE constraint) a match whose id
    differs between the two - the catch-up must have adopted Supabase's id."""
    assert_corpus_caught_up(local_conn, supabase_conn)
    rows = ledger_rows(local_conn)
    placeholders = ", ".join(["%s"] * len(LEDGER_COLUMNS))
    update = ", ".join(f"{c} = EXCLUDED.{c}" for c in LEDGER_COLUMNS if c != "match_id")
    with supabase_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM feature_ledger")
        before = cur.fetchone()[0]
        cur.executemany(
            f"INSERT INTO feature_ledger ({', '.join(LEDGER_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT (match_id) DO UPDATE SET {update}",
            rows,
        )
        cur.execute("SELECT count(*) FROM feature_ledger")
        after = cur.fetchone()[0]
    supabase_conn.commit()
    return {"local_rows": len(rows), "supabase_before": before, "supabase_after": after}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="feature_ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("publish", help="upsert the corpus ledger to Supabase")
    args = parser.parse_args(argv)

    local_url = env_value("LOCAL_DATABASE_URL")
    supabase_url = env_value("SUPABASE_SESSION_POOLER_URL")
    if not local_url or not supabase_url:
        sys.exit("LOCAL_DATABASE_URL and SUPABASE_SESSION_POOLER_URL must both be set")
    if args.command == "publish":
        with psycopg.connect(local_url) as local_conn, psycopg.connect(supabase_url) as supabase_conn:
            result = publish(local_conn, supabase_conn)
        print(
            f"feature_ledger: {result['local_rows']} corpus rows; Supabase "
            f"{result['supabase_before']} -> {result['supabase_after']} rows"
        )


if __name__ == "__main__":
    main()
