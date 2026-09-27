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
  * rejected  - the loader rejected the file (its own reasons, verbatim).
  * NOT skipped: a first-seen team or venue. The loader's resolver creates it
                with the corpus's alias rules, the features give it training's
                cold start (Elo 1500; venue NaN under ten prior matches), and
                it takes an id in the ENTITY_ID_FLOOR band - see "First-seen
                teams and venues" below.
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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg

from db.defaults import SUPABASE_ID_FLOOR
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
from ingest.raw_store import RawStore
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


# --- First-seen teams and venues --------------------------------------------------
#
# The stage database's resolver creates a first-seen team or venue with the
# corpus loader's own alias rules (ingest/entity_resolution.py). What it cannot
# do alone is give it an id that means the same thing everywhere: the corpus
# creates entities of its own, and two databases minting ids from two
# sequences is how one id comes to mean two things.
#
# So an entity first seen HERE takes an id in its own band, at or above
# ENTITY_ID_FLOOR - the floor the live worker's match ids already use - where
# the corpus never allocates (its sequences sit in the hundreds). The local
# catch-up pulls the band into the corpus BEFORE loading, so the corpus loader
# then finds these entities by exact alias and never creates a duplicate.

ENTITY_ID_FLOOR = SUPABASE_ID_FLOOR


@dataclass(frozen=True)
class Entity:
    table: str
    id_column: str
    columns: tuple[str, ...]
    alias_table: str


TEAM_ENTITY = Entity("teams", "team_id", ("name", "short_name", "full_member"), "team_aliases")
VENUE_ENTITY = Entity("venues", "venue_id", ("name", "city", "country"), "venue_aliases")
ALIAS_COLUMNS = ("source", "source_name", "source_id")


def _next_in_band(conn, table: str, column: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT GREATEST(coalesce(max({column}), 0), %s) + 1 FROM {table}", (ENTITY_ID_FLOOR - 1,)
        )
        return cur.fetchone()[0]


def promote_entities(stage_conn, supabase_conn, entity: Entity, stage_ids: set[int]) -> int:
    """Give stage-created entities their Supabase id, on both sides, with the
    aliases the resolver made for them.

    Committed on Supabase at once: if the run dies later, the next run copies
    them into its stage database and resolves them by exact alias, so nothing
    is created twice. Returns how many were created.
    """
    if not stage_ids:
        return 0
    columns = ", ".join(entity.columns)
    alias_columns = ", ".join(ALIAS_COLUMNS)
    with supabase_conn.cursor() as cur:
        # One writer in the band at a time. The workflow's concurrency group
        # already serialises the job; this covers anything else.
        cur.execute(f"LOCK TABLE {entity.table}, {entity.alias_table} IN SHARE ROW EXCLUSIVE MODE")
    next_id = _next_in_band(supabase_conn, entity.table, entity.id_column)
    next_alias = _next_in_band(supabase_conn, entity.alias_table, "alias_id")

    with stage_conn.cursor() as stage, supabase_conn.cursor() as remote:
        for old_id in sorted(stage_ids):
            stage.execute(f"SELECT {columns} FROM {entity.table} WHERE {entity.id_column} = %s", (old_id,))
            values = stage.fetchone()
            stage.execute(
                f"SELECT {alias_columns} FROM {entity.alias_table} "
                f"WHERE {entity.id_column} = %s ORDER BY alias_id",
                (old_id,),
            )
            aliases = stage.fetchall()
            new_id, next_id = next_id, next_id + 1

            # Stage: delete, then insert, so a UNIQUE(name) never sees the old
            # and new rows at once.
            stage.execute(f"DELETE FROM {entity.alias_table} WHERE {entity.id_column} = %s", (old_id,))
            stage.execute(f"DELETE FROM {entity.table} WHERE {entity.id_column} = %s", (old_id,))
            for target in (stage, remote):
                target.execute(
                    f"INSERT INTO {entity.table} ({entity.id_column}, {columns}) "
                    f"VALUES (%s, {', '.join(['%s'] * len(entity.columns))})",
                    (new_id, *values),
                )
            for alias in aliases:
                alias_id, next_alias = next_alias, next_alias + 1
                for target in (stage, remote):
                    target.execute(
                        f"INSERT INTO {entity.alias_table} (alias_id, {entity.id_column}, {alias_columns}) "
                        f"VALUES (%s, %s, %s, %s, %s)",
                        (alias_id, new_id, *alias),
                    )
    supabase_conn.commit()
    stage_conn.commit()
    return len(stage_ids)


def pull_entity_band(supabase_conn, local_conn) -> dict[str, int]:
    """The catch-up's first step: every Supabase entity and alias in the daily
    job's band that the corpus lacks, ids unchanged. A same-named entity the
    corpus created independently fails loudly on UNIQUE(name) - never merged
    by guesswork."""
    pulled = {}
    for entity in (TEAM_ENTITY, VENUE_ENTITY):
        columns = ", ".join((entity.id_column, *entity.columns))
        alias_columns = ", ".join(("alias_id", entity.id_column, *ALIAS_COLUMNS))
        with supabase_conn.cursor() as cur:
            cur.execute(
                f"SELECT {columns} FROM {entity.table} WHERE {entity.id_column} >= %s ORDER BY 1",
                (ENTITY_ID_FLOOR,),
            )
            rows = cur.fetchall()
            cur.execute(
                f"SELECT {alias_columns} FROM {entity.alias_table} "
                f"WHERE {entity.id_column} >= %s ORDER BY 1",
                (ENTITY_ID_FLOOR,),
            )
            aliases = cur.fetchall()
        count = 0
        with local_conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    f"INSERT INTO {entity.table} ({columns}) VALUES ({', '.join(['%s'] * len(row))}) "
                    f"ON CONFLICT ({entity.id_column}) DO NOTHING",
                    row,
                )
                count += cur.rowcount
            for alias in aliases:
                cur.execute(
                    f"INSERT INTO {entity.alias_table} ({alias_columns}) VALUES (%s, %s, %s, %s, %s) "
                    f"ON CONFLICT (alias_id) DO NOTHING",
                    alias,
                )
        local_conn.commit()
        pulled[entity.table] = count
    return pulled


# --- The run -------------------------------------------------------------------


def run_daily(
    stage_url: str,
    supabase_conn,
    paths: list[Path],
    artifact: dict,
    model_version: str,
    since: date | None = None,
    raw_store=None,
) -> dict:
    """`raw_store` keeps each ingested match's raw JSON (ingest/raw_store.py);
    the CLI always passes one. Tests pass a recording stand-in."""
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
        new_teams: set[int] = set()
        new_venues: set[int] = set()
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
            if (live_id := live_duplicate(supabase_conn, staged)) is not None:
                report["skipped"].append(
                    {"cricsheet_id": cand["cricsheet_id"], "why": "live", "live_match_id": live_id}
                )
                continue
            # A first-seen team or venue is NOT a skip: the loader's resolver
            # created it exactly as the corpus loader would have, and the
            # features treat it exactly as training did (Elo 1500, venue NaN
            # under ten prior matches). It only needs a Supabase id - below.
            new_teams.update(t for t in (staged["team_a"], staged["team_b"]) if t > max_team)
            if staged["venue_id"] is not None and staged["venue_id"] > max_venue:
                new_venues.add(staged["venue_id"])
            eligible.append(cand)
        if pass1_ids:
            _drop_staged(stage, pass1_ids)
        log(f"skipped: {dict(Counter(s['why'] for s in report['skipped']))}")
        report["entities_created"] = {
            "teams": promote_entities(stage, supabase_conn, TEAM_ENTITY, new_teams),
            "venues": promote_entities(stage, supabase_conn, VENUE_ENTITY, new_venues),
        }
        log(f"entities created: {report['entities_created']}")

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

        # The raw JSON, BEFORE anything is written to Supabase: a match is
        # only "done" once its ledger row exists, so a failed upload leaves it
        # undone and the next run stores it again.
        stored = 0
        if raw_store is not None:
            for cand in eligible:
                raw_store.put(cand["cricsheet_id"], cand["path"].read_bytes())
                stored += 1
        report["raw_files_stored"] = stored

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


def corpus_data_dir() -> Path:
    """CRICSHEET_DATA_DIR, which api/.env gives relative to api/."""
    configured = Path(require_env("CRICSHEET_DATA_DIR"))
    return configured if configured.is_absolute() else Path(__file__).resolve().parents[2] / configured


def catchup_local(local_url: str, supabase_conn, paths: list[Path], since: date | None) -> dict:
    """Load released matches into the LOCAL corpus for future retraining,
    adopting the id Supabase already gave a match so the two sides agree.
    Pulls the daily job's entity band first, so first-seen teams and venues
    keep the ids Supabase already uses. Loads every in-scope file the corpus
    lacks. Does not rebuild or retrain; prints the commands that come next."""
    state = supabase_state(supabase_conn)
    index = {c: i for i, c in enumerate(LEDGER_COLUMNS)}
    report = LoadReport()
    data_dir = corpus_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(local_url) as bulk, psycopg.connect(local_url, autocommit=True) as catalog:
        # BEFORE any load: the entities the daily job created, ids unchanged,
        # so the loader below resolves them by exact alias instead of minting
        # its own duplicates.
        log(f"pulled from Supabase's entity band: {pull_entity_band(supabase_conn, bulk)}")
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
            if match_id is None:
                # NEVER the corpus's own serial. Migration 20260919000001 set
                # the corpus's matches sequence to 1,000,000 as well, so a
                # serial id here would be one Supabase has already given a
                # different match. Supabase is the one allocator above the
                # baseline; an id reserved and then unused is only a gap.
                match_id = _next_supabase_id(supabase_conn)
            load_match(bulk, catalog, report, path, match_id=match_id)
            # Kept on disk, beside the corpus's other files, so a future
            # re-parse never needs Cricsheet's full archive again.
            kept = data_dir / path.name
            if not kept.exists():
                kept.write_bytes(path.read_bytes())
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
    "entities_created",
    "ingested_by_format",
    "skipped_by_reason",
    "matches_written",
    "predictions_written",
    "ledger_rows_written",
    "raw_files_stored",
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

    started = datetime.now(timezone.utc)
    status, summary, error_class = "success", {}, None
    try:
        summary = _main(args)
    except Exception as exc:  # noqa: BLE001 - the one place a failure becomes an exit code
        # No traceback: its frames and messages can carry row values. The
        # class and a redacted message are enough to know where to look, and
        # the run is red either way.
        log(f"FAILED: {type(exc).__name__}: {redact(str(exc))}")
        status, error_class = "failure", type(exc).__name__
    if args.command == "run":
        record_run(started, status, summary, error_class)
    return 0 if status == "success" else 1


PIPELINE = "cricsheet_daily"


def record_run(started: datetime, status: str, summary: dict, error_class: str | None) -> None:
    """One pipeline_runs row per run, success or failure - /accuracy shows the
    last success and flags it when stale. Best effort: a run that cannot reach
    Supabase cannot record that it failed (that absence is what the staleness
    flag catches), and failing to record must never turn a failed run green
    or a good one red."""
    try:
        with psycopg.connect(require_env("SUPABASE_SESSION_POOLER_URL"), connect_timeout=30) as conn:
            conn.execute(
                "INSERT INTO pipeline_runs (pipeline, started_at, finished_at, status, counts, error_class) "
                "VALUES (%s, %s, now(), %s, %s, %s)",
                (PIPELINE, started, status, json.dumps(summary, default=str), error_class),
            )
        log(f"recorded {status} in pipeline_runs")
    except Exception as exc:  # noqa: BLE001 - see docstring
        log(f"could not record the run in pipeline_runs: {type(exc).__name__}")


def _main(args) -> dict:
    supabase_url = require_env("SUPABASE_SESSION_POOLER_URL")
    with tempfile.TemporaryDirectory() as tmp:
        paths = fetch_bundle(args.bundle_url or BUNDLES[args.bundle], Path(tmp))
        with psycopg.connect(supabase_url, connect_timeout=30) as supabase_conn:
            if args.command == "catchup-local":
                catchup_local(require_env("LOCAL_DATABASE_URL"), supabase_conn, paths, args.since)
                return {}
            version, _path, _notes = active_model_row(supabase_conn)
            artifact = resolve_pinned_artifact(supabase_conn, version, Path(env_value("MODEL_CACHE_DIR", str(CACHE_DIR))))
            log(f"model {version}")
            store = RawStore()
            store.ensure_bucket()
            report = run_daily(
                args.stage_url, supabase_conn, paths, artifact, version, args.since, raw_store=store
            )

    summary = public_summary(report)
    log(json.dumps(summary, indent=2, default=str))
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    sys.exit(main())
