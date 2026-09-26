"""Backfill `predictions.batting_team_id` on Supabase from local `deliveries`.

Migration 20260925000001 added the column; every writer fills it from now
on. The rows written before it are NULL, and the only record of who batted
is `deliveries.batting_team_id` in the local corpus - Supabase's copy of that
table is empty by design. So this reads batting order locally and writes it
remotely, one UPDATE per (match, innings).

Never a guess. A (match, innings) is written only when local deliveries name
exactly one batting side for it, and that side is one of the two teams the
SUPABASE match row lists - the same identity check resolve_outcomes makes
before trusting that a match id means the same match on both databases.
Anything else stays NULL, and the UI renders NULL as "batting side".

    python -m ingest.backfill_batting_team --dry-run
    python -m ingest.backfill_batting_team --expect-changed N
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import psycopg
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"


def plan(local_conn, supabase_conn) -> tuple[list[tuple[int, int, int, int]], Counter]:
    """([(match_id, innings, batting_team_id, rows)], skipped reasons by row count)."""
    pending = supabase_conn.execute(
        "SELECT p.match_id, p.innings, count(*), m.team_a, m.team_b "
        "FROM predictions p JOIN matches m USING (match_id) "
        "WHERE p.innings IS NOT NULL AND p.batting_team_id IS NULL "
        "GROUP BY p.match_id, p.innings, m.team_a, m.team_b"
    ).fetchall()
    if not pending:
        return [], Counter()

    batting = {
        (match_id, innings): teams
        for match_id, innings, teams in local_conn.execute(
            "SELECT match_id, innings, array_agg(DISTINCT batting_team_id) "
            "FROM deliveries WHERE match_id = ANY(%s) GROUP BY match_id, innings",
            ([row[0] for row in pending],),
        ).fetchall()
    }

    updates: list[tuple[int, int, int, int]] = []
    skipped: Counter = Counter()
    for match_id, innings, rows, team_a, team_b in pending:
        teams = batting.get((match_id, innings))
        if teams is None:
            skipped["no local deliveries for this innings"] += rows
        elif len(teams) != 1:
            skipped["local deliveries name more than one batting side"] += rows
        elif teams[0] not in (team_a, team_b):
            skipped["batting side is not one of the Supabase match's teams"] += rows
        else:
            updates.append((match_id, innings, teams[0], rows))
    return updates, skipped


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="report and write nothing")
    parser.add_argument(
        "--expect-changed",
        type=int,
        default=None,
        help="refuse unless exactly this many prediction rows would change",
    )
    args = parser.parse_args(argv)

    env = dotenv_values(ENV_PATH)
    for key in ("LOCAL_DATABASE_URL", "SUPABASE_SESSION_POOLER_URL"):
        if not env.get(key):
            sys.exit(f"{key} must be set in {ENV_PATH}")

    with (
        psycopg.connect(env["LOCAL_DATABASE_URL"], connect_timeout=30) as local_conn,
        psycopg.connect(env["SUPABASE_SESSION_POOLER_URL"], connect_timeout=30) as supabase_conn,
    ):
        updates, skipped = plan(local_conn, supabase_conn)
        changing = sum(rows for *_key, rows in updates)
        print(
            f"{changing} prediction row(s) across {len({u[0] for u in updates})} match(es) "
            f"would get a batting team"
        )
        for reason, rows in skipped.items():
            print(f"  left NULL ({reason}): {rows} row(s)")

        if args.expect_changed is not None and changing != args.expect_changed:
            sys.exit(f"expected {args.expect_changed} row(s) to change, found {changing}; refusing")
        if args.dry_run:
            print("dry run: nothing written")
            return

        with supabase_conn.cursor() as cur:
            for match_id, innings, team_id, _rows in updates:
                cur.execute(
                    "UPDATE predictions SET batting_team_id = %s "
                    "WHERE match_id = %s AND innings = %s AND batting_team_id IS NULL",
                    (team_id, match_id, innings),
                )
        supabase_conn.commit()
        print(f"wrote {changing} row(s)")


if __name__ == "__main__":
    main()
