"""End-to-end deployment smoke test (Phase 2 session 4, Decision 7).

What proves the deployment worked: a replay driven through the running
service, writing predictions to Supabase, **verified by querying Supabase
afterwards** - not by reading logs and not by trusting the HTTP response.

The shape follows from SPEC.md section 2.1. replay.py reads its ball sequence
from the local corpus, which a container cannot reach, so the driver stays
local and the service does the serving:

    local driver                     the service              Supabase
    -------------------------------  ----------------------   --------
    read innings-2 balls for one
    match from the LOCAL corpus
          |
          |  POST /predict/win-prob   (one request per ball)
          +------------------------>  as-of features from the
                                      synced summaries, predict
                                      with the pulled artifact,
                                      INSERT INTO predictions ---> row
          |
          |  then, on a SEPARATE connection:
          +-------------------------------------------------------> SELECT
               assert one row per ball, the pinned model_version,
               p strictly in (0, 1), created_at inside the window

Skipped unless SMOKE_BASE_URL is set, so it never runs in CI and never
depends on something being deployed. Point it at a locally-run container in
session 4a and at the Railway URL in 4b - the test does not care which, which
is the point of it.

    SMOKE_BASE_URL=http://localhost:8000 pytest tests/deploy/test_smoke.py -v
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

BALLS_TO_SEND = 12
REQUEST_TIMEOUT = 30.0


def _env() -> dict:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL") or not env.get("SUPABASE_SESSION_POOLER_URL"):
        pytest.skip(f"LOCAL_DATABASE_URL and SUPABASE_SESSION_POOLER_URL must be set in {ENV_PATH}")
    return env


@pytest.fixture(scope="module")
def base_url() -> str:
    url = os.environ.get("SMOKE_BASE_URL")
    if not url:
        pytest.skip("set SMOKE_BASE_URL to the running service to enable the smoke test")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def local_conn():
    # connect_timeout is not optional here: without it this fixture blocked
    # for 20.5 hours against an unreachable Supabase before anyone noticed.
    connection = psycopg.connect(_env()["LOCAL_DATABASE_URL"], autocommit=True, connect_timeout=20)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def supabase_conn():
    connection = psycopg.connect(
        _env()["SUPABASE_SESSION_POOLER_URL"], autocommit=True, connect_timeout=20
    )
    yield connection
    connection.close()


def _get(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return json.loads(response.read())


def _post(url: str, body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise AssertionError(f"POST {url} -> HTTP {exc.code}: {exc.read().decode()[:400]}") from exc


# --- health ---------------------------------------------------------------


def test_health_reports_a_pooler_host_and_a_verified_model(base_url):
    """Decision 6: the deployed service must prove which host it reached,
    rather than the env var being taken on trust."""
    health = _get(f"{base_url}/health")
    assert health["status"] == "ok"
    assert health["db_host"].endswith(".pooler.supabase.com:5432"), health["db_host"]
    assert health["db_address_family"] == "AF_INET", (
        "SPEC.md section 2.4: a direct host resolves IPv6-only and is unreachable "
        f"from a container. Got {health}"
    )
    assert len(health["model_sha256"]) == 64
    assert health["reference_age_days"] is not None


# --- the replay ------------------------------------------------------------


@pytest.fixture(scope="module")
def replay_balls(local_conn) -> tuple[int, list[dict]]:
    """Innings-2 balls for one completed match, read from the LOCAL corpus.

    The match must also exist on Supabase, because predictions.match_id is a
    foreign key and Supabase holds only live/recent matches (section 2.1).
    """
    with local_conn.cursor() as cur:
        cur.execute(
            """
            SELECT ms.match_id, ms.score, ms.wickets, ms.balls_bowled, ms.balls_remaining,
                   ms.target, ms.runs_required, ms.current_run_rate, ms.required_run_rate,
                   ms.rrr_minus_crr, ms.partnership_runs, ms.partnership_balls,
                   ms.balls_since_wicket, ms.phase, ms.match_date,
                   m.venue_id, m.format, d.batting_team_id, d.bowling_team_id
            FROM match_states ms
            JOIN matches m ON m.match_id = ms.match_id
            JOIN deliveries d ON d.delivery_id = ms.delivery_id
            WHERE ms.innings = 2
              AND ms.required_run_rate IS NOT NULL
              AND ms.batting_team_won IS NOT NULL
              AND NOT ms.has_reconciliation_anomaly
              AND m.venue_id IS NOT NULL
              AND ms.match_id = (
                  SELECT match_id FROM match_states
                  WHERE innings = 2 AND required_run_rate IS NOT NULL
                  ORDER BY match_id DESC LIMIT 1
              )
            ORDER BY ms.balls_bowled
            LIMIT %s
            """,
            (BALLS_TO_SEND,),
        )
        rows = cur.fetchall()
    if not rows:
        pytest.skip("no innings-2 rows in the local corpus")

    match_id = rows[0][0]
    balls = [
        {
            "match_id": match_id,
            "innings": 2,
            "score": r[1],
            "wickets": r[2],
            "balls_bowled": r[3],
            "balls_remaining": r[4],
            "target": r[5],
            "runs_required": r[6],
            "current_run_rate": float(r[7]) if r[7] is not None else None,
            "required_run_rate": float(r[8]) if r[8] is not None else None,
            "rrr_minus_crr": float(r[9]) if r[9] is not None else None,
            "partnership_runs": r[10],
            "partnership_balls": r[11],
            "balls_since_wicket": r[12],
            "phase": r[13],
            "match_date": r[14].isoformat(),
            "venue_id": r[15],
            "format": r[16],
            "batting_team_id": r[17],
            "bowling_team_id": r[18],
        }
        for r in rows
    ]
    return match_id, balls


def test_replay_writes_one_prediction_per_ball_and_supabase_agrees(
    base_url, replay_balls, supabase_conn, local_conn
):
    match_id, balls = replay_balls

    # predictions.match_id is a FK into a table that holds only live/recent
    # matches. Mirror the one row across so the FK resolves; this is the same
    # thing cricketdata.py's _ensure_match_row does for a real live match.
    with local_conn.cursor() as cur:
        cur.execute(
            "SELECT competition, format, venue_id, start_time, team_a, team_b, status "
            "FROM matches WHERE match_id = %s",
            (match_id,),
        )
        match_row = cur.fetchone()
    with supabase_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO matches (match_id, competition, format, venue_id, start_time,
                                 team_a, team_b, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (match_id) DO NOTHING
            """,
            (match_id, *match_row),
        )

    started = datetime.now(timezone.utc) - timedelta(seconds=5)
    returned = [_post(f"{base_url}/predict/win-prob", ball) for ball in balls]
    assert len(returned) == len(balls)

    # The assertion that matters: ask Supabase, on a connection the service
    # never touched, rather than believing the responses above.
    with supabase_conn.cursor() as cur:
        cur.execute(
            """
            SELECT prediction_id, model_version, prediction_type, match_phase,
                   (payload->>'p')::numeric, created_at
            FROM predictions
            WHERE match_id = %s AND created_at >= %s
            ORDER BY prediction_id
            """,
            (match_id, started),
        )
        stored = cur.fetchall()

    assert len(stored) == len(balls), (
        f"sent {len(balls)} balls but Supabase holds {len(stored)} predictions for match "
        f"{match_id} in this window"
    )
    health = _get(f"{base_url}/health")
    for prediction_id, model_version, ptype, phase, probability, created_at in stored:
        assert model_version == health["model_version"], "written under an unexpected model"
        assert ptype == "win_prob"
        assert phase == "innings2"
        assert 0.0 < float(probability) < 1.0, f"prediction {prediction_id} out of range"
        assert created_at >= started

    assert [row[0] for row in stored] == sorted(row[0] for row in stored)

    # The stored probability must be the one the caller was handed. JSONB
    # keeps numbers as numeric, so this round-trips exactly - if it ever does
    # not, the response and the logged prediction have diverged and the
    # accuracy page would be scoring something the user never saw.
    assert [float(row[4]) for row in stored] == [r["win_probability"] for r in returned]

    print(f"\n{len(stored)} predictions verified in Supabase for match {match_id}")


def test_first_innings_is_refused_rather_than_guessed(base_url, replay_balls):
    """The model is innings-2 only. A service that silently returned a number
    for innings 1 would be inventing one."""
    _, balls = replay_balls
    payload = dict(balls[0], innings=1)
    request = urllib.request.Request(
        f"{base_url}/predict/win-prob",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT)
    assert exc.value.code == 400
