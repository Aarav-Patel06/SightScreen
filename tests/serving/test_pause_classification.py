"""Supabase pause resilience (SPEC.md section 13, Phase 2 session 4,
Decision 4).

Section 13 makes this a precondition for deploying: free projects pause after
~7 days idle and "present as connection errors that look like code bugs ...
make the worker log a distinguishable error on a paused project."

These are unit tests over the exception text. They do NOT replace the real
test - pausing the actual project and watching the worker handle it - which
runs in session 4b, because restoring a paused project takes minutes and the
whole database is unavailable meanwhile. What they do is pin the
classification so the real test has something to fail against.
"""

from __future__ import annotations

import random

import pytest

from serving.db import (
    AUTH,
    PROBE_CACHE_SECONDS,
    RECONNECT_COOLDOWN_CAP_SECONDS,
    ConnectionCoolingDown,
    ReconnectingConnection,
    OTHER,
    PAUSED,
    TIMEOUT,
    UNREACHABLE,
    AuthenticationFailed,
    backoff_delay,
    classify_connection_error,
    connect_with_backoff,
    describe,
)

# The text a genuinely paused project returns, captured on 2026-09-16 by
# pausing the real Supabase project. Note it is NOT the phrasing
# docs/phase1-closeout.md:170-174 recorded ("Tenant or user not found") - the
# real reply uses a slash and interpolates the username. The literal from the
# docs never matched, so a paused project classified as OTHER and logged
# "Supabase connection failed" instead of naming the pause, defeating the
# whole point of SPEC.md section 13's requirement. Only pausing the project
# could have found that; the code faithfully implemented a misremembered
# string.
PAUSED_TEXT = (
    'connection failed: connection to server at "3.111.105.85", port 5432 failed: '
    "FATAL:  (ENOTFOUND) tenant/user postgres.xrvgmjmvgmtepgryyulk not found"
)
LEGACY_PAUSED_TEXT = "connection failed: FATAL:  Tenant or user not found"
AUTH_TEXT = 'connection failed: FATAL:  password authentication failed for user "postgres.abc"'
DNS_TEXT = 'connection failed: could not translate host name "aws-0-x.pooler.supabase.com"'
TIMEOUT_TEXT = "connection timeout expired"


@pytest.mark.parametrize(
    "text,expected",
    [
        (PAUSED_TEXT, PAUSED),
        (LEGACY_PAUSED_TEXT, PAUSED),
        ("Project is paused", PAUSED),
        (AUTH_TEXT, AUTH),
        ('FATAL:  no pg_hba.conf entry for host "1.2.3.4"', AUTH),
        (DNS_TEXT, UNREACHABLE),
        ("connection refused", UNREACHABLE),
        (TIMEOUT_TEXT, TIMEOUT),
        ("connection timed out", TIMEOUT),
        ("something nobody has seen before", OTHER),
    ],
)
def test_classification(text, expected):
    assert classify_connection_error(OSError(text)) == expected


def test_paused_is_not_misread_as_auth():
    """The whole point. "Tenant or user not found" reads like a credentials
    problem and is not one - if this ordering ever flips, a paused project
    would kill the worker instead of making it wait."""
    assert classify_connection_error(OSError(PAUSED_TEXT)) == PAUSED
    assert "PAUSED" in describe(PAUSED).upper()


def test_backoff_grows_and_is_capped():
    rng = random.Random(0)
    delays = [backoff_delay(attempt, rng=rng) for attempt in range(1, 12)]
    assert delays[0] < delays[3] < delays[6]
    assert all(delay <= 300.0 * 1.25 + 1e-9 for delay in delays)


def test_backoff_is_jittered():
    """Identical attempt numbers must not produce identical delays, or a
    restart storm would retry in lockstep."""
    rng = random.Random(1)
    assert len({round(backoff_delay(5, rng=rng), 6) for _ in range(20)}) > 1


class _Boom:
    """A fake psycopg.connect that fails a given number of times."""

    def __init__(self, text, fail_times):
        self.text = text
        self.remaining = fail_times
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise OSError(self.text)
        return "connection"


def test_paused_retries_until_it_succeeds(monkeypatch):
    boom = _Boom(PAUSED_TEXT, fail_times=3)
    monkeypatch.setattr("serving.db.psycopg.connect", boom)
    slept, logged = [], []
    result = connect_with_backoff(
        "postgresql://x", sleep=slept.append, log=logged.append
    )
    assert result == "connection"
    assert boom.calls == 4
    assert len(slept) == 3
    assert all("paused" in line.lower() for line in logged)


def test_auth_failure_exits_immediately(monkeypatch):
    """Retrying a wrong password forever is a silent outage: the service
    looks alive, the logs scroll, and nothing works."""
    boom = _Boom(AUTH_TEXT, fail_times=99)
    monkeypatch.setattr("serving.db.psycopg.connect", boom)
    slept = []
    with pytest.raises(AuthenticationFailed):
        connect_with_backoff("postgresql://x", sleep=slept.append, log=lambda _m: None)
    assert boom.calls == 1
    assert slept == []


def test_unknown_errors_retry_rather_than_crash_loop(monkeypatch):
    boom = _Boom("some novel failure", fail_times=2)
    monkeypatch.setattr("serving.db.psycopg.connect", boom)
    assert connect_with_backoff(
        "postgresql://x", sleep=lambda _s: None, log=lambda _m: None
    ) == "connection"


def test_max_attempts_gives_up_when_asked(monkeypatch):
    """The worker retries forever; a startup probe may not want to."""
    boom = _Boom(PAUSED_TEXT, fail_times=99)
    monkeypatch.setattr("serving.db.psycopg.connect", boom)
    with pytest.raises(OSError):
        connect_with_backoff(
            "postgresql://x", max_attempts=3, sleep=lambda _s: None, log=lambda _m: None
        )
    assert boom.calls == 3


def test_the_four_causes_are_mutually_distinguishable():
    """SPEC.md section 13 asks for a paused project to be distinguishable.
    These are the four things that actually present as a failed connection,
    and each must land in its own class - otherwise the log sends you to the
    wrong place.

    Only AUTH exits. A pause, a network fault and a timeout are all things
    that resolve themselves; a wrong password is not, and retrying it forever
    is a silent outage.
    """
    causes = {
        "pause": PAUSED_TEXT,
        "wrong password": AUTH_TEXT,
        "network failure": DNS_TEXT,
        "genuine timeout": TIMEOUT_TEXT,
    }
    classes = {name: classify_connection_error(OSError(t)) for name, t in causes.items()}
    assert len(set(classes.values())) == 4, f"causes collapsed together: {classes}"
    assert classes["wrong password"] == AUTH
    assert [c for c in classes.values() if c == AUTH] == [AUTH], "exactly one cause may exit"


# --- the health endpoint must ask, not remember ----------------------------


class _FakeDatabase:
    """Stands in for ReconnectingConnection in the two states that matter."""

    def __init__(self, reachable, kind=None):
        self._reachable = reachable
        self._kind = kind

    def probe(self, **_kwargs):
        return (True, None, None) if self._reachable else (False, self._kind, "boom")

    last_kind = None


def test_health_reports_degraded_when_the_database_is_gone(monkeypatch):
    """The defect this covers was observed live, not imagined.

    During the real Supabase pause the deployed /health returned
    `status: ok` with an uptime of 2427s, because every value in the response
    was collected at startup and nothing in the handler touched the
    connection. A monitor would have shown green for the whole outage - worse
    than having no health check, because a health check is trusted.

    Cannot be re-triggered against the live service without pausing the
    project again, so it is pinned here.
    """
    from fastapi import Response

    from serving import app as app_module

    monkeypatch.setattr(app_module.startup, "refresh_endpoint_state", lambda *_a, **_k: None)
    saved = dict(app_module._state)
    try:
        app_module._state.clear()
        app_module._state.update(
            {
                "facts": {"service_role": "api", "model_version": "winprob2-20260910"},
                "database": _FakeDatabase(reachable=False, kind=PAUSED),
                "model": {},
            }
        )
        response = Response()
        body = app_module.health(response)
        assert response.status_code == 503, "a dead database must not report HTTP 200"
        assert body["status"] == "degraded"
        assert body["db_reachable"] is False
        assert body["db_status"] == PAUSED
        assert "PAUSED" in body["db_detail"].upper()
    finally:
        app_module._state.clear()
        app_module._state.update(saved)


def test_health_reports_ok_when_the_database_answers(monkeypatch):
    from fastapi import Response

    from serving import app as app_module

    # The reference re-check needs a live connection; it has its own tests.
    monkeypatch.setattr(app_module.startup, "refresh_reference_state", lambda *_a, **_k: None)
    monkeypatch.setattr(app_module.startup, "refresh_endpoint_state", lambda *_a, **_k: None)

    saved = dict(app_module._state)
    try:
        app_module._state.clear()
        app_module._state.update(
            {
                "facts": {"service_role": "api"},
                "database": _FakeDatabase(reachable=True),
                "conn": object(),
                "model": {},
            }
        )
        response = Response()
        body = app_module.health(response)
        assert body["status"] == "ok"
        assert body["db_reachable"] is True
        assert response.status_code in (None, 200)
    finally:
        app_module._state.clear()
        app_module._state.update(saved)


# --- the two concerns raised before the second pause -----------------------


def _always_fails(text=PAUSED_TEXT):
    state = {"calls": 0}

    def connect(*_a, **_k):
        state["calls"] += 1
        raise OSError(text)

    return connect, state


def test_reconnect_does_not_storm_a_paused_project(monkeypatch):
    """Traffic arriving during an outage must not become connection attempts.

    Without the holder's own cooldown, every request would open a fresh
    attempt against a project that cannot serve it. Railway's health checker
    polls frequently on its own, and SPEC.md section 2.4 warns the free-tier
    pool ceiling is low and shared - so speculative connections are expensive
    even when they succeed.
    """
    connect, state = _always_fails()
    monkeypatch.setattr("serving.db.psycopg.connect", connect)
    holder = ReconnectingConnection("postgresql://x", log=lambda _m: None)

    refused = 0
    for _ in range(50):
        try:
            holder.get(max_attempts=1)
        except ConnectionCoolingDown:
            refused += 1
        except Exception:
            pass

    assert state["calls"] == 1, f"50 requests produced {state['calls']} connection attempts"
    assert refused == 49
    assert holder.last_kind == PAUSED, "the real cause must survive the cooldown"


def test_the_cooldown_grows_and_is_capped(monkeypatch):
    connect, _ = _always_fails()
    monkeypatch.setattr("serving.db.psycopg.connect", connect)
    holder = ReconnectingConnection("postgresql://x", log=lambda _m: None)

    seen = []
    for _ in range(10):
        holder._next_attempt_at = 0.0  # let each attempt through
        try:
            holder.get(max_attempts=1)
        except Exception:
            pass
        seen.append(holder._cooldown())

    assert seen[0] < seen[2] < seen[4], f"cooldown must grow: {seen}"
    assert max(seen) <= RECONNECT_COOLDOWN_CAP_SECONDS
    assert seen[-1] == RECONNECT_COOLDOWN_CAP_SECONDS, "must reach and hold the cap"


def test_the_two_backoff_layers_add_rather_than_multiply(monkeypatch):
    """The holder's cooldown and a caller's retry loop must not compound.

    With max_attempts=1 the holder does not sleep at all - it raises and lets
    the caller decide when to come back. So the worst case per worker cycle is
    the loop's own interval plus one connection attempt, not the product of
    the two.
    """
    connect, state = _always_fails()
    monkeypatch.setattr("serving.db.psycopg.connect", connect)
    holder = ReconnectingConnection("postgresql://x", log=lambda _m: None)
    slept = []
    monkeypatch.setattr("serving.db.time.sleep", slept.append)

    try:
        holder.get(max_attempts=1)
    except Exception:
        pass
    assert slept == [], "max_attempts=1 must not sleep inside the holder"
    assert state["calls"] == 1


def test_probe_reuses_the_connection_and_never_opens_one(monkeypatch):
    """SPEC.md section 2.4: do not open connections speculatively. A health
    endpoint that opens one per call can exhaust the pool it is reporting on -
    its own outage."""
    connect, state = _always_fails()
    monkeypatch.setattr("serving.db.psycopg.connect", connect)
    holder = ReconnectingConnection("postgresql://x", log=lambda _m: None)

    for _ in range(20):
        reachable, _, detail = holder.probe(now=0.0)
        assert reachable is False
        assert detail == "no open connection"
    assert state["calls"] == 0, "probe must never open a connection"


def test_probe_caches_so_a_burst_costs_one_round_trip():
    class _Conn:
        closed = False

        def __init__(self):
            self.queries = 0

        def cursor(self):
            outer = self

            class _Cur:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_a):
                    return False

                def execute(self_inner, _sql):
                    outer.queries += 1

            return _Cur()

    holder = ReconnectingConnection("postgresql://x", log=lambda _m: None)
    fake = _Conn()
    holder._conn = fake

    for _ in range(25):
        assert holder.probe(now=100.0)[0] is True
    assert fake.queries == 1, f"25 health checks cost {fake.queries} round trips"

    # Past the cache window it asks again.
    holder.probe(now=100.0 + PROBE_CACHE_SECONDS + 0.1)
    assert fake.queries == 2


def test_health_reports_degraded_when_reference_data_goes_stale(monkeypatch):
    """Freshness is re-checked on a timer, not trusted from boot.

    A container up for 20.5 hours was still reporting the reference age it
    measured at startup - so the 14-day staleness limit could never fire on a
    long-running service. Observed on the deployed api, which said
    reference_age_days=1 when the real answer was 2.
    """
    from fastapi import Response

    from serving import app as app_module

    monkeypatch.setattr(
        app_module.startup,
        "refresh_reference_state",
        lambda *_a, **_k: "reference tables were last synced 2026-09-01 (20 days ago, limit 14)",
    )
    monkeypatch.setattr(app_module.startup, "refresh_endpoint_state", lambda *_a, **_k: None)
    saved = dict(app_module._state)
    try:
        app_module._state.clear()
        app_module._state.update(
            {
                "facts": {"service_role": "api"},
                "database": _FakeDatabase(reachable=True),
                "conn": object(),
                "model": {},
            }
        )
        response = Response()
        body = app_module.health(response)
        assert response.status_code == 503
        assert body["status"] == "degraded"
        assert "20 days ago" in body["db_detail"]
    finally:
        app_module._state.clear()
        app_module._state.update(saved)


def test_the_reference_recheck_is_rate_limited(monkeypatch):
    """It must not hit the database on every health call - Railway's checker
    polls frequently and SPEC.md section 2.4's pool is small."""
    from serving import startup as startup_module

    calls = {"n": 0}

    def fake_fresh(_conn):
        calls["n"] += 1
        return {"age_days": 1, "newest_breakpoint": "2026-08-24", "corpus_age_days": 24}

    monkeypatch.setattr(startup_module, "assert_reference_fresh", fake_fresh)
    monkeypatch.setattr(startup_module, "_reference_checked_at", 0.0)
    monkeypatch.setattr(startup_module, "_reference_error", None)

    facts: dict = {}
    for i in range(30):
        startup_module.refresh_reference_state(object(), facts, now=1000.0 + i)
    assert calls["n"] == 1, f"30 health calls caused {calls['n']} freshness queries"

    startup_module.refresh_reference_state(
        object(), facts, now=1000.0 + startup_module.REFERENCE_RECHECK_SECONDS + 1
    )
    assert calls["n"] == 2


# --- fixes for the defects the second pause exposed ------------------------


def test_probe_records_the_classification_itself(monkeypatch):
    """Measured defect: at t+6.5s into a real pause /health reported
    db_status=unknown, because once psycopg closed the connection probe()
    returned "no open connection" with no classification, and only /predict's
    discard() ever set last_kind. A diagnostic that degrades exactly when it
    is needed is the same failure shape as cached-at-startup."""

    class _Dead:
        closed = False

        def cursor(self):
            raise OSError(PAUSED_TEXT)

    holder = ReconnectingConnection("postgresql://x", log=lambda _m: None)
    holder._conn = _Dead()

    reachable, kind, _ = holder.probe(now=0.0)
    assert reachable is False
    assert kind == PAUSED
    assert holder.last_kind == PAUSED, "probe must record the cause, not rely on a caller"

    # Now the connection is gone entirely: the classification must survive.
    holder._conn = None
    reachable, kind, detail = holder.probe(now=100.0)
    assert reachable is False
    assert kind == PAUSED, f"classification lost once the connection closed: {kind}"


def test_probe_can_heal_on_demand_but_only_within_the_cooldown(monkeypatch):
    """Measured defect: with the database restored and no traffic, /health
    reported 503 degraded indefinitely - probe() never reconnected and
    /predict was the only caller of get(). One bounded attempt, governed by
    the same cooldown, so healing cannot become a storm."""
    attempts = {"n": 0}

    class _Live:
        closed = False

        def cursor(self):
            class _Cur:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_a):
                    return False

                def execute(self_inner, _sql):
                    return None

            return _Cur()

    def connect(*_a, **_k):
        attempts["n"] += 1
        return _Live()

    monkeypatch.setattr("serving.db.psycopg.connect", connect)
    holder = ReconnectingConnection("postgresql://x", log=lambda _m: None)

    assert holder.probe(now=0.0)[0] is False, "no connection yet, and not asked to heal"
    assert attempts["n"] == 0, "probe must not reconnect unless asked"

    reachable, _, _ = holder.probe(now=10.0, allow_reconnect=True)
    assert reachable is True
    assert attempts["n"] == 1


def test_probe_healing_respects_the_cooldown(monkeypatch):
    connect, state = _always_fails()
    monkeypatch.setattr("serving.db.psycopg.connect", connect)
    holder = ReconnectingConnection("postgresql://x", log=lambda _m: None)
    for i in range(30):
        holder.probe(now=float(i), allow_reconnect=True)
    assert state["calls"] == 1, f"healing storm: {state['calls']} attempts in 30 probes"


# --- the model pin on the prediction path ---------------------------------


def test_the_version_guard_refuses_after_a_promotion():
    """SPEC.md section 8.4 promotes models while containers are alive, and
    section 5.4's predictions.model_version is what Phase 3's accuracy page
    groups calibration by. A container that keeps serving after a promotion
    writes rows attributed to a model that did not produce them, with an audit
    trail that looks clean."""
    from models.artifact import ActiveVersionGuard, ModelVersionMismatch

    active = {"version": "winprob2-20260910"}

    class _Conn:
        def cursor(self):
            class _Cur:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_a):
                    return False

                def execute(self_inner, _sql, _params=None):
                    return None

                def fetchone(self_inner):
                    return (active["version"], "https://h/a.pkl#sha256=x", None)

            return _Cur()

    guard = ActiveVersionGuard("winprob2-20260910", cache_seconds=0.0)
    conn = _Conn()
    guard.confirm(conn, now=0.0)  # still active: fine

    active["version"] = "winprob2-20261001"  # promoted underneath us
    with pytest.raises(ModelVersionMismatch, match="Restart this container"):
        guard.confirm(conn, now=1.0)


def test_the_version_guard_mismatch_is_sticky():
    """Once a container knows it is serving the wrong model it does not get to
    recover by waiting - the artifact in memory is still the old one."""
    from models.artifact import ActiveVersionGuard, ModelVersionMismatch

    queries = {"n": 0}

    class _Conn:
        def cursor(self):
            class _Cur:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_a):
                    return False

                def execute(self_inner, _sql, _params=None):
                    queries["n"] += 1

                def fetchone(self_inner):
                    return ("winprob2-NEWER", "https://h/a.pkl#sha256=x", None)

            return _Cur()

    guard = ActiveVersionGuard("winprob2-20260910")
    conn = _Conn()
    with pytest.raises(ModelVersionMismatch):
        guard.confirm(conn)
    before = queries["n"]
    for _ in range(10):
        with pytest.raises(ModelVersionMismatch):
            guard.confirm(conn)
    assert queries["n"] == before, "a known mismatch must not re-query, just refuse"


def test_the_version_guard_caches_so_it_is_not_a_query_per_prediction():
    """The trade: read-per-prediction is safest but costs a round trip per
    call against a pooler whose free-tier ceiling is low (SPEC.md 2.4). 15s
    bounds the exposure to a handful of rows rather than minutes of them."""
    from models.artifact import ACTIVE_VERSION_CACHE_SECONDS, ActiveVersionGuard

    queries = {"n": 0}

    class _Conn:
        def cursor(self):
            class _Cur:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_a):
                    return False

                def execute(self_inner, _sql, _params=None):
                    queries["n"] += 1

                def fetchone(self_inner):
                    return ("winprob2-20260910", "https://h/a.pkl#sha256=x", None)

            return _Cur()

    guard = ActiveVersionGuard("winprob2-20260910")
    conn = _Conn()
    for i in range(100):
        guard.confirm(conn, now=1000.0 + i * 0.1)   # 10 seconds of traffic
    assert queries["n"] == 1, f"100 predictions caused {queries['n']} version queries"

    guard.confirm(conn, now=1000.0 + ACTIVE_VERSION_CACHE_SECONDS + 1)
    assert queries["n"] == 2
    assert ACTIVE_VERSION_CACHE_SECONDS <= 15.0, "exposure window must stay small"
