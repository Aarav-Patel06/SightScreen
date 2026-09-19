"""The worker's scoring path, driven by a replay (Phase 3 session 1).

`ReplayClient` implements the same `LiveClient` interface `CricketDataClient`
does, so the worker's predictor can be exercised end to end without a live
match - which is the only way this path is testable at all, since live
cricket is a calendar problem.

What these do NOT prove, said plainly: the CricketData path itself. That
provider supplies no toss and no per-ball team, so its `current_teams`
depends on parsing an innings label, and the difference between "the wiring
works" and "it works on the live feed" is exactly the gap Phase 2 closed for
the HTTP path and cannot close here.
"""

from __future__ import annotations

import json
import os

import psycopg
import pytest
from dotenv import dotenv_values

from ingest.live_client import MatchState
from ingest.replay import ReplayClient
from serving.live_loop import LivePredictor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_PATH = os.path.join(REPO_ROOT, "api", ".env")

# A completed CPL chase from the local corpus, used by Phase 2's acceptance
# replay as well, so its shape is already known: 125 innings-2 deliveries.
REPLAY_MATCH = 9337


class _StubGuard:
    """Stands in for ActiveVersionGuard. Records that it was consulted,
    because "the pin is re-confirmed before each write" is a claim worth
    testing rather than trusting."""

    def __init__(self) -> None:
        self.calls = 0

    def confirm(self, conn) -> None:
        self.calls += 1


def _local_conn():
    url = os.environ.get("LOCAL_DATABASE_URL") or dotenv_values(ENV_PATH).get(
        "LOCAL_DATABASE_URL"
    )
    if not url:
        pytest.skip("LOCAL_DATABASE_URL not set")
    return psycopg.connect(url, connect_timeout=20)


@pytest.fixture()
def corpus():
    with _local_conn() as conn:
        yield conn


def _artifact(corpus):
    """The registered model, loaded from the local artifact directory."""
    from models.registry import load_model_version

    with corpus.cursor() as cur:
        cur.execute("SELECT model_version FROM model_versions WHERE is_active LIMIT 1")
        row = cur.fetchone()
    if row is None:
        pytest.skip("no active model_versions row locally")
    version = row[0]
    try:
        artifact = load_model_version(corpus, version)
    except Exception as exc:  # noqa: BLE001 - artifact absent on a fresh checkout
        pytest.skip(f"artifact for {version} unavailable: {exc}")
    return {"artifact": artifact, "model_version": version}


class _RecordingConn:
    """A real connection for reads, an interceptor for prediction writes.

    The predictor reads its as-of features and writes its rows through ONE
    connection, which in production is Supabase. A wholly fake connection
    broke the as-of lookups; a real Supabase connection would leave rows in
    the live log. So this delegates everything to a real connection except
    `INSERT INTO predictions`, which it records.

    The as-of summaries exist identically in both databases - session 3
    built them that way and a parity gate keeps them so - which is why the
    local corpus connection serves here.
    """

    def __init__(self, real) -> None:
        self._real = real
        self.rows: list[tuple] = []

    def cursor(self):
        return _RecordingCursor(self, self._real.cursor())


class _RecordingCursor:
    def __init__(self, conn: _RecordingConn, real) -> None:
        self._conn = conn
        self._real = real
        self._intercepted = False

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)

    def execute(self, sql, params=None):
        if "INSERT INTO predictions" in sql:
            self._intercepted = True
            self._conn.rows.append(params)
            return None
        self._intercepted = False
        return self._real.execute(sql, params)

    def fetchone(self):
        if self._intercepted:
            return (len(self._conn.rows),)
        return self._real.fetchone()

    def fetchall(self):
        return self._real.fetchall()

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_worker_scores_every_innings_two_ball_of_a_replayed_chase(corpus):
    model = _artifact(corpus)
    client = ReplayClient(corpus, REPLAY_MATCH, speed="instant")
    state = client.get_match_state(REPLAY_MATCH)
    deliveries = client.get_deliveries_since(REPLAY_MATCH, 0)

    guard = _StubGuard()
    sink = _RecordingConn(corpus)
    predictor = LivePredictor(sink, model, guard, log=lambda _m: None)
    written = predictor.observe(client, state, deliveries)

    innings_two = [d for d in deliveries if d.innings == 2]
    assert written == len(innings_two), (
        f"{written} predictions for {len(innings_two)} innings-2 deliveries - "
        "the worker must score every ball of the chase, not a subset"
    )
    # Innings 1 is not scored at all: predict_win_prob is second-innings only
    # and a first-innings score projection is Phase 4.
    assert written < len(deliveries)


def test_the_pin_is_reconfirmed_before_every_write(corpus):
    model = _artifact(corpus)
    client = ReplayClient(corpus, REPLAY_MATCH, speed="instant")
    state = client.get_match_state(REPLAY_MATCH)
    deliveries = client.get_deliveries_since(REPLAY_MATCH, 0)

    guard = _StubGuard()
    predictor = LivePredictor(_RecordingConn(corpus), model, guard, log=lambda _m: None)
    written = predictor.observe(client, state, deliveries)

    # Not "at least once": a promotion between two balls of the same over
    # must be caught, so the guard is consulted per prediction. Its own 15s
    # cache is what keeps that from being a query per ball.
    assert guard.calls == written


def test_rows_carry_the_ball_key_and_the_real_phase(corpus):
    model = _artifact(corpus)
    client = ReplayClient(corpus, REPLAY_MATCH, speed="instant")
    state = client.get_match_state(REPLAY_MATCH)
    deliveries = client.get_deliveries_since(REPLAY_MATCH, 0)

    sink = _RecordingConn(corpus)
    predictor = LivePredictor(sink, model, _StubGuard(), log=lambda _m: None)
    predictor.observe(client, state, deliveries)

    keys = [(r[3], r[4], r[5]) for r in sink.rows]
    assert len(keys) == len(set(keys)), "the ball key must be unique within a match"
    assert all(innings == 2 for innings, _o, _b in keys)

    payloads = [json.loads(r[2]) for r in sink.rows]
    assert all(0.0 <= p["p"] <= 1.0 for p in payloads)
    # match_phase is the literal 'innings2' (section 5.4's vocabulary); the
    # powerplay/middle/death phase the calibrator conditions on lives in the
    # payload, and section 8.5's per-phase breakdown reads it from there.
    assert {p["phase"] for p in payloads} <= {"powerplay", "middle", "death"}
    assert len({p["phase"] for p in payloads}) > 1


def test_it_declines_rather_than_guessing_when_the_batting_side_is_unknown(corpus):
    """The case CricketData actually produces.

    A provider that cannot say who is batting must produce no prediction at
    all. Guessing swaps the two Elo ratings behind elo_diff, which yields a
    confident number about the wrong team - and nothing downstream could
    tell.
    """
    model = _artifact(corpus)
    client = ReplayClient(corpus, REPLAY_MATCH, speed="instant")
    state = client.get_match_state(REPLAY_MATCH)
    deliveries = client.get_deliveries_since(REPLAY_MATCH, 0)

    class _SilentProvider:
        def current_teams(self, match_id):
            return None, None

    sink = _RecordingConn(corpus)
    messages: list[str] = []
    predictor = LivePredictor(sink, model, _StubGuard(), log=messages.append)
    written = predictor.observe(_SilentProvider(), state, deliveries)

    assert written == 0
    assert sink.rows == []
    assert any("which side is batting" in m for m in messages)
    # Once per match, not once per ball.
    assert len(messages) == 1


def test_a_match_with_no_date_is_declined(corpus):
    """The as-of key. Defaulting to today would compute venue and Elo
    features against a corpus that already contains the match being
    predicted - leakage that produces better-looking numbers."""
    model = _artifact(corpus)
    client = ReplayClient(corpus, REPLAY_MATCH, speed="instant")
    state = client.get_match_state(REPLAY_MATCH)
    deliveries = client.get_deliveries_since(REPLAY_MATCH, 0)

    from dataclasses import replace

    sink = _RecordingConn(corpus)
    messages: list[str] = []
    predictor = LivePredictor(sink, model, _StubGuard(), log=messages.append)
    written = predictor.observe(client, replace(state, match_date=None), deliveries)

    assert written == 0
    assert any("match date" in m for m in messages)
