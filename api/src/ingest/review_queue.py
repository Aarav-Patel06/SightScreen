"""PowerShell-runnable CLI to review the unresolved_entities queue (SPEC.md
section 4.4) and record decisions. Every decision here writes a real alias
row, so the exact same name/registry-ID is never asked about again - see
entity_resolution.py's module docstring for why that's guaranteed.

Usage (from the api/ directory, with api/.env configured):
    python -m ingest.review_queue list
    python -m ingest.review_queue list --kind player --status pending
    python -m ingest.review_queue resolve 17 --as-existing 42
    python -m ingest.review_queue resolve 18 --as-new
    python -m ingest.review_queue resolve 19 --ignore
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from ingest.entity_resolution import (
    PLAYER_CONFIG,
    TEAM_CONFIG,
    VENUE_CONFIG,
    _create_alias,
    _create_new_entity_and_alias,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

_CONFIG_BY_KIND = {"player": PLAYER_CONFIG, "team": TEAM_CONFIG, "venue": VENUE_CONFIG}


def _connect():
    env = dotenv_values(ENV_PATH)
    db_url = env.get("LOCAL_DATABASE_URL")
    if not db_url:
        sys.exit(f"LOCAL_DATABASE_URL not set in {ENV_PATH}")
    return psycopg.connect(db_url, autocommit=True)


def _parse_candidates(value) -> list[dict]:
    # psycopg normally adapts jsonb to a Python list already; tolerate a
    # raw string too rather than assuming one or the other.
    return json.loads(value) if isinstance(value, str) else value


def list_queue(conn, kind: str | None = None, status: str = "pending") -> None:
    query = (
        "SELECT unresolved_id, entity_kind, source_name, reason, candidates, first_seen_match_id "
        "FROM unresolved_entities WHERE status = %s"
    )
    params: list[object] = [status]
    if kind:
        query += " AND entity_kind = %s"
        params.append(kind)
    query += " ORDER BY unresolved_id"

    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    if not rows:
        print("(queue is empty)")
        return

    for unresolved_id, entity_kind, source_name, reason, candidates, first_seen_match_id in rows:
        print(
            f"[{unresolved_id}] {entity_kind}: {source_name!r}  reason={reason}  "
            f"first_seen_match={first_seen_match_id}"
        )
        for candidate in _parse_candidates(candidates):
            print(
                f"    candidate {candidate['candidate_id']}: "
                f"{candidate['candidate_name']!r} score={candidate['score']:.1f}"
            )


def _fetch_unresolved(conn, unresolved_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT entity_kind, source, source_name, source_id FROM unresolved_entities "
            "WHERE unresolved_id = %s",
            (unresolved_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"no unresolved_entities row with id {unresolved_id}")
    entity_kind, source, source_name, source_id = row
    return {"entity_kind": entity_kind, "source": source, "source_name": source_name, "source_id": source_id}


def _mark_status(conn, unresolved_id: int, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE unresolved_entities SET status = %s WHERE unresolved_id = %s",
            (status, unresolved_id),
        )


def resolve_as_existing(conn, unresolved_id: int, entity_id: int) -> None:
    entry = _fetch_unresolved(conn, unresolved_id)
    config = _CONFIG_BY_KIND[entry["entity_kind"]]
    _create_alias(conn, config, entry["source"], entry["source_name"], entry["source_id"], entity_id)
    _mark_status(conn, unresolved_id, "resolved")


def resolve_as_new(conn, unresolved_id: int) -> int:
    entry = _fetch_unresolved(conn, unresolved_id)
    config = _CONFIG_BY_KIND[entry["entity_kind"]]
    new_id = _create_new_entity_and_alias(
        conn, config, entry["source"], entry["source_name"], entry["source_id"]
    )
    _mark_status(conn, unresolved_id, "resolved")
    return new_id


def ignore(conn, unresolved_id: int) -> None:
    _mark_status(conn, unresolved_id, "ignored")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="review_queue")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="show queued entities")
    list_parser.add_argument("--kind", choices=["player", "team", "venue"])
    list_parser.add_argument("--status", default="pending")

    resolve_parser = subparsers.add_parser("resolve", help="record a decision for one entry")
    resolve_parser.add_argument("unresolved_id", type=int)
    action = resolve_parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--as-existing", type=int, metavar="ENTITY_ID")
    action.add_argument("--as-new", action="store_true")
    action.add_argument("--ignore", action="store_true")

    args = parser.parse_args(argv)
    conn = _connect()
    try:
        if args.command == "list":
            list_queue(conn, kind=args.kind, status=args.status)
        elif args.command == "resolve":
            if args.as_existing is not None:
                resolve_as_existing(conn, args.unresolved_id, args.as_existing)
                print(f"[{args.unresolved_id}] resolved as existing entity {args.as_existing}")
            elif args.as_new:
                new_id = resolve_as_new(conn, args.unresolved_id)
                print(f"[{args.unresolved_id}] resolved as new entity {new_id}")
            elif args.ignore:
                ignore(conn, args.unresolved_id)
                print(f"[{args.unresolved_id}] marked ignored")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
