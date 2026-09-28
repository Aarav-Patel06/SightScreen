"""One iteration of the worker once it picks its own matches (session 3).

Idle, the only provider call is the hourly fixture check: currentMatches is
not polled until a picked match is due, PRE_START_LEAD before its start. The
heartbeat is still logged, for free. Driven with a fake clock: step() returns
how long to wait and the test advances time by exactly that.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ingest.cricketdata import INTERVAL_SPARSE, LiveBudget
from ingest.match_pick import Team
from serving.live_loop import (
    FIXTURE_CALLS_PER_DAY,
    IDLE_INTERVAL_SECONDS,
    PRE_START_LEAD,
    Subscriptions,
    Worker,
)

T0 = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.budget = LiveBudget(clock=lambda: T0)
        self.subscribed = None
        self.fixture_calls = 0
        self.live_calls = 0
        self.ended: set[str] = set()

    def list_fixtures(self):
        self.fixture_calls += 1
        self.budget.observe({"info": {"hitsToday": 10, "hitsLimit": 2000}})
        return self.rows

    def list_live_matches(self):
        self.live_calls += 1
        return []

    def snapshot_of(self, provider_id):
        if provider_id in self.ended:
            class _Ended:
                started = True
                ended = True
            return _Ended()
        return None


def _worker(rows, picks=None):
    client = _Client(rows)
    subs = Subscriptions(lambda name: Team(1 if name == "India" else 2, True), log=lambda _m: None)
    worker = Worker(client, subs, predictor=None, log=lambda _m: None, record_pick=(picks if picks is not None else []).append)
    return client, worker


def _odi(start):
    return {
        "id": "odi", "dateTimeGMT": start.strftime("%Y-%m-%dT%H:%M:%S"), "matchType": "odi", "ms": "fixture",
        "series": "West Indies tour of India, 2026", "t1": "India [IND]", "t2": "West Indies [WI]", "status": "",
    }


def test_an_idle_day_costs_only_the_hourly_fixture_check():
    client, worker = _worker([])
    lines: list[str] = []
    worker._log = lines.append
    now = T0
    while now < T0 + timedelta(days=1):
        interval = worker.step(now)
        assert interval <= IDLE_INTERVAL_SECONDS, "the heartbeat must keep coming"
        now += timedelta(seconds=interval)

    assert client.fixture_calls == FIXTURE_CALLS_PER_DAY == 24
    assert client.live_calls == 0
    assert sum("heartbeat" in line for line in lines) >= 144


def test_a_picked_match_is_polled_from_shortly_before_its_start():
    start = T0 + timedelta(hours=2, minutes=3)
    client, worker = _worker([_odi(start)])
    now = T0
    while now < start - PRE_START_LEAD:
        now += timedelta(seconds=worker.step(now))
        if now < start - PRE_START_LEAD:
            assert client.live_calls == 0, f"polled at {now} before the match was due"

    assert now == start - PRE_START_LEAD, "the wait lands on the due time, not past it"
    interval = worker.step(now)
    assert client.live_calls == 1
    assert interval == INTERVAL_SPARSE, "not started yet: the sparse cadence"


def test_the_pick_is_recorded_once():
    picks: list[dict] = []
    client, worker = _worker([_odi(T0 + timedelta(hours=5))], picks)
    now = T0
    for _ in range(3):
        now += timedelta(seconds=worker.step(now))
        now = max(now, T0 + timedelta(hours=len(picks) + 1))

    assert len(picks) == 1
    assert picks[0]["picked"][0]["provider_id"] == "odi"


def test_a_finished_match_stops_being_polled():
    start = T0 + timedelta(minutes=5)
    client, worker = _worker([_odi(start)])
    worker.step(T0)
    assert client.live_calls == 1

    client.ended.add("odi")
    client.rows = []
    worker.step(T0 + timedelta(minutes=5))  # polls once more, then releases the match
    calls = client.live_calls
    worker.step(T0 + timedelta(minutes=10))
    assert client.live_calls == calls
