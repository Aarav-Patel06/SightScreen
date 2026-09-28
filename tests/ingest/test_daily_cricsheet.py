"""The daily Cricsheet job (ingest/daily_cricsheet.py) against the corpus.

Five claims, each checked against the real corpus as the oracle:

  1. FEATURE GATE. The as-of summaries rebuilt from feature_ledger - which is
     all the daily job has - are hash-identical to the corpus's own, over
     every row. If this holds, the job's features are the corpus's features.
  2. PARITY. Match 8429 (Cricsheet 1496581, India v England ODI, 2026-07-19)
     run through the job as if newly released: every innings-2 probability
     equals the local replay's, EXACTLY. Same float64 inputs into the same
     booster on the same machine; any difference is a bug, not noise. And the
     same run fed a stale ledger (the 60 days before the match removed) must
     NOT match - otherwise the parity check could not see stale state at all.
  3. IDEMPOTENCY. A second run changes nothing. A run that died before its
     ledger write is finished by the next without duplicating a row.
  4. SKIP IF LIVE. A match the live worker already has is skipped and logged;
     its live rows are byte-identical afterwards.
  5. COHORT. Everything written is source='backfill', and the calibration
     monitor's live population never sees it.

Scratch databases on the local server stand in for the runner's throwaway
Postgres ("stage") and for Supabase ("fake Supabase"); nothing here touches
the real Supabase. Skips without the corpus, like every corpus test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
NPX = shutil.which("npx")

PARITY_MATCH_ID = 8429
PARITY_CRICSHEET_ID = "1496581"
PARITY_DATE = date(2026, 7, 19)
STALE_DAYS = 60

# Every table a run reads or writes on the fake Supabase, for the
# nothing-changed comparison.
WATCHED = ("matches", "predictions", "prediction_outcomes", "feature_ledger",
           "venue_asof_summary", "elo_asof_summary", "reference_sync_state")


def _env(key: str) -> str:
    value = os.environ.get(key) or dotenv_values(ENV_PATH).get(key)
    if not value:
        pytest.skip(f"{key} not set")
    return value


def _swap_dbname(url: str, dbname: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


def _scratch(local_url: str, name: str) -> str:
    """A migrated scratch database on the corpus's server. Never the corpus."""
    if NPX is None:
        pytest.skip("npx not found on PATH")
    url = _swap_dbname(local_url, name)
    with psycopg.connect(_swap_dbname(local_url, "postgres"), autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is None:
            admin.execute(f'CREATE DATABASE "{name}"')
    subprocess.run([NPX, "supabase", "db", "push", "--db-url", url], cwd=REPO_ROOT, check=True, capture_output=True)
    return url


def _truncate_all(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()]
        conn.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
        # The migration's floor for live-worker ids, which TRUNCATE reset.
        conn.execute("SELECT setval(pg_get_serial_sequence('matches', 'match_id'), 1000000)")


def _copy_table(src, dst, table: str, where: str = "") -> None:
    with src.cursor().copy(f"COPY (SELECT * FROM {table} {where}) TO STDOUT") as out:
        with dst.cursor().copy(f"COPY {table} FROM STDIN") as inp:
            for chunk in out:
                inp.write(chunk)


@pytest.fixture(scope="module")
def local_url():
    url = _env("LOCAL_DATABASE_URL")
    # CI's LOCAL_DATABASE_URL is an empty migrated database, against which
    # the feature gate would pass vacuously. These tests mean the corpus.
    with psycopg.connect(url) as conn:
        if conn.execute("SELECT count(*) FROM matches").fetchone()[0] == 0:
            pytest.skip("the corpus is empty")
    return url


@pytest.fixture(scope="module")
def stage_url(local_url):
    return _scratch(local_url, "cricket_daily_stage_test")


@pytest.fixture(scope="module")
def fake_supabase_url(local_url):
    return _scratch(local_url, "cricket_daily_fake_supabase_test")


# Every version the daily job may serve: the active model, and P, registered
# non-active on 2026-09-28 ahead of its shadow run (SPEC.md section 11). Each
# gets the full suite - bit-identical parity included - with the fake
# Supabase marking it active, which is what the job will see when it serves.
SERVED_VERSIONS = ("winprob2-20260910", "winprob2-20260927")


@pytest.fixture(scope="module", params=SERVED_VERSIONS)
def artifact(local_url, request):
    from models.registry import load_model_version

    with psycopg.connect(local_url) as conn:
        try:
            return {"artifact": load_model_version(conn, request.param), "model_version": request.param}
        except Exception as exc:  # noqa: BLE001 - artifact absent on a fresh checkout
            pytest.skip(f"artifact for {request.param} unavailable: {exc}")


@pytest.fixture(scope="module")
def parity_file():
    # api/.env holds it relative to api/.
    path = REPO_ROOT / "api" / _env("CRICSHEET_DATA_DIR") / f"{PARITY_CRICSHEET_ID}.json"
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path


def _seed_fake_supabase(local_url: str, fake_url: str, ledger_where: str) -> None:
    """Supabase as it stands before the daily job ever ran: the reference
    tables, the active model, the summaries and their sync state, and the
    corpus ledger - minus whatever `ledger_where` excludes."""
    from features.feature_ledger import LEDGER_COLUMNS, ledger_rows

    _truncate_all(fake_url)
    with psycopg.connect(local_url) as src, psycopg.connect(fake_url) as dst:
        for table in ("venues", "teams", "players", "venue_aliases", "team_aliases", "player_aliases",
                      "model_versions", "venue_asof_summary", "elo_asof_summary", "reference_sync_state"):
            _copy_table(src, dst, table)
        rows = ledger_rows(src)
        index = {c: i for i, c in enumerate(LEDGER_COLUMNS)}
        keep = [r for r in rows if not _excluded(r, index, ledger_where)]
        placeholders = ", ".join(["%s"] * len(LEDGER_COLUMNS))
        with dst.cursor() as cur:
            cur.executemany(f"INSERT INTO feature_ledger VALUES ({placeholders})", keep)
        dst.commit()


def _excluded(row, index, mode: str) -> bool:
    if row[index["cricsheet_id"]] == PARITY_CRICSHEET_ID:
        return True
    if mode == "stale":
        day = row[index["start_time"]].date()
        return PARITY_DATE - timedelta(days=STALE_DAYS) <= day < PARITY_DATE
    return False


def _run(stage_url, fake_url, files, artifact, raw_store=None):
    from ingest.daily_cricsheet import run_daily

    with psycopg.connect(fake_url, autocommit=True) as fake:
        fake.execute("UPDATE model_versions SET is_active = (model_version = %s)", (artifact["model_version"],))
    _truncate_all(stage_url)
    with psycopg.connect(fake_url) as supabase_conn:
        return run_daily(
            stage_url, supabase_conn, files, artifact, artifact["model_version"], raw_store=raw_store
        )


def _logged(fake_url) -> dict[tuple, float]:
    with psycopg.connect(fake_url) as conn:
        rows = conn.execute(
            "SELECT p.innings, p.over_num, p.ball_in_over, (p.payload->>'p')::float8 "
            "FROM predictions p JOIN matches m USING (match_id) "
            "WHERE m.external_ids->>'cricsheet' = %s",
            (PARITY_CRICSHEET_ID,),
        ).fetchall()
    return {(i, o, b): p for i, o, b, p in rows}


def _local_replay(local_url, artifact) -> dict[tuple, float]:
    from ingest.replay_log import load_balls, score_match

    with psycopg.connect(local_url) as conn:
        balls = load_balls(conn, PARITY_MATCH_ID)
        scored = score_match(artifact, conn, PARITY_MATCH_ID, balls)
    return {(b["innings"], b["over_num"], b["ball_in_over"]): b["p"] for b in scored}


def parity_diffs(local_url, stage_url, fake_url, parity_file, artifact, ledger_mode: str):
    _seed_fake_supabase(local_url, fake_url, ledger_mode)
    report = _run(stage_url, fake_url, [parity_file], artifact)
    expected, got = _local_replay(local_url, artifact), _logged(fake_url)
    assert expected, "the local replay produced no balls - wrong fixture match"
    diffs = [(k, expected[k], got.get(k)) for k in expected if got.get(k) != expected[k]]
    diffs += [(k, None, got[k]) for k in got if k not in expected]
    return report, expected, diffs


def _snapshot(url) -> dict:
    out = {}
    with psycopg.connect(url) as conn:
        for table in WATCHED:
            out[table] = conn.execute(
                f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')), count(*) FROM {table} t"
            ).fetchone()
    return out


# --- 1. Feature gate ---------------------------------------------------------


def test_summaries_rebuilt_from_the_ledger_equal_the_corpus_summaries(local_url, stage_url):
    from features.asof_summary import DERIVED_TABLES, content_hash
    from features.feature_ledger import ledger_rows, rebuild_summaries_from_ledger

    _truncate_all(stage_url)
    with psycopg.connect(local_url) as local, psycopg.connect(stage_url) as stage:
        for table in ("venues", "teams"):
            _copy_table(local, stage, table)
        stage.commit()
        rebuild_summaries_from_ledger(stage, ledger_rows(local))
        for table in DERIVED_TABLES:
            assert content_hash(stage, table) == content_hash(local, table), table.name


# --- 2. Parity ----------------------------------------------------------------


def test_parity_with_the_local_replay_is_exact(local_url, stage_url, fake_supabase_url, parity_file, artifact):
    report, expected, diffs = parity_diffs(
        local_url, stage_url, fake_supabase_url, parity_file, artifact, "current"
    )
    assert report["ingested_by_format"] == {"ODI": 1}, report
    assert diffs == [], f"{len(diffs)} of {len(expected)} balls differ, e.g. {diffs[:3]}"
    print(f"parity: {len(expected)} balls, all bit-identical")


def test_parity_detects_a_stale_feature_state(local_url, stage_url, fake_supabase_url, parity_file, artifact):
    _report, expected, diffs = parity_diffs(
        local_url, stage_url, fake_supabase_url, parity_file, artifact, "stale"
    )
    assert diffs, "a ledger missing 60 days produced identical predictions - parity is blind to stale state"
    worst = max(abs(e - g) for _k, e, g in diffs if e is not None and g is not None)
    print(f"stale ledger: {len(diffs)} of {len(expected)} balls differ, max |dp| {worst:.4f}")


# --- 3. Idempotency ------------------------------------------------------------


def test_a_second_run_changes_nothing(local_url, stage_url, fake_supabase_url, parity_file, artifact):
    _seed_fake_supabase(local_url, fake_supabase_url, "current")
    first = _run(stage_url, fake_supabase_url, [parity_file], artifact)
    assert first["predictions_written"] > 0 and first["ledger_rows_written"] == 1
    before = _snapshot(fake_supabase_url)

    second = _run(stage_url, fake_supabase_url, [parity_file], artifact)
    assert second["selection"]["already_done"] == 1 and second["selection"]["candidates"] == 0
    assert (second["matches_written"], second["predictions_written"], second["ledger_rows_written"]) == (0, 0, 0)
    assert set(second["summary_rows_pushed"].values()) == {0}
    assert _snapshot(fake_supabase_url) == before


def test_a_run_that_died_before_its_ledger_write_is_finished_without_duplicates(
    local_url, stage_url, fake_supabase_url, parity_file, artifact
):
    _seed_fake_supabase(local_url, fake_supabase_url, "current")
    _run(stage_url, fake_supabase_url, [parity_file], artifact)
    with psycopg.connect(fake_supabase_url, autocommit=True) as conn:
        conn.execute("DELETE FROM feature_ledger WHERE cricsheet_id = %s", (PARITY_CRICSHEET_ID,))
        predictions_before = conn.execute("SELECT count(*) FROM predictions").fetchone()[0]

    resumed = _run(stage_url, fake_supabase_url, [parity_file], artifact)
    assert (resumed["matches_written"], resumed["predictions_written"], resumed["ledger_rows_written"]) == (0, 0, 1)
    with psycopg.connect(fake_supabase_url) as conn:
        assert conn.execute("SELECT count(*) FROM predictions").fetchone()[0] == predictions_before
        assert conn.execute(
            "SELECT count(*) FROM matches WHERE external_ids->>'cricsheet' = %s", (PARITY_CRICSHEET_ID,)
        ).fetchone()[0] == 1


# --- 4. Skip if live -----------------------------------------------------------


def test_a_match_the_live_worker_already_has_is_skipped(local_url, stage_url, fake_supabase_url, parity_file, artifact):
    _seed_fake_supabase(local_url, fake_supabase_url, "current")
    with psycopg.connect(local_url) as local:
        team_a, team_b, fmt, venue = local.execute(
            "SELECT team_a, team_b, format, venue_id FROM matches WHERE match_id = %s", (PARITY_MATCH_ID,)
        ).fetchone()
    observed = datetime(2026, 7, 20, 9, 30, tzinfo=timezone.utc)  # the worker's first sighting, a day later
    with psycopg.connect(fake_supabase_url, autocommit=True) as conn:
        # Teams in the other order: a pair is a pair.
        live_id = conn.execute(
            "INSERT INTO matches (external_ids, competition, format, venue_id, start_time, team_a, team_b, status) "
            "VALUES (%s, 'India tour of England', %s, %s, %s, %s, %s, 'complete') RETURNING match_id",
            (json.dumps({"cricketdata": "live-uuid"}), fmt, venue, observed, team_b, team_a),
        ).fetchone()[0]
        version = artifact["model_version"]
        for ball in (1, 2):
            conn.execute(
                "INSERT INTO predictions (match_id, model_version, prediction_type, payload, match_phase, "
                "innings, over_num, ball_in_over, source, batting_team_id) "
                "VALUES (%s, %s, 'win_prob', %s, 'innings2', 2, 0, %s, 'live', %s)",
                (live_id, version, json.dumps({"p": 0.5}), ball, team_a),
            )
    before = _snapshot(fake_supabase_url)

    report = _run(stage_url, fake_supabase_url, [parity_file], artifact)

    assert report["skipped"] == [
        {"cricsheet_id": PARITY_CRICSHEET_ID, "why": "live", "live_match_id": live_id}
    ]
    assert report["ingested_by_format"] == {}
    after = _snapshot(fake_supabase_url)
    for table in ("matches", "predictions", "prediction_outcomes", "feature_ledger"):
        assert after[table] == before[table], table


# --- 5. Cohort -----------------------------------------------------------------


def test_everything_written_is_backfill_and_invisible_to_the_live_population(
    local_url, stage_url, fake_supabase_url, parity_file, artifact
):
    from eval.calibration_monitor import _LOG_QUERY

    _seed_fake_supabase(local_url, fake_supabase_url, "current")
    report = _run(stage_url, fake_supabase_url, [parity_file], artifact)
    version = artifact["model_version"]
    with psycopg.connect(fake_supabase_url) as conn:
        by_source = dict(conn.execute(
            "SELECT p.source, count(*) FROM predictions p JOIN matches m USING (match_id) "
            "WHERE m.external_ids ? 'cricsheet' GROUP BY 1"
        ).fetchall())
        live = conn.execute(_LOG_QUERY, {"model_version": version, "source": "live"}).fetchall()
        backfill = conn.execute(_LOG_QUERY, {"model_version": version, "source": "backfill"}).fetchall()
    assert by_source == {"backfill": report["predictions_written"]}
    assert live == []
    # Resolved (an outcome row exists) and therefore in the backfill cohort.
    assert len(backfill) == report["predictions_written"]


# --- 6. Every run is recorded ----------------------------------------------------


def test_a_failed_run_exits_non_zero_and_is_still_recorded(fake_supabase_url, stage_url, monkeypatch):
    """/accuracy flags the ingest when its last SUCCESS is old, so a failure
    must be recorded as a failure - never as nothing, and never as success -
    and must still turn the run red. Class name only: the table is public
    via the page, and a message can carry row values."""
    from ingest import daily_cricsheet

    _truncate_all(fake_supabase_url)
    monkeypatch.setenv("SUPABASE_SESSION_POOLER_URL", fake_supabase_url)
    code = daily_cricsheet.main(
        ["run", "--stage-url", stage_url, "--bundle-url", "https://cricsheet.invalid/x.zip"]
    )
    assert code == 1
    with psycopg.connect(fake_supabase_url) as conn:
        rows = conn.execute(
            "SELECT pipeline, status, error_class, counts FROM pipeline_runs"
        ).fetchall()
    assert rows == [("cricsheet_daily", "failure", "URLError", {})]


# --- 7. First-seen team and venue: the cold start -------------------------------
#
# Match 8002, Panama v Turks and Caicos Island (T20, 2025-04-17, Clayton
# Panama, Panama City), is BOTH the team's and the venue's first appearance in
# the corpus. Training gave it Elo 1500 for the new side (features.elo's
# STARTING_RATING) and NaN venue features (fewer than MIN_VENUE_MATCHES prior
# matches). The daily job, meeting both for the first time, must create them
# with the loader's own alias rules and produce the identical predictions.

COLD_MATCH_ID = 8002
COLD_CRICSHEET_ID = "1481296"


def _seed_before_first_appearance(local_url: str, fake_url: str) -> tuple[int, int]:
    """Supabase as it would have been the day before match 8002's team and
    venue existed: neither they, their aliases, nor any ledger or summary row
    that mentions them."""
    from features.feature_ledger import LEDGER_COLUMNS, ledger_rows

    _truncate_all(fake_url)
    with psycopg.connect(local_url) as src, psycopg.connect(fake_url) as dst:
        team, venue = src.execute(
            "SELECT team_b, venue_id FROM matches WHERE match_id = %s", (COLD_MATCH_ID,)
        ).fetchone()
        wheres = {
            "venues": f"WHERE venue_id <> {venue}",
            "teams": f"WHERE team_id <> {team}",
            "venue_aliases": f"WHERE venue_id <> {venue}",
            "team_aliases": f"WHERE team_id <> {team}",
            "venue_asof_summary": f"WHERE venue_id <> {venue}",
            "elo_asof_summary": f"WHERE team_id <> {team}",
        }
        for table in ("venues", "teams", "players", "venue_aliases", "team_aliases", "player_aliases",
                      "model_versions", "venue_asof_summary", "elo_asof_summary", "reference_sync_state"):
            _copy_table(src, dst, table, wheres.get(table, ""))
        index = {c: i for i, c in enumerate(LEDGER_COLUMNS)}
        keep = [
            r for r in ledger_rows(src)
            if r[index["cricsheet_id"]] != COLD_CRICSHEET_ID
            and team not in (r[index["team_a"]], r[index["team_b"]], r[index["winner"]])
            and r[index["venue_id"]] != venue
        ]
        placeholders = ", ".join(["%s"] * len(LEDGER_COLUMNS))
        with dst.cursor() as cur:
            cur.executemany(f"INSERT INTO feature_ledger VALUES ({placeholders})", keep)
        dst.commit()
    return team, venue


def _cold_logged(fake_url) -> dict[tuple, float]:
    with psycopg.connect(fake_url) as conn:
        rows = conn.execute(
            "SELECT p.innings, p.over_num, p.ball_in_over, (p.payload->>'p')::float8 "
            "FROM predictions p JOIN matches m USING (match_id) "
            "WHERE m.external_ids->>'cricsheet' = %s",
            (COLD_CRICSHEET_ID,),
        ).fetchall()
    return {(i, o, b): p for i, o, b, p in rows}


def _cold_replay(local_url, artifact) -> dict[tuple, float]:
    from ingest.replay_log import load_balls, score_match

    with psycopg.connect(local_url) as conn:
        scored = score_match(artifact, conn, COLD_MATCH_ID, load_balls(conn, COLD_MATCH_ID))
    return {(b["innings"], b["over_num"], b["ball_in_over"]): b["p"] for b in scored}


@pytest.fixture(scope="module")
def cold_file():
    path = REPO_ROOT / "api" / _env("CRICSHEET_DATA_DIR") / f"{COLD_CRICSHEET_ID}.json"
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path


def _cold_diffs(local_url, stage_url, fake_url, cold_file, artifact):
    _seed_before_first_appearance(local_url, fake_url)
    report = _run(stage_url, fake_url, [cold_file], artifact)
    expected, got = _cold_replay(local_url, artifact), _cold_logged(fake_url)
    assert expected, "the local replay produced no balls - wrong fixture match"
    diffs = [(k, expected[k], got.get(k)) for k in expected if got.get(k) != expected[k]]
    diffs += [(k, None, got[k]) for k in got if k not in expected]
    return report, expected, diffs


def test_a_first_seen_team_and_venue_get_trainings_cold_start_exactly(
    local_url, stage_url, fake_supabase_url, cold_file, artifact
):
    from ingest.daily_cricsheet import ENTITY_ID_FLOOR

    report, expected, diffs = _cold_diffs(local_url, stage_url, fake_supabase_url, cold_file, artifact)
    assert report["skipped"] == [], report["skipped"]
    assert report["entities_created"] == {"teams": 1, "venues": 1}
    assert diffs == [], f"{len(diffs)} of {len(expected)} balls differ, e.g. {diffs[:3]}"

    # Created with the loader's alias rules, in the daily job's id band.
    with psycopg.connect(fake_supabase_url) as conn:
        team = conn.execute(
            "SELECT t.team_id, t.name, a.source_name FROM teams t JOIN team_aliases a USING (team_id) "
            "WHERE t.name = 'Turks and Caicos Island'"
        ).fetchall()
        venue = conn.execute(
            "SELECT v.venue_id, v.name, a.source_name FROM venues v JOIN venue_aliases a USING (venue_id) "
            "WHERE v.name = 'Clayton Panama, Panama City'"
        ).fetchall()
        used = conn.execute(
            "SELECT team_b, venue_id FROM matches WHERE external_ids->>'cricsheet' = %s",
            (COLD_CRICSHEET_ID,),
        ).fetchone()
    assert len(team) == 1 and team[0][0] >= ENTITY_ID_FLOOR and team[0][2] == "Turks and Caicos Island"
    assert len(venue) == 1 and venue[0][0] >= ENTITY_ID_FLOOR
    assert used == (team[0][0], venue[0][0])
    print(f"cold start: {len(expected)} balls bit-identical; team {team[0][0]}, venue {venue[0][0]}")


def test_the_cold_start_gate_sees_a_cold_start_that_differs(
    local_url, stage_url, fake_supabase_url, cold_file, artifact, monkeypatch
):
    """The same gate, with the new entities given history they do not have:
    an Elo of 1600 for the new team, and ten prior chases at the new venue.
    If this still matched, the gate could not tell a cold start from any
    other."""
    from ingest import daily_cricsheet

    original = daily_cricsheet.rebuild_summaries_from_ledger

    def warm_start(stage_conn, rows):
        result = original(stage_conn, rows)
        with stage_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO elo_asof_summary SELECT team_id, 'T20', DATE '2020-01-01', 1600 "
                "FROM teams WHERE team_id >= %s", (daily_cricsheet.ENTITY_ID_FLOOR,)
            )
            cur.execute(
                "INSERT INTO venue_asof_summary SELECT venue_id, DATE '2020-01-01', 5.0, 10, 1600, 10 "
                "FROM venues WHERE venue_id >= %s", (daily_cricsheet.ENTITY_ID_FLOOR,)
            )
        stage_conn.commit()
        return result

    monkeypatch.setattr(daily_cricsheet, "rebuild_summaries_from_ledger", warm_start)
    _report, expected, diffs = _cold_diffs(local_url, stage_url, fake_supabase_url, cold_file, artifact)
    assert diffs, "a warm start for the new team and venue changed nothing - the gate is blind to it"
    worst = max(abs(e - g) for _k, e, g in diffs if e is not None and g is not None)
    print(f"warm start: {len(diffs)} of {len(expected)} balls differ, max |dp| {worst:.4f}")


def test_the_catch_up_adopts_the_daily_jobs_new_entities_instead_of_duplicating_them(
    local_url, stage_url, fake_supabase_url, cold_file, artifact
):
    """After the daily job creates a team and venue at ids >= ENTITY_ID_FLOOR,
    a corpus catching up must end with THOSE ids - not mint its own copies,
    which would give one team two ids and fail the next reference sync."""
    from ingest.cricsheet import LoadReport, load_match
    from ingest.daily_cricsheet import ENTITY_ID_FLOOR, pull_entity_band

    team, venue = _seed_before_first_appearance(local_url, fake_supabase_url)
    _run(stage_url, fake_supabase_url, [cold_file], artifact)

    # A "corpus" that has never seen the team or venue: the stage scratch
    # database, reset and given the same reference tables minus both.
    _truncate_all(stage_url)
    with psycopg.connect(local_url) as src, psycopg.connect(stage_url) as corpus:
        for table, where in (("venues", f"WHERE venue_id <> {venue}"), ("teams", f"WHERE team_id <> {team}"),
                             ("players", ""), ("venue_aliases", f"WHERE venue_id <> {venue}"),
                             ("team_aliases", f"WHERE team_id <> {team}"), ("player_aliases", "")):
            _copy_table(src, corpus, table, where)
        corpus.commit()

    with psycopg.connect(fake_supabase_url) as supabase, psycopg.connect(stage_url) as corpus:
        assert pull_entity_band(supabase, corpus) == {"teams": 1, "venues": 1}
        with psycopg.connect(stage_url, autocommit=True) as catalog:
            report = LoadReport()
            load_match(corpus, catalog, report, cold_file)
        assert report.matches_loaded == 1
        assert report.resolution_counts["team"]["auto_created"] == 0
        assert report.resolution_counts["venue"]["auto_created"] == 0
        team_b, venue_id = corpus.execute(
            "SELECT team_b, venue_id FROM matches WHERE external_ids->>'cricsheet' = %s", (COLD_CRICSHEET_ID,)
        ).fetchone()
        supabase_ids = supabase.execute(
            "SELECT team_b, venue_id FROM matches WHERE external_ids->>'cricsheet' = %s", (COLD_CRICSHEET_ID,)
        ).fetchone()
    assert (team_b, venue_id) == supabase_ids
    assert team_b >= ENTITY_ID_FLOOR and venue_id >= ENTITY_ID_FLOOR


def test_a_reference_sync_keeps_supabases_sequences_below_the_entity_band(stage_url, fake_supabase_url):
    """The daily job's first-seen teams live at ids >= SUPABASE_ID_FLOOR. A
    reference sync that set Supabase's sequence to a plain MAX(team_id) would
    move it INTO that band, and the next serial insert would allocate there."""
    from db.defaults import SUPABASE_ID_FLOOR
    from ingest.sync_reference_tables import sync_table

    _truncate_all(stage_url)
    _truncate_all(fake_supabase_url)
    with psycopg.connect(stage_url) as local, psycopg.connect(fake_supabase_url) as supabase:
        supabase.execute(
            "INSERT INTO teams (team_id, name) VALUES (%s, 'First Seen By The Daily Job')", (SUPABASE_ID_FLOOR,)
        )
        supabase.commit()
        local.execute("INSERT INTO teams (team_id, name) VALUES (5, 'A Corpus Team')")
        local.commit()
        sync_table(local, supabase, "teams", "team_id", ("team_id", "name", "short_name", "full_member"))
        last_value = supabase.execute("SELECT last_value FROM teams_team_id_seq").fetchone()[0]
    assert last_value == 5


# --- 8. The raw JSON is kept ---------------------------------------------------------


class RecordingStore:
    def __init__(self, fail: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail = fail

    def put(self, cricsheet_id: str, raw: bytes) -> None:
        if self.fail:
            raise ConnectionError("storage unreachable")
        self.objects[cricsheet_id] = raw


def test_every_ingested_match_keeps_its_raw_json(local_url, stage_url, fake_supabase_url, parity_file, artifact):
    """So a future re-parse - a column the loader did not read the first
    time - never needs Cricsheet's full archive."""
    _seed_fake_supabase(local_url, fake_supabase_url, "current")
    store = RecordingStore()
    report = _run(stage_url, fake_supabase_url, [parity_file], artifact, raw_store=store)
    assert report["raw_files_stored"] == 1
    assert store.objects == {PARITY_CRICSHEET_ID: parity_file.read_bytes()}


def test_a_match_whose_raw_json_cannot_be_kept_is_not_ingested(
    local_url, stage_url, fake_supabase_url, parity_file, artifact
):
    """The upload comes before any Supabase write, so a failure leaves the
    match undone - and the next run, finding no ledger row, stores it again."""
    _seed_fake_supabase(local_url, fake_supabase_url, "current")
    with pytest.raises(ConnectionError):
        _run(stage_url, fake_supabase_url, [parity_file], artifact, raw_store=RecordingStore(fail=True))
    with psycopg.connect(fake_supabase_url) as conn:
        written = conn.execute(
            "SELECT (SELECT count(*) FROM matches WHERE external_ids ? 'cricsheet'), "
            "(SELECT count(*) FROM feature_ledger WHERE cricsheet_id = %s)",
            (PARITY_CRICSHEET_ID,),
        ).fetchone()
    assert written == (0, 0)
