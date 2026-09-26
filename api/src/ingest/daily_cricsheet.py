"""Daily Cricsheet ingest: every newly released men's T20/ODI match, scored
by the served model and logged as the BACKFILL cohort.

Runs in GitHub Actions (.github/workflows/cricsheet-daily.yml), where the
local corpus does not exist. Everything it needs comes from Supabase plus a
throwaway Postgres on the runner (`--stage-url`, empty, migrations applied):

  1. Download the bundle; keep in-scope files not already done.
  2. Copy the reference tables (teams, venues, players, aliases) from
     Supabase into the stage database.
  3. Load each file with the UNCHANGED `ingest.cricsheet.load_match` - the
     same parser, format filter and entity resolution that built the corpus.
     A first pass decides eligibility; the eligible matches are then loaded
     again under their real ids (see "Ids" below).
  4. match_states for the new matches, with the same REBUILD_SQL.
  5. The as-of summaries, rebuilt from feature_ledger (every corpus match's
     inputs plus the new ones) with the unchanged Elo and summary code -
     features/feature_ledger.py.
  6. Score with `ingest.replay_log.score_match`, write with
     `insert_predictions` (source defaults to 'backfill'), resolve outcomes
     with `models.resolve_outcomes.resolve_with`, then the ledger rows, then
     the summaries through `sync_reference_tables.sync_derived_table`, which
     hash-verifies on Supabase before recording them.

SKIPPED, AND LOGGED, NEVER WRITTEN:
  * live      - the live worker already has this match (same format, same
                team pair, start date within a day). Its predictions are live
                and must not be touched; merging the two sources is later work.
  * new_team / new_venue - the loader had to create the entity. Creating it
                on Supabase would let Supabase and the corpus mint the same id
                for different things. The local catch-up creates it; a later
                daily run then picks the match up.
  * rejected  - the loader rejected the file (its own reasons, verbatim).
  * no balls  - not a skip: the match row and ledger row are written (Elo and
                venue need a no-result too), there is just nothing to score.

IDS. A corpus match keeps its corpus id. A new match takes the next id from
Supabase's `matches` sequence (>= 1,000,000, shared with the live worker),
keyed unique on external_ids->>'cricsheet'; the local catch-up adopts it.

DONE means a Supabase `matches` row AND a feature_ledger row for the
cricsheet id. The ledger row is written last, so a run that dies part way is
finished by the next one: every earlier write is ON CONFLICT DO NOTHING.

Usage (from api/src):
    python -m ingest.daily_cricsheet run --stage-url URL [--bundle all --since 2026-08-24]
    python -m ingest.daily_cricsheet catchup-local [--bundle all]
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg

from db.env import env_value, require_env
from eval.splits import TEST_SPLIT_START, assert_in_test_split
from features.asof_summary import DERIVED_TABLES, content_hash
from features.feature_ledger import (
    LEDGER_COLUMNS,
    fetch_ledger,
    insert_ledger_rows,
    ledger_rows,
    rebuild_summaries_from_ledger,
)
from features.match_state import rebuild_match_states
from ingest.cricsheet import ARCHIVE_URL, TARGET_FORMATS, TARGET_GENDER, LoadReport, load_match
from ingest.replay_log import (
    CACHE_DIR,
    _MIRRORED_COLUMNS,
    confirm_model_version,
    insert_predictions,
    load_balls,
    score_match,
)
from ingest.sync_reference_tables import TABLES as REFERENCE_TABLES
from ingest.sync_reference_tables import sync_derived_table
from models.artifact import active_model_row, resolve_pinned_artifact
from models.resolve_outcomes import resolve_with

# Verified against real downloads on 2026-09-25: the 2- and 7-day bundles
# were byte-identical, because the windows count back from Cricsheet's last
# release, not from today, and releases are irregular. The 30-day bundle
# (1.9MB) is the only one that tolerates a quiet fortnight plus missed runs;
# the overlap costs nothing, since a done match is skipped.
BUNDLES = {
    "recently_added_30": "https://cricsheet.org/downloads/recently_added_30_json.zip",
    "all": ARCHIVE_URL,
}

# The corpus's last match date when this pipeline started. A ledger row dated
# on or before it is a corpus match that was never meant to be predicted
# here; one dated after it with no Supabase row is a match an earlier run
# skipped (new team/venue) and the local catch-up has since loaded.
CORPUS_BASELINE_END = date(2026, 8, 24)

# Pass-1 ids in the stage database live far above any real id, so a pass-1
# row can never be mistaken for, or collide with, the real-id pass 2.
STAGE_PASS1_ID_FLOOR = 1_900_000_000

LIVE_DATE_WINDOW_DAYS = 1

_LIVE_QUERY = """
    SELECT match_id FROM matches
    WHERE external_ids ? 'cricketdata'
      AND format = %(format)s
      AND LEAST(team_a, team_b) = LEAST(%(a)s::int, %(b)s::int)
      AND GREATEST(team_a, team_b) = GREATEST(%(a)s::int, %(b)s::int)
      AND abs((start_time AT TIME ZONE 'UTC')::date - %(day)s::date) <= %(window)s
"""


def log(message: str) -> None:
    print(message, flush=True)


# --- Source ------------------------------------------------------------------


def fetch_bundle(url: str, dest: Path) -> list[Path]:
    """Download and extract. Any failure raises - an unreachable Cricsheet
    must turn the run red, not look like a quiet day."""
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / "bundle.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "SightScreen daily ingest"})
    with urllib.request.urlopen(request, timeout=120) as response, open(archive, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if n.endswith(".json")]
        zf.extractall(dest, members=names)
    log(f"bundle {url}: {archive.stat().st_size:,} bytes, {len(names)} match files")
    return sorted(dest / n for n in names)


def peek(path: Path) -> dict:
    """The few fields needed to SELECT files. Parsing proper is load_match's."""
    info = json.loads(path.read_text(encoding="utf-8"))["info"]
    return {
        "cricsheet_id": path.stem,
        "match_type": info.get("match_type"),
        "gender": info.get("gender"),
        "date": date.fromisoformat(info["dates"][0]),
    }


def in_scope(meta: dict) -> bool:
    # The corpus loader's own filter constants - not a second copy of them.
    return meta["match_type"] in TARGET_FORMATS and meta["gender"] == TARGET_GENDER


# --- Supabase state ----------------------------------------------------------


def supabase_state(supabase_conn) -> dict:
    ledger = fetch_ledger(supabase_conn)
    index = {c: i for i, c in enumerate(LEDGER_COLUMNS)}
    with supabase_conn.cursor() as cur:
        cur.execute(
            "SELECT external_ids->>'cricsheet', match_id FROM matches WHERE external_ids ? 'cricsheet'"
        )
        match_rows = dict(cur.fetchall())
    return {
        "ledger": ledger,
        "ledger_by_cid": {r[index["cricsheet_id"]]: r for r in ledger},
        "match_id_by_cid": match_rows,
    }


def select_candidates(paths: list[Path], state: dict, since: date | None) -> tuple[list[dict], Counter]:
    index = {c: i for i, c in enumerate(LEDGER_COLUMNS)}
    floor = max(TEST_SPLIT_START, since) if since else TEST_SPLIT_START
    candidates, tally = [], Counter()
    for path in paths:
        meta = peek(path)
        if not in_scope(meta):
            tally["out_of_scope"] += 1
            continue
        if meta["date"] < floor:
            tally["before_window"] += 1
            continue
        cid = meta["cricsheet_id"]
        ledger_row = state["ledger_by_cid"].get(cid)
        if ledger_row is not None and cid in state["match_id_by_cid"]:
            tally["already_done"] += 1
            continue
        if ledger_row is not None and ledger_row[index["start_time"]].date() <= CORPUS_BASELINE_END:
            tally["in_corpus"] += 1
            continue
        candidates.append({**meta, "path": path})
    candidates.sort(key=lambda c: (c["date"], c["cricsheet_id"]))
    return candidates, tally


# --- Stage database ------------------------------------------------------------


def assert_stage_empty(stage_conn) -> None:
    """The stage database is truncated and reloaded by this job. Refuse
    anything that already holds data - above all, the real corpus."""
    with stage_conn.cursor() as cur:
        cur.execute("SELECT (SELECT count(*) FROM matches) + (SELECT count(*) FROM teams)")
        if cur.fetchone()[0]:
            raise RuntimeError(
                "refusing: the stage database is not empty. It must be a fresh, "
                "migrated throwaway database - never the corpus."
            )


def copy_reference_tables(supabase_conn, stage_conn) -> dict[str, int]:
    counts = {}
    for table, id_column, columns in REFERENCE_TABLES:
        column_list = ", ".join(columns)
        with supabase_conn.cursor() as src:
            src.execute(f"SELECT {column_list} FROM {table} ORDER BY {id_column}")
            rows = src.fetchall()
        with stage_conn.cursor() as cur:
            with cur.copy(f"COPY {table} ({column_list}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{id_column}'), "
                f"GREATEST((SELECT MAX({id_column}) FROM {table}), 1))"
            )
        counts[table] = len(rows)
    with stage_conn.cursor() as cur:
        cur.execute(
            "SELECT setval(pg_get_serial_sequence('matches', 'match_id'), %s)", (STAGE_PASS1_ID_FLOOR,)
        )
    stage_conn.commit()
    return counts


def _max_id(conn, table: str, column: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT coalesce(max({column}), 0) FROM {table}")
        return cur.fetchone()[0]


def _staged_row(stage_conn, cricsheet_id: str) -> dict | None:
    with stage_conn.cursor() as cur:
        cur.execute(
            "SELECT match_id, format, team_a, team_b, venue_id, start_time FROM matches "
            "WHERE external_ids->>'cricsheet' = %s",
            (cricsheet_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return dict(zip(("match_id", "format", "team_a", "team_b", "venue_id", "start_time"), row))


def _drop_staged(stage_conn, match_ids: list[int]) -> None:
    with stage_conn.cursor() as cur:
        cur.execute("DELETE FROM deliveries WHERE match_id = ANY(%s)", (match_ids,))
        cur.execute("DELETE FROM matches WHERE match_id = ANY(%s)", (match_ids,))
    stage_conn.commit()


def live_duplicate(supabase_conn, staged: dict) -> int | None:
    with supabase_conn.cursor() as cur:
        cur.execute(
            _LIVE_QUERY,
            {
                "format": staged["format"],
                "a": staged["team_a"],
                "b": staged["team_b"],
                "day": staged["start_time"].date(),
                "window": LIVE_DATE_WINDOW_DAYS,
            },
        )
        row = cur.fetchone()
    return row[0] if row else None


def _next_supabase_id(supabase_conn) -> int:
    with supabase_conn.cursor() as cur:
        cur.execute("SELECT nextval(pg_get_serial_sequence('matches', 'match_id'))")
        return cur.fetchone()[0]


def _write_match_row(stage_conn, supabase_conn, match_id: int) -> int:
    """The corpus loader's row, as the replay mirror copies it, plus the
    cricsheet key the mirror never sets. DO NOTHING on either key."""
    columns = ("external_ids", *_MIRRORED_COLUMNS)
    with stage_conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(columns)} FROM matches WHERE match_id = %s", (match_id,))
        row = list(cur.fetchone())
    row[0] = json.dumps(row[0])
    with supabase_conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO matches ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) "
            f"ON CONFLICT ((external_ids->>'cricsheet')) WHERE external_ids->>'cricsheet' IS NOT NULL "
            f"DO NOTHING",
            row,
        )
        return cur.rowcount


# --- The run -------------------------------------------------------------------


def run_daily(
    stage_url: str,
    supabase_conn,
    paths: list[Path],
    artifact: dict,
    model_version: str,
    since: date | None = None,
) -> dict:
    started = time.monotonic()
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "skipped": []}
    state = supabase_state(supabase_conn)
    candidates, tally = select_candidates(paths, state, since)
    report["selection"] = dict(tally, candidates=len(candidates))
    log(f"selection: {report['selection']}")

    with (
        psycopg.connect(stage_url) as stage,
        psycopg.connect(stage_url, autocommit=True) as catalog,
    ):
        assert_stage_empty(stage)
        report["reference_rows"] = copy_reference_tables(supabase_conn, stage)
        max_team, max_venue = _max_id(stage, "teams", "team_id"), _max_id(stage, "venues", "venue_id")

        # Pass 1: load everything once to learn what it resolves to.
        load_report = LoadReport()
        eligible, pass1_ids = [], []
        for cand in candidates:
            load_match(stage, catalog, load_report, cand["path"])
            staged = _staged_row(stage, cand["cricsheet_id"])
            if staged is None:
                reason = next(
                    (r["reason"] for r in load_report.rejections if r["file"] == cand["cricsheet_id"]),
                    "not loaded",
                )
                report["skipped"].append({"cricsheet_id": cand["cricsheet_id"], "why": "rejected", "detail": reason})
                continue
            pass1_ids.append(staged["match_id"])
            why = None
            if staged["team_a"] > max_team or staged["team_b"] > max_team:
                why = "new_team"
            elif staged["venue_id"] is not None and staged["venue_id"] > max_venue:
                why = "new_venue"
            elif (live_id := live_duplicate(supabase_conn, staged)) is not None:
                why, cand["live_match_id"] = "live", live_id
            if why:
                report["skipped"].append(
                    {"cricsheet_id": cand["cricsheet_id"], "why": why,
                     **({"live_match_id": cand["live_match_id"]} if why == "live" else {})}
                )
                continue
            eligible.append(cand)
        if pass1_ids:
            _drop_staged(stage, pass1_ids)
        log(f"skipped: {dict(Counter(s['why'] for s in report['skipped']))}")

        # Ids, in (date, cricsheet id) order so Elo's same-day tie-break is
        # reproducible by the catch-up.
        index = {c: i for i, c in enumerate(LEDGER_COLUMNS)}
        for cand in eligible:
            cid = cand["cricsheet_id"]
            if cid in state["match_id_by_cid"]:
                cand["match_id"] = state["match_id_by_cid"][cid]
            elif cid in state["ledger_by_cid"]:
                cand["match_id"] = state["ledger_by_cid"][cid][index["match_id"]]
            else:
                cand["match_id"] = _next_supabase_id(supabase_conn)

        # Pass 2: the real load, under the real ids.
        load_report = LoadReport()
        for cand in eligible:
            load_match(stage, catalog, load_report, cand["path"], match_id=cand["match_id"])
            if _staged_row(stage, cand["cricsheet_id"]) is None:
                raise RuntimeError(f"{cand['cricsheet_id']} loaded in pass 1 but not pass 2: {load_report.rejections}")
        rebuild_match_states(stage)
        stage.commit()

        ids = [c["match_id"] for c in eligible]
        new_ledger = ledger_rows(stage, ids) if ids else []
        all_rows = [r for r in state["ledger"] if r[index["match_id"]] not in set(ids)] + new_ledger
        rebuild_summaries_from_ledger(stage, all_rows)

        # Score and write.
        by_format, rows_written, scored_ids = Counter(), 0, []
        matches_written = 0
        for cand in eligible:
            match_id = cand["match_id"]
            balls = load_balls(stage, match_id)
            confirm_model_version(supabase_conn, model_version)
            matches_written += _write_match_row(stage, supabase_conn, match_id)
            if balls:
                assert_in_test_split(match_id, balls[0]["match_date"])
                scored = score_match(artifact, stage, match_id, balls)
                rows_written += insert_predictions(supabase_conn, match_id, model_version, scored)
                scored_ids.append(match_id)
            else:
                supabase_conn.commit()
            by_format[cand["match_type"]] += 1
        log(f"scored: {dict(by_format)}, {rows_written} prediction rows")
        if scored_ids:
            with contextlib.redirect_stdout(io.StringIO()):
                resolve_with(stage, supabase_conn, scored_ids)
        ledger_written = insert_ledger_rows(supabase_conn, new_ledger)
        supabase_conn.commit()

        summaries_pushed = {}
        for table in DERIVED_TABLES:
            digest, _count = content_hash(stage, table)
            with supabase_conn.cursor() as cur:
                cur.execute("SELECT content_hash FROM reference_sync_state WHERE table_name = %s", (table.name,))
                remote = cur.fetchone()
            if remote is not None and remote[0] == digest:
                summaries_pushed[table.name] = 0
                continue
            summaries_pushed[table.name] = sync_derived_table(stage, supabase_conn, table)

    report.update(
        ingested_by_format=dict(by_format),
        matches_written=matches_written,
        predictions_written=rows_written,
        ledger_rows_written=ledger_written,
        summary_rows_pushed=summaries_pushed,
        elapsed_seconds=round(time.monotonic() - started, 1),
        skipped_by_reason=dict(Counter(s["why"] for s in report["skipped"])),
    )
    return report


# --- Local catch-up --------------------------------------------------------------


def catchup_local(local_url: str, supabase_conn, paths: list[Path], since: date | None) -> dict:
    """Load released matches into the LOCAL corpus for future retraining,
    adopting the id Supabase already gave a match so the two sides agree.
    Loads every in-scope file the corpus lacks (skipped ones included - this
    is where their new team or venue gets created). Does not rebuild or
    retrain; prints the commands that come next."""
    state = supabase_state(supabase_conn)
    index = {c: i for i, c in enumerate(LEDGER_COLUMNS)}
    report = LoadReport()
    with psycopg.connect(local_url) as bulk, psycopg.connect(local_url, autocommit=True) as catalog:
        with bulk.cursor() as cur:
            cur.execute("SELECT external_ids->>'cricsheet' FROM matches WHERE external_ids ? 'cricsheet'")
            have = {r[0] for r in cur.fetchall()}
        bulk.commit()
        todo = []
        for path in paths:
            meta = peek(path)
            if in_scope(meta) and path.stem not in have and (since is None or meta["date"] >= since):
                todo.append((meta["date"], path.stem, path))
        for _date, cid, path in sorted(todo):
            match_id = state["match_id_by_cid"].get(cid)
            if match_id is None and cid in state["ledger_by_cid"]:
                match_id = state["ledger_by_cid"][cid][index["match_id"]]
            load_match(bulk, catalog, report, path, match_id=match_id)
    log(f"catch-up: {report.matches_loaded} loaded, {report.matches_rejected} rejected")
    for r in report.rejections:
        log(f"  rejected {r['file']}: {r['reason']}")
    log(
        "next: python -m features.match_state rebuild && python -m features.elo rebuild && "
        "python -m features.asof_summary rebuild && python -m features.player_summary rebuild && "
        "python -m features.feature_ledger publish && python -m ingest.sync_reference_tables"
    )
    return {"loaded": report.matches_loaded, "rejected": report.matches_rejected}


# What a public log may carry: counts and timings. Never match ids, team
# names, row contents or connection strings - the repository is public, and
# so are its Actions logs and artifacts.
PUBLIC_KEYS = (
    "selection",
    "reference_rows",
    "ingested_by_format",
    "skipped_by_reason",
    "matches_written",
    "predictions_written",
    "ledger_rows_written",
    "summary_rows_pushed",
    "elapsed_seconds",
)

_SECRET_SHAPES = (
    re.compile(r"postgres(?:ql)?://\S+"),
    # psycopg double-quotes hosts, users, databases and values it names.
    re.compile(r'"[^"]*"'),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    # psycopg's DETAIL lines quote key values, i.e. row contents.
    re.compile(r"DETAIL:.*", re.DOTALL),
)


def public_summary(report: dict) -> dict:
    return {key: report[key] for key in PUBLIC_KEYS if key in report}


def redact(message: str) -> str:
    for shape in _SECRET_SHAPES:
        message = shape.sub("[redacted]", message)
    return message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="daily_cricsheet")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "catchup-local"):
        p = sub.add_parser(name)
        p.add_argument("--bundle", choices=sorted(BUNDLES), default="recently_added_30")
        p.add_argument("--bundle-url", help="override the bundle URL (failure drills)")
        p.add_argument("--since", type=date.fromisoformat, help="ignore matches dated before this")
    sub.choices["run"].add_argument("--stage-url", required=True)
    sub.choices["run"].add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    try:
        return _main(args)
    except Exception as exc:  # noqa: BLE001 - the one place a failure becomes an exit code
        # No traceback: its frames and messages can carry row values. The
        # class and a redacted message are enough to know where to look, and
        # the run is red either way.
        log(f"FAILED: {type(exc).__name__}: {redact(str(exc))}")
        return 1


def _main(args) -> int:
    supabase_url = require_env("SUPABASE_SESSION_POOLER_URL")
    with tempfile.TemporaryDirectory() as tmp:
        paths = fetch_bundle(args.bundle_url or BUNDLES[args.bundle], Path(tmp))
        with psycopg.connect(supabase_url, connect_timeout=30) as supabase_conn:
            if args.command == "catchup-local":
                catchup_local(require_env("LOCAL_DATABASE_URL"), supabase_conn, paths, args.since)
                return 0
            version, _path, _notes = active_model_row(supabase_conn)
            artifact = resolve_pinned_artifact(supabase_conn, version, Path(env_value("MODEL_CACHE_DIR", str(CACHE_DIR))))
            log(f"model {version}")
            report = run_daily(args.stage_url, supabase_conn, paths, artifact, version, args.since)

    summary = public_summary(report)
    log(json.dumps(summary, indent=2, default=str))
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
