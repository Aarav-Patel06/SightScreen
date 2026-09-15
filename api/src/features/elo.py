"""Elo ratings as a time series (SPEC.md section 6.1, Phase 0 session 6).

Standard Elo, K=20, starting rating 1500, separate per format. Stored as a
time series - one row per team per completed match - never a mutable
current-rating column, so a rating can always be looked up as of any past
date without leaking future information (section 6.1's explicit warning:
"critical to avoid leakage").

No future match can influence a past rating, by construction rather than
convention: matches are processed in strict chronological order, and each
match's update reads only the most recently *written* rating for each team
(necessarily from an earlier match, since nothing later has been written
yet) before inserting this match's own new row. Rows are never updated or
deleted after being written - there is no mutable "current rating" column
to accidentally overwrite.

Team renames (session 6 Decision 7) need no special handling here at all: a
rename is represented as a team_aliases row pointing at one continuous
team_id (sessions 4/5/6's _merge_team), so Elo - which only ever reads
matches.team_a/team_b/winner by team_id - sees one unbroken history
automatically. Confirmed empirically: Delhi Capitals' elo_ratings history
starts in 2008 (the Delhi Daredevils era), not 2019.

Usage (from the api/ directory, with api/.env configured):
    python -m features.elo rebuild
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import dotenv_values

# as_of_date_key lives in venue_stats because that's the module that
# introduces the date-key contract. It can't move up into features/as_of.py:
# that module imports both this one and venue_stats, so the edge would cycle.
from features.venue_stats import as_of_date_key

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
REPORTS_DIR = REPO_ROOT / "api" / "data" / "load_reports"

K_FACTOR = 20
STARTING_RATING = 1500.0
FORMATS = ("T20", "ODI")


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def _expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def _result_for_team_a(winner_id: int | None, team_a: int, result_method: str) -> float | None:
    """1.0 win, 0.0 loss, 0.5 tie, None means skip - no_result carries no
    information to update Elo on (spec section 6.1's update rule)."""
    if result_method == "no_result":
        return None
    if result_method == "tie" or winner_id is None:
        return 0.5
    return 1.0 if winner_id == team_a else 0.0


def recompute_format(conn, format_: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM elo_ratings WHERE format = %s", (format_,))
        cur.execute(
            """
            SELECT match_id, team_a, team_b, winner, result_method, start_time
            FROM matches
            WHERE format = %s AND status = 'complete'
            ORDER BY start_time, match_id
            """,
            (format_,),
        )
        matches = cur.fetchall()

        ratings: dict[int, float] = {}
        rows_written = 0
        skipped_no_result = 0

        for match_id, team_a, team_b, winner_id, result_method, start_time in matches:
            rating_a = ratings.get(team_a, STARTING_RATING)
            rating_b = ratings.get(team_b, STARTING_RATING)

            result_a = _result_for_team_a(winner_id, team_a, result_method)
            if result_a is None:
                skipped_no_result += 1
                continue

            expected_a = _expected_score(rating_a, rating_b)
            new_rating_a = rating_a + K_FACTOR * (result_a - expected_a)
            new_rating_b = rating_b + K_FACTOR * ((1 - result_a) - (1 - expected_a))

            cur.execute(
                "INSERT INTO elo_ratings (team_id, format, match_id, as_of, rating) "
                "VALUES (%s, %s, %s, %s, %s)",
                (team_a, format_, match_id, start_time, new_rating_a),
            )
            cur.execute(
                "INSERT INTO elo_ratings (team_id, format, match_id, as_of, rating) "
                "VALUES (%s, %s, %s, %s, %s)",
                (team_b, format_, match_id, start_time, new_rating_b),
            )
            ratings[team_a] = new_rating_a
            ratings[team_b] = new_rating_b
            rows_written += 2

    conn.commit()
    return {
        "format": format_,
        "matches_processed": len(matches),
        "matches_skipped_no_result": skipped_no_result,
        "rows_written": rows_written,
        "distinct_teams": len(ratings),
    }


def elo_as_of(conn, team_id: int, format_: str, as_of_date) -> float:
    """The only sanctioned way any later phase may read a rating - never a
    direct SELECT against a "current" value, because there isn't one.

    Reads elo_asof_summary, not elo_ratings (Phase 2 session 3): elo_ratings
    stays local because its match_id references matches, which never goes to
    Supabase. The summary exists in both databases, so this one function
    serves training and the live worker alike and differs only in which
    connection it receives.

    Returns STARTING_RATING, never None, when a team has no prior rated
    match. That is a different fact from the venue helpers' None: the model
    was trained on 1500.0 for cold-start teams and NaN for cold-start venues.

    The float() is required because elo_asof_summary.rating is NUMERIC and
    psycopg hands back a Decimal - and it changes nothing: the stored decimal
    is float4's shortest exact representation, so float(Decimal('1496.445'))
    is the same float the old REAL-backed text decode produced. See the
    migration for why NUMERIC rather than REAL.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rating FROM elo_asof_summary
            WHERE team_id = %s AND format = %s AND effective_date < %s
            ORDER BY effective_date DESC LIMIT 1
            """,
            (team_id, format_, as_of_date_key(as_of_date)),
        )
        row = cur.fetchone()
    return float(row[0]) if row is not None else STARTING_RATING


def elo_as_of_direct(conn, team_id: int, format_: str, as_of_date) -> float:
    """The elo_ratings-backed implementation, kept as the parity gate's
    oracle and never called on a serving path.

    NOTE the `, match_id DESC` that the pre-session-3 version did not have.
    Every matches.start_time in this corpus is exactly midnight, so a team
    playing twice on one date writes TWO elo_ratings rows with an identical
    as_of, and a bare `ORDER BY as_of DESC LIMIT 1` chose between them by
    heap order. Measured on the real corpus: 372 tied groups, and for 137 of
    them the bare ordering returned the EARLIER match's rating - the
    pre-second-match value rather than the end-of-day one. recompute_format
    processes matches `ORDER BY start_time, match_id`, so the highest
    match_id on a date is that date's last match and its rating is the
    end-of-day rating.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rating FROM elo_ratings
            WHERE team_id = %s AND format = %s AND as_of < %s
            ORDER BY as_of DESC, match_id DESC LIMIT 1
            """,
            (team_id, format_, as_of_date),
        )
        row = cur.fetchone()
    return row[0] if row is not None else STARTING_RATING


def rebuild() -> dict:
    """Recomputes Elo for every format from matches, chronologically.
    Idempotent: a pure function of matches.team_a/team_b/winner/
    result_method/start_time, never hand-edited.
    """
    db_url = _env()["LOCAL_DATABASE_URL"]
    start = time.monotonic()
    by_format = {}
    with psycopg.connect(db_url) as conn:
        for format_ in FORMATS:
            by_format[format_] = recompute_format(conn, format_)

    elapsed = time.monotonic() - start
    report = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        "by_format": by_format,
    }

    print(f"Elo rebuilt in {elapsed:.1f}s")
    for format_, r in by_format.items():
        print(
            f"  {format_}: {r['matches_processed']} matches, {r['rows_written']} rows written, "
            f"{r['matches_skipped_no_result']} skipped (no_result), {r['distinct_teams']} teams"
        )

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"elo_{int(time.time())}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report written to {report_path}")

    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="elo")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("rebuild", help="recompute Elo ratings for all formats from matches")
    args = parser.parse_args(argv)
    if args.command == "rebuild":
        rebuild()


if __name__ == "__main__":
    main()
