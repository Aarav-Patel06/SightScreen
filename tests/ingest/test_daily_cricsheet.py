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


@pytest.fixture(scope="module")
def artifact(local_url):
    from models.registry import load_model_version

    with psycopg.connect(local_url) as conn:
        row = conn.execute("SELECT model_version FROM model_versions WHERE is_active").fetchone()
        if row is None:
            pytest.skip("no active model locally")
        try:
            return {"artifact": load_model_version(conn, row[0]), "model_version": row[0]}
        except Exception as exc:  # noqa: BLE001 - artifact absent on a fresh checkout
            pytest.skip(f"artifact unavailable: {exc}")


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


def _run(stage_url, fake_url, files, artifact):
    from ingest.daily_cricsheet import run_daily

    _truncate_all(stage_url)
    with psycopg.connect(fake_url) as supabase_conn:
        return run_daily(stage_url, supabase_conn, files, artifact, artifact["model_version"])


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
