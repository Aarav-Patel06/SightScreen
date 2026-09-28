"""Joining a match already in progress (session 3).

The adapter emits everything bowled before the worker's first sighting as one
INFERRED catch-up span, and `poll()` returns only what came after it. The
catch-up used to reach nothing: `LivePredictor` built a fresh
`IncrementalMatchStateBuilder` on the first NEW ball, so the chase started
from 0/0. Joined at 96/4 chasing 100, the first prediction read score 0,
wickets 0, 100 required - and every later one was wrong by the same amount.
A worker restart mid-chase is the same path.

The decision: the catch-up is NOT scored (its per-ball states are invented -
the span puts all its runs on its last ball), but it goes through the
builder so the state from the join onward is exact. The gap is the first
live row's `balls_bowled > 0`.

No database, no model: the scorer is replaced so the test sees the row it
was handed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import serving.live_loop as live_loop
from ingest.cricketdata import InningsSnapshot, MatchSnapshot, reconstruct
from ingest.live_client import MatchState
from serving.live_loop import LivePredictor


def _snapshot(*innings: InningsSnapshot) -> MatchSnapshot:
    return MatchSnapshot(
        provider_id="p", name="A v B", format="T20", status="", venue="", teams=("A", "B"),
        innings=innings, started=True, ended=False, reduced_overs=None, dls_target=None,
        observed_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )


FIRST = InningsSnapshot(runs=99, wickets=10, balls=109, batting_team="B")


class _JoinedClient:
    """First sighting at 96/4 off 14.4 chasing 100; then one ball (a single)."""

    def __init__(self) -> None:
        joined = _snapshot(FIRST, InningsSnapshot(96, 4, 88, "A"))
        after = _snapshot(FIRST, InningsSnapshot(97, 4, 89, "A"))
        self.catch_up = reconstruct(None, joined)
        self.new = reconstruct(joined, after)

    def get_deliveries_since(self, _match_id, last_ball):
        return (self.catch_up + self.new)[last_ball:]

    def current_teams(self, _match_id):
        return 1, 2


class _Cursor:
    def __init__(self, inserts):
        self._inserts = inserts

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        if "INSERT INTO predictions" in sql:
            self._inserts.append(params)

    def fetchone(self):
        return (1,)


class _Conn:
    def __init__(self):
        self.inserts: list = []

    def cursor(self):
        return _Cursor(self.inserts)


class _Guard:
    def confirm(self, _conn):
        pass


STATE = MatchState(
    match_id=77, status="live", innings=2, ball_count=0, target_runs=100, target_overs=20.0,
    venue_id=None, format="T20", team_a=1, team_b=2, toss_winner=None, toss_decision=None,
    winner=None, match_date=date(2026, 9, 30),
)


def test_a_late_join_scores_from_the_exact_state_at_the_join(monkeypatch):
    rows = []
    monkeypatch.setattr(live_loop, "compute_as_of_features", lambda *a, **k: {})
    monkeypatch.setattr(live_loop, "predict_win_prob", lambda _art, row, _as_of: rows.append(row) or 0.5)
    client = _JoinedClient()
    conn = _Conn()
    predictor = LivePredictor(conn, {"model_version": "v", "artifact": None}, _Guard(), log=lambda _m: None)

    written = predictor.observe(client, STATE, client.new)

    assert written == 1, "the catch-up span is not scored; only the ball after the join"
    first = rows[0]
    assert (first.score, first.wickets, first.balls_bowled) == (96, 4, 88)
    assert first.runs_required == 4


def test_a_join_after_the_winning_ball_still_decides_the_chase(monkeypatch):
    """A restart after the last ball but before the provider's matchEnded:
    the catch-up alone decides the chase."""
    monkeypatch.setattr(live_loop, "compute_as_of_features", lambda *a, **k: {})
    monkeypatch.setattr(live_loop, "predict_win_prob", lambda *_a: 0.5)
    client = _JoinedClient()
    won = _snapshot(FIRST, InningsSnapshot(100, 4, 89, "A"))
    client.catch_up = reconstruct(None, won)
    client.new = []
    predictor = LivePredictor(_Conn(), {"model_version": "v", "artifact": None}, _Guard(), log=lambda _m: None)

    predictor.observe(client, STATE, [])

    assert 77 in predictor.decided
