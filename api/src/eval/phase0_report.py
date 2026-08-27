"""Phase 0 acceptance report (SPEC.md section 11).

Prints every acceptance figure in one run - the single command to point at
as proof, rather than five separate manual queries. A few criteria are test
results, not DB-queryable numbers (listed at the bottom of the output).

Usage (from the api/ directory, with api/.env configured):
    python -m eval.phase0_report
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

DELIVERIES_TARGET = 1_000_000
UNRESOLVED_PLAYER_TARGET = 50


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def main() -> None:
    conn = psycopg.connect(_env()["LOCAL_DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM deliveries")
        deliveries_count = cur.fetchone()[0]

        cur.execute(
            "SELECT count(*) FROM unresolved_entities "
            "WHERE entity_kind='player' AND status='pending' AND source_id IS NOT NULL"
        )
        players_with_registry_id_queued = cur.fetchone()[0]

        cur.execute(
            "SELECT count(*) FROM unresolved_entities "
            "WHERE entity_kind='player' AND status='pending' AND source_id IS NULL"
        )
        players_without_registry_id_queued = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM unresolved_entities WHERE entity_kind='team' AND status='pending'")
        teams_queued = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM unresolved_entities WHERE entity_kind='venue' AND status='pending'")
        venues_queued = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM matches")
        matches_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM match_states")
        match_states_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM elo_ratings")
        elo_count = cur.fetchone()[0]
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        db_size = cur.fetchone()[0]

    print("=== SPEC.md section 11 - Phase 0 acceptance ===\n")

    status = "PASS" if deliveries_count > DELIVERIES_TARGET else "FAIL"
    print(f"[{status}] deliveries: {deliveries_count:,} (target: > {DELIVERIES_TARGET:,})")

    status = "PASS" if players_with_registry_id_queued < UNRESOLVED_PLAYER_TARGET else "FAIL"
    print(
        f"[{status}] players with a registry ID, still queued: {players_with_registry_id_queued} "
        f"(target: < {UNRESOLVED_PLAYER_TARGET} - an exact registry match is authoritative, "
        "no downstream heuristic may override it)"
    )
    print(
        f"[INFO] players WITHOUT a registry ID, queued: {players_without_registry_id_queued} "
        "(reported, not a failure - fuzzy matching is the fallback path for a minority of the "
        "corpus, not the main road)"
    )
    print(
        f"[INFO] teams queued: {teams_queued}, venues queued: {venues_queued} "
        "(no Cricsheet registry exists for either kind - not subject to the registry-specific target)"
    )

    print()
    print(f"[INFO] matches: {matches_count:,}  match_states: {match_states_count:,}  elo_ratings: {elo_count:,}")
    print(f"[INFO] local training database size: {db_size}")

    print()
    print("Test-based criteria (run separately, not DB-queryable numbers):")
    print("  pytest tests/ingest/test_entity_resolution.py -v      # zero wrong merges, golden set incl. near-collisions")
    print("  pytest tests/ingest/test_cricsheet_exact_match.py -v  # 5-match spot-check against Cricsheet source")
    print("  pytest tests -q                                       # full suite, incl. match_states/Elo idempotency")
    print("  CI status: check the latest run on the default branch (github.com/.../actions)")


if __name__ == "__main__":
    main()
