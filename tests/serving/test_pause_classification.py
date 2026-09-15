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
    OTHER,
    PAUSED,
    UNREACHABLE,
    AuthenticationFailed,
    backoff_delay,
    classify_connection_error,
    connect_with_backoff,
    describe,
)

# The paused text is verbatim Supavisor. docs/phase1-closeout.md:170-174
# records this costing real debugging time once already.
PAUSED_TEXT = 'connection failed: FATAL:  Tenant or user not found'
AUTH_TEXT = 'connection failed: FATAL:  password authentication failed for user "postgres.abc"'
DNS_TEXT = 'connection failed: could not translate host name "aws-0-x.pooler.supabase.com"'
TIMEOUT_TEXT = "connection timeout expired"


@pytest.mark.parametrize(
    "text,expected",
    [
        (PAUSED_TEXT, PAUSED),
        ("Project is paused", PAUSED),
        (AUTH_TEXT, AUTH),
        ('FATAL:  no pg_hba.conf entry for host "1.2.3.4"', AUTH),
        (DNS_TEXT, UNREACHABLE),
        (TIMEOUT_TEXT, UNREACHABLE),
        ("connection refused", UNREACHABLE),
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
