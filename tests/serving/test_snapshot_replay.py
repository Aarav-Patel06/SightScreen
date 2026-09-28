"""A whole match through the real worker, before any real match (session 3).

England v India, 3rd ODI, 2026-07-19 (Cricsheet 1496581) is replayed as the
CricketData responses the worker would have seen: the fixture list three
hours before, then `currentMatches` from the first ball to the result. The
worker picks it, polls innings 1 sparsely, catches the break and the target,
predicts the chase ball by ball, and marks the match complete. `check` then
holds it to the match itself.

A harness that passes a broken worker proves nothing, so the same replay is
run against deliberately broken ones, and each must fail.

The scorer is a stub: this is about the worker's plumbing, which the real
artifact does not change (tests/serving/test_live_predictor.py scores with
it). Runs on the writable test database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import ingest.cricketdata as cricketdata
import serving.live_loop as live_loop
from ingest.snapshot_replay import HarnessFailure, Timeline, check, load_cricsheet, run_replay

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "cricsheet" / "1496581.json"
START = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)
VERSION = "replay-stub"


class _Guard:
    def confirm(self, _conn):
        pass


@pytest.fixture
def seeded(conn, monkeypatch):
    with conn.cursor() as cur:
        for name in ("England", "India"):
            cur.execute("INSERT INTO teams (name, full_member) VALUES (%s, true)", (name,))
        cur.execute("INSERT INTO venues (name, city) VALUES ('Lord''s', 'London')")
        cur.execute(
            "INSERT INTO model_versions (model_version, model_type, trained_at, train_end_date, artifact_path, is_active) "
            "VALUES (%s, 'stub', now(), '2026-01-01', 'none', true) ON CONFLICT DO NOTHING",
            (VERSION,),
        )
    # The scorer only: every other step is the worker's own.
    monkeypatch.setattr(live_loop, "predict_win_prob", lambda _artifact, row, _as_of: 0.5)
    yield conn
    # model_versions is not in conftest's reset list, and this row is ACTIVE:
    # left behind, it would be the "active model" every later test finds.
    with conn.cursor() as cur:
        cur.execute("DELETE FROM predictions WHERE model_version = %s", (VERSION,))
        cur.execute("DELETE FROM model_versions WHERE model_version = %s", (VERSION,))


def _replay(conn, **kwargs):
    timeline = Timeline(load_cricsheet(FIXTURE), START)
    return run_replay(conn, timeline, {"model_version": VERSION, "artifact": None}, _Guard(), **kwargs)


def test_a_whole_match_through_the_worker(seeded):
    result = _replay(seeded)
    summary = check(seeded, result)

    assert result.picks and result.picks[0]["picked"][0]["provider_id"] == result.timeline.provider_id
    assert result.picks[0]["picked"][0]["tier"] == 0, "Full Member v Full Member"
    print(f"replay: {summary}")


def test_joining_mid_chase_scores_from_the_exact_state_and_marks_the_gap(seeded):
    timeline = Timeline(load_cricsheet(FIXTURE), START)
    mid_chase = timeline.chase_starts + timedelta(minutes=45)
    result = _replay(seeded, begin=mid_chase)

    check(seeded, result, joined_late=True)


# --- the harness must catch a broken worker ------------------------------------


def _swap_innings_labels(endpoint, body):
    if endpoint == "currentMatches":
        for match in body["data"]:
            labels = [s["inning"] for s in match["score"]]
            if len(labels) == 2:
                match["score"][0]["inning"], match["score"][1]["inning"] = labels[1], labels[0]
    return body


def test_it_catches_a_worker_that_misses_the_innings_break(seeded, monkeypatch):
    monkeypatch.setattr(cricketdata, "INTERVAL_BREAK", cricketdata.INTERVAL_SPARSE)
    monkeypatch.setattr(cricketdata, "NEAR_END_BALLS", -1)
    monkeypatch.setattr(cricketdata, "NEAR_END_WICKETS", 11)
    with pytest.raises(HarnessFailure, match="legal balls between two polls"):
        check(seeded, _replay(seeded))


def test_it_catches_a_worker_that_reads_the_batting_side_backwards(seeded):
    with pytest.raises(HarnessFailure, match="batting_team_id"):
        check(seeded, _replay(seeded, mutate=_swap_innings_labels))


def test_it_catches_a_worker_that_never_marks_the_match_complete(seeded, monkeypatch):
    monkeypatch.setattr(live_loop.LivePredictor, "record_status", lambda self, match_id, status: False)
    with pytest.raises(HarnessFailure, match="not 'complete'"):
        check(seeded, _replay(seeded))


def test_it_catches_a_late_join_scored_from_zero(seeded, monkeypatch):
    """The session-3 bug: the catch-up never reached the builder."""
    real = cricketdata.CricketDataClient.get_deliveries_since

    def no_catch_up(self, match_id, last_ball):
        return [] if last_ball == 0 else real(self, match_id, last_ball)

    monkeypatch.setattr(cricketdata.CricketDataClient, "get_deliveries_since", no_catch_up)
    timeline = Timeline(load_cricsheet(FIXTURE), START)
    with pytest.raises(HarnessFailure, match="over-boundary"):
        check(seeded, _replay(seeded, begin=timeline.chase_starts + timedelta(minutes=45)), joined_late=True)


def test_it_catches_a_worker_that_overspends(seeded, monkeypatch):
    monkeypatch.setattr(cricketdata, "INTERVAL_SPARSE", 15.0)
    monkeypatch.setattr(cricketdata, "INTERVAL_BREAK", 15.0)
    with pytest.raises(HarnessFailure, match="provider calls"):
        check(seeded, _replay(seeded))
