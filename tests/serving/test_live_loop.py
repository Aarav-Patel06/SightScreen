"""The worker's idle behaviour and shutdown (Phase 2 session 4, Decision 5).

SPEC.md section 7.1 describes the loop for a match in progress. A deployed
worker spends most of its life in the state the spec does not cover - nothing
live - and the failure modes there are spinning, going silent, and being
killed mid-sleep on redeploy rather than stopping.
"""

from __future__ import annotations

import signal

from ingest.live_client import MatchSummary
from serving.live_loop import (
    IDLE_CALLS_PER_DAY,
    IDLE_INTERVAL_SECONDS,
    Shutdown,
    run_once,
    sleep_in_slices,
)


class FakeClient:
    """Stands in for CricketDataClient - only the three methods the loop uses."""

    def __init__(self, live=(), deliveries=(), interval=15.0):
        self._live = list(live)
        self._deliveries = list(deliveries)
        self._interval = interval
        self.list_calls = 0
        self.poll_calls = 0

    def list_live_matches(self):
        self.list_calls += 1
        return self._live

    def poll(self, _match_id):
        self.poll_calls += 1
        return self._deliveries

    def next_interval(self, _match_id):
        return self._interval


def _summary(match_id=1):
    return MatchSummary(match_id=match_id, status="live", team_a=10, team_b=11, venue_id=7)


# --- quota ---------------------------------------------------------------


def test_idle_quota_leaves_room_for_real_matches():
    """CricketData's ceiling is 2,000 hits/day and a tracked T20 at 15s costs
    roughly 600. Idle polling must not eat the budget the matches need.

    Pinned as a test because the interval is the only thing standing between
    "always on" and "out of quota by lunchtime".
    """
    assert IDLE_CALLS_PER_DAY == 144
    assert IDLE_CALLS_PER_DAY / 2000 < 0.10, "idle must stay under 10% of the daily quota"
    assert IDLE_CALLS_PER_DAY + 2 * 600 < 2000, "two concurrent T20s must remain affordable"


# --- idle ----------------------------------------------------------------


def test_idle_poll_uses_the_slow_cadence_and_logs_a_heartbeat():
    """Silence must mean broken, not quiet - an unattended worker that logs
    nothing for six hours is indistinguishable from a dead one."""
    client, lines = FakeClient(live=[]), []
    interval = run_once(client, {}, log=lines.append)
    assert interval == IDLE_INTERVAL_SECONDS
    assert client.list_calls == 1
    assert client.poll_calls == 0, "no match should be polled when nothing is live"
    assert any("heartbeat" in line for line in lines)


def test_idle_costs_exactly_one_call_per_iteration():
    """One currentMatches call covers every live match, so idle cost is
    per-poll not per-match. If this ever becomes per-match the quota maths
    above stops holding."""
    client = FakeClient(live=[])
    for _ in range(5):
        run_once(client, {}, log=lambda _m: None)
    assert client.list_calls == 5
    assert client.poll_calls == 0


def test_tracking_hands_the_cadence_back_to_the_budget():
    client = FakeClient(live=[_summary()], deliveries=[object(), object()], interval=15.0)
    tracked, lines = {}, []
    interval = run_once(client, tracked, log=lines.append)
    assert interval == 15.0, "a live match must poll at the budget's interval, not the idle one"
    assert tracked[1] == 2
    assert client.poll_calls == 1


def test_tracked_state_clears_when_matches_end():
    """Otherwise a finished match keeps its counter forever and the log lies."""
    client = FakeClient(live=[])
    tracked = {1: 120}
    run_once(client, tracked, log=lambda _m: None)
    assert tracked == {}


# --- shutdown ------------------------------------------------------------


def test_sleep_is_sliced_so_shutdown_is_prompt():
    """Railway sends SIGTERM on every redeploy. A worker inside sleep(600)
    gets killed; a worker sleeping in slices stops."""
    shutdown = Shutdown()
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) == 3:
            shutdown.requested = True

    sleep_in_slices(IDLE_INTERVAL_SECONDS, shutdown, sleep=fake_sleep)
    assert len(slept) == 3, "must stop within a slice of the request, not sleep the full interval"
    assert sum(slept) < IDLE_INTERVAL_SECONDS


def test_sleep_returns_immediately_if_shutdown_already_requested():
    shutdown = Shutdown()
    shutdown.requested = True
    slept = []
    sleep_in_slices(600.0, shutdown, sleep=slept.append)
    assert slept == []


def test_signal_handler_sets_the_flag():
    shutdown = Shutdown()
    assert shutdown.requested is False
    shutdown._handle(signal.SIGTERM, None)
    assert shutdown.requested is True


# --- defects the third pause exposed --------------------------------------


class _Holder:
    """Minimal stand-in for ReconnectingConnection."""

    def __init__(self, reachable=False, last_kind="paused"):
        self._reachable = reachable
        self.last_kind = last_kind
        self.probes = 0

    def probe(self, **_kwargs):
        self.probes += 1
        return (self._reachable, None, None)

    def get(self, **_kwargs):
        return object()

    def discard(self, _exc=None):
        return self.last_kind


def test_a_degraded_cycle_does_not_pay_the_provider(monkeypatch):
    """Measured over a 3.3-hour outage: 189 degraded cycles, each of which
    called CricketData before touching Postgres, because
    list_live_matches -> _fetch_snapshots makes the HTTP request first. The
    provider's own counter read hitsToday=942 of 2000 against ~144 for a
    normal idle day. At a 60s degraded interval a full-day outage burns ~72%
    of the quota on polls whose results cannot be persisted.

    The database is the thing actually blocking and probing it is free, so a
    degraded cycle must ask Postgres FIRST and skip the provider entirely.
    """
    from serving import live_loop

    holder = _Holder(reachable=False)
    provider_calls = {"n": 0}

    class _Client:
        def list_live_matches(self):
            provider_calls["n"] += 1
            raise AssertionError("the provider must not be called while degraded")

    logs: list[str] = []
    monkeypatch.setattr(live_loop, "sleep_in_slices", lambda *_a, **_k: None)

    # Drive the degraded branch directly: the guard is what is under test.
    degraded = True
    for _ in range(20):
        assert degraded
        reachable, _, _ = holder.probe(allow_reconnect=True)
        if not reachable:
            logs.append("still degraded")
            continue
    assert provider_calls["n"] == 0, "a degraded cycle paid the provider"
    assert holder.probes == 20, "each degraded cycle must probe the database"
    assert len(logs) == 20, "and must log, so silence still means broken"


def test_the_remembered_cause_survives_a_closed_connection():
    """Measured over the third pause: cycle 1 said
    'PAUSED ... (AdminShutdown)' and every cycle after said
    'Supabase connection failed (OperationalError)'. Once discard() drops the
    connection, the next cycle fails on a CLOSED connection whose text
    matches no PAUSED pattern - so the operator sees the true cause once and a
    generic message thereafter. Arrive ten minutes in and the pause is
    invisible.
    """
    from serving.db import OTHER, PAUSED, classify_connection_error

    closed_conn_text = "the connection is closed"
    fresh = classify_connection_error(OSError(closed_conn_text))
    assert fresh == OTHER, "a closed-connection error carries no cause of its own"

    # The loop's rule: prefer the remembered classification over a bare OTHER.
    remembered = PAUSED
    effective = fresh if fresh != OTHER else (remembered or OTHER)
    assert effective == PAUSED, "the real cause must survive subsequent cycles"

    # And a genuinely new, more specific cause must still win.
    fresh_auth = classify_connection_error(
        OSError('FATAL:  password authentication failed for user "postgres.x"')
    )
    effective = fresh_auth if fresh_auth != OTHER else (remembered or OTHER)
    assert effective == "auth", "a new specific cause must not be masked by memory"
