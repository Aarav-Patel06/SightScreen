"""Find humans split across two player_ids, before §6.5 aggregates them.

**"One player_id per registry id" is not "one player_id per person."** The
loader creates one player per Cricsheet registry id, which is correct and is
the guarantee it can actually offer. Cricsheet sometimes issues TWO registry
ids for one human, and then the loader faithfully creates two players. Found
2026-09-21: `A Davidson Soler` (524943f1) and `A Davidson-Soler` (71d6fd2c).

Today that is harmless, because one side holds 269 deliveries and the other
holds 0. It stops being harmless in Phase 5.

§6.5's Kalman update reads its own prior per player and writes back the
posterior, keyed on player_id. A split human therefore gets **two
half-histories and two wrong posteriors**, and the failure is invisible in
the output: fewer innings per side widens `sd`, and §6.5 explicitly ships
`sd` to the UI as "a genuine confidence band". So the artifact of the split
renders as a legitimate-looking uncertainty estimate rather than as an error.
Nothing downstream can tell the difference.

That is why this is a script and not a note. Cricsheet refreshes, so the
answer is not durable - a clean scan today says nothing about a scan after
the next download. **Run it before Phase 5 starts.**

    python -m ingest.check_player_identity            report
    python -m ingest.check_player_identity --strict   exit 1 if any split is active

`--strict` is what a Phase 5 precondition check would call. It fails only on
groups where MORE THAN ONE side has deliveries, because that is the
population that can corrupt an ability estimate; an inert twin with zero
deliveries contributes nothing to any aggregate and merging it would be
churn without a reason.

This script does NOT merge anything. Merging two registry ids asserts that
two humans are one, which is a claim about the world that wants a person to
make it - `_merge_team` exists for teams and is called from a hand-curated
list for exactly that reason.
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys
import unicodedata
from pathlib import Path

import psycopg
from dotenv import dotenv_values

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _local_database_url() -> str | None:
    """Environment first, then api/.env - the same precedence
    tests/conftest.py uses, and pydantic-settings' own.

    Deliberately NOT `from config import settings`: that triggers full
    Settings validation, so a script needing one connection string fails on
    SUPABASE_URL being absent, and it resolves its env file relative to the
    CWD so the same command works from api/ and not from api/src/.
    """
    return os.environ.get("LOCAL_DATABASE_URL") or dotenv_values(_ENV_PATH).get(
        "LOCAL_DATABASE_URL"
    )


# Appearances per player, in one pass. The obvious version - a correlated
# subquery per player - is 18,468 scans of a 3.78M-row table and took over
# five minutes before being killed; this takes 1.5 seconds. Worth recording
# because the slow version looks perfectly reasonable when you write it.
_ACTIVITY_SQL = """
WITH appearances AS (
              SELECT batter_id      AS pid FROM deliveries WHERE batter_id      IS NOT NULL
    UNION ALL SELECT bowler_id      AS pid FROM deliveries WHERE bowler_id      IS NOT NULL
    UNION ALL SELECT non_striker_id AS pid FROM deliveries WHERE non_striker_id IS NOT NULL
)
SELECT pid, count(*) FROM appearances GROUP BY pid
"""


def fold_name(name: str | None) -> str:
    """Collapse the differences that should never have made two people.

    Accents, smart quotes, dashes, punctuation, case and runs of whitespace.
    This is deliberately MORE aggressive than the ingest-side competition
    fold: there the output is stored, so folding accents would rewrite
    correct data, while here the output is only a grouping key and never
    written anywhere.
    """
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = (
        text.replace("’", "'")
        .replace("‘", "'")
        .replace("–", "-")
        .replace("—", "-")
    )
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def scan(conn) -> list[list[tuple[int, str, int, str]]]:
    """Groups of players whose folded names collide. Sorted, active first."""
    activity = dict(conn.execute(_ACTIVITY_SQL).fetchall())
    registry: dict[int, list[str]] = collections.defaultdict(list)
    for pid, source_id in conn.execute(
        "SELECT player_id, source_id FROM player_aliases WHERE source = 'cricsheet'"
    ).fetchall():
        registry[pid].append(source_id or "-")

    groups: dict[str, list[tuple[int, str, int, str]]] = collections.defaultdict(list)
    for pid, name in conn.execute("SELECT player_id, canonical_name FROM players").fetchall():
        groups[fold_name(name)].append(
            (pid, name, activity.get(pid, 0), ",".join(registry.get(pid, [])) or "-")
        )
    return [
        sorted(members, key=lambda m: -m[2])
        for members in groups.values()
        if len(members) > 1
    ]


def active_splits(groups) -> list:
    """Groups where more than one side has deliveries - the harmful kind."""
    return [g for g in groups if sum(1 for m in g if m[2] > 0) > 1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=None)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if any group has more than one side with deliveries",
    )
    args = parser.parse_args(argv)

    url = args.db_url or _local_database_url()
    if not url:
        raise SystemExit(
            f"no database URL: pass --db-url, export LOCAL_DATABASE_URL, or put it in {_ENV_PATH}"
        )

    with psycopg.connect(url) as conn:
        groups = scan(conn)
    harmful = active_splits(groups)

    print(f"folded near-duplicate groups:             {len(groups)}")
    print(f"  with >1 side holding deliveries:        {len(harmful)}")
    for group in groups:
        print()
        for pid, name, n, source in group:
            mark = "  " if n == 0 else "* "
            print(f"  {mark}id={pid:<6} deliveries={n:<8,} registry={source:<12} {name}")

    if not groups:
        print("\nno near-duplicates.")
    elif not harmful:
        print(
            "\nNo group has two active sides, so no ability estimate can be split "
            "today. Not durable - re-run after any Cricsheet refresh."
        )
    else:
        print(
            f"\n{len(harmful)} group(s) would split a §6.5 ability posterior into two "
            "half-histories, and the widened sd would render as a confidence band."
        )
    return 1 if (args.strict and harmful) else 0


if __name__ == "__main__":
    sys.exit(main())
