"""Backfill the result margin and method (migration 20260927000001).

    local      every corpus match, from its raw JSON: the file on disk
               (CRICSHEET_DATA_DIR), else the daily job's raw store, else -
               once, for whatever is left - Cricsheet's full archive, whose
               files are then kept on disk and in the store so this never
               happens again.
    supabase   copy the columns to Supabase's rows through the replay mirror
               (`mirror_match_rows`), which refuses live-worker rows and can
               be held to an expected change count.

Usage (from api/src):
    python -m ingest.backfill_outcomes local
    python -m ingest.backfill_outcomes supabase --dry-run
    python -m ingest.backfill_outcomes supabase --expect-changed N
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import psycopg

from db.defaults import SUPABASE_ID_FLOOR
from db.env import require_env
from ingest.cricsheet import ARCHIVE_URL, OUTCOME_DETAIL_COLUMNS, outcome_detail
from ingest.daily_cricsheet import corpus_data_dir, fetch_bundle, log
from ingest.raw_store import RawStore
from ingest.replay_log import mirror_match_rows


def _raw_files(conn, store: RawStore) -> dict[str, bytes]:
    data_dir = corpus_data_dir()
    with conn.cursor() as cur:
        cur.execute("SELECT external_ids->>'cricsheet', match_id FROM matches WHERE external_ids ? 'cricsheet'")
        wanted = dict(cur.fetchall())
    found, from_store, missing = {}, 0, []
    for cid in wanted:
        path = data_dir / f"{cid}.json"
        if path.exists():
            found[cid] = path.read_bytes()
        elif (raw := store.get(cid)) is not None:
            found[cid], from_store = raw, from_store + 1
            path.write_bytes(raw)
        else:
            missing.append(cid)
    log(f"raw JSON: {len(found) - from_store} on disk, {from_store} from the store, {len(missing)} missing")
    if missing:
        with tempfile.TemporaryDirectory() as tmp:
            by_id = {p.stem: p for p in fetch_bundle(ARCHIVE_URL, Path(tmp))}
            for cid in missing:
                if cid not in by_id:
                    continue
                raw = by_id[cid].read_bytes()
                found[cid] = raw
                (data_dir / f"{cid}.json").write_bytes(raw)
                # A match the daily job ingested belongs in its store too.
                if wanted[cid] >= SUPABASE_ID_FLOOR:
                    store.put(cid, raw)
        still = [cid for cid in missing if cid not in found]
        if still:
            sys.exit(f"{len(still)} corpus matches have no raw JSON anywhere, e.g. {still[:5]}")
    return found


def backfill_local(local_url: str) -> int:
    store = RawStore()
    with psycopg.connect(local_url) as conn:
        raw = _raw_files(conn, store)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT external_ids->>'cricsheet', match_id, team_a, team_b FROM matches "
                "WHERE external_ids ? 'cricsheet'"
            )
            matches = cur.fetchall()
        set_clause = ", ".join(f"{c} = %s" for c in OUTCOME_DETAIL_COLUMNS)
        changed_test = " OR ".join(f"{c} IS DISTINCT FROM %s" for c in OUTCOME_DETAIL_COLUMNS)
        changed = 0
        with conn.cursor() as cur:
            for cid, match_id, team_a, team_b in matches:
                info = json.loads(raw[cid])["info"]
                # The loader stored teams[0] as team_a and teams[1] as team_b.
                team_ids = dict(zip(info["teams"], (team_a, team_b)))
                values = tuple(outcome_detail(info, team_ids).values())
                cur.execute(
                    f"UPDATE matches SET {set_clause} WHERE match_id = %s AND ({changed_test})",
                    (*values, match_id, *values),
                )
                changed += cur.rowcount
        conn.commit()
    log(f"local: {changed} of {len(matches)} matches updated")
    return changed


def backfill_supabase(local_url: str, supabase_url: str, dry_run: bool, expect_changed: int | None) -> int:
    with psycopg.connect(local_url) as local, psycopg.connect(supabase_url) as supabase:
        with local.cursor() as cur:
            cur.execute("SELECT match_id FROM matches")
            corpus_ids = {r[0] for r in cur.fetchall()}
        with supabase.cursor() as cur:
            # Live-worker rows are the worker's; the mirror would refuse them
            # anyway, and their ids mean different matches in the corpus.
            cur.execute("SELECT match_id FROM matches WHERE NOT external_ids ? 'cricketdata'")
            ids = sorted(r[0] for r in cur.fetchall() if r[0] in corpus_ids)
        log(f"supabase: {len(ids)} corpus-backed rows")
        return mirror_match_rows(local, supabase, ids, dry_run=dry_run, expect_changed=expect_changed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backfill_outcomes")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("local")
    remote = sub.add_parser("supabase")
    remote.add_argument("--dry-run", action="store_true")
    remote.add_argument("--expect-changed", type=int)
    args = parser.parse_args(argv)
    local_url = require_env("LOCAL_DATABASE_URL")
    if args.command == "local":
        backfill_local(local_url)
    else:
        backfill_supabase(local_url, require_env("SUPABASE_SESSION_POOLER_URL"), args.dry_run, args.expect_changed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
