"""Liveness as a fact on disk, because the apparatus has lied three times.

A buffered pipe left a running job's output empty; `ps` under Git Bash did
not list a live Windows process; together they made a COMPLETED 48-run eval
look dead and it was re-run for about $0.70. These tests pin the properties
that make the heartbeat answer the question those two got wrong.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from agent_eval.heartbeat import STALE_AFTER_SECONDS, Heartbeat, read, status


@pytest.fixture()
def path(tmp_path):
    return tmp_path / "hb.json"


def test_a_fresh_job_reads_as_running(path):
    Heartbeat.start("job", total=10, path=path)
    state, line = status(path)
    assert state == "RUNNING"
    assert "job" in line


def test_finishing_is_distinct_from_going_quiet(path):
    """The distinction that matters. A job that stopped writing WITHOUT
    finishing crashed; one that finished ended cleanly. Collapsing them is
    how a dead run gets mistaken for a done one, and vice versa."""
    beat = Heartbeat.start("job", total=2, path=path)
    beat.finish()
    assert status(path)[0] == "FINISHED"


def test_a_stale_job_is_not_reported_as_finished(path):
    beat = Heartbeat.start("job", total=2, path=path)
    stale = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS + 60)
    beat.state.updated_at = stale.isoformat()
    beat._write()
    state, line = status(path)
    assert state == "STALE"
    assert "did NOT" in line
    assert "already spent money" in line


def test_progress_and_cost_survive_a_tick(path):
    beat = Heartbeat.start("job", total=48, path=path)
    beat.tick(done=12, dollars=0.3142)
    state = read(path)
    assert state.done == 12
    assert state.dollars == pytest.approx(0.3142)
    assert "12/48" in status(path)[1]


def test_the_context_manager_finishes_on_a_clean_exit(path):
    with Heartbeat.start("job", total=1, path=path):
        pass
    assert status(path)[0] == "FINISHED"


def test_an_exception_is_recorded_rather_than_swallowed(path):
    with pytest.raises(ValueError):
        with Heartbeat.start("job", total=1, path=path):
            raise ValueError("boom")
    state = read(path)
    assert state.finished
    assert "ValueError" in state.note


def test_liveness_never_consults_the_process_table(path):
    """The PID is recorded for diagnosis only. A heartbeat whose freshness
    said RUNNING must not be overridden by a PID lookup - that lookup is
    exactly what was wrong twice."""
    beat = Heartbeat.start("job", total=1, path=path)
    beat.state.pid = 999_999_999  # certainly not a live process
    beat._write()
    assert status(path)[0] == "RUNNING"


def test_a_missing_file_is_none_not_a_crash(path):
    assert read(path) is None
    assert status(path)[0] == "NONE"


def test_a_corrupt_file_does_not_masquerade_as_a_live_job(path):
    path.write_text("{ not json", encoding="utf-8")
    assert read(path) is None
    assert status(path)[0] == "NONE"


def test_writes_are_atomic_so_a_reader_never_sees_half_a_file(path):
    """A --status that reported "corrupt" because it caught a partial write
    would be one more instrument lying about liveness."""
    beat = Heartbeat.start("job", total=100, path=path)
    for i in range(50):
        beat.tick(done=i)
        assert json.loads(path.read_text(encoding="utf-8"))["done"] == i
    assert not path.with_suffix(".tmp").exists()
