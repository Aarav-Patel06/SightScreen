"""Every deployed process says which commit it is running.

Railway lost its GitHub connection for seven days in September 2026 and no
push deployed, while /health looked healthy throughout. The commit a process
runs is now part of the facts both Railway roles report: /health for `api`,
the startup log for `worker` (both come from startup.banner).
"""

from __future__ import annotations

from serving import startup


def test_the_banner_reports_the_commit_railway_built(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "fbff394504a8edd2350da229f759c8f91bab379a")
    assert startup.git_sha() == "fbff394504a8edd2350da229f759c8f91bab379a"


def test_an_unknown_commit_says_so_rather_than_guessing(monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    assert startup.git_sha() == "unknown"


def test_the_banner_includes_it(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    monkeypatch.setattr(startup, "resolved_endpoint", lambda url: {
        "host": "aws-0-x.pooler.supabase.com", "port": 5432, "resolved": "1.2.3.4", "family": "AF_INET"})
    monkeypatch.setattr(startup, "supabase_url", lambda: "postgresql://x")
    lines: list[str] = []
    facts = startup.banner("worker", log=lines.append)
    assert facts["git_sha"] == "abc123"
    assert any("git_sha" in line and "abc123" in line for line in lines)


class _RecordingConn:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple] = []
        self.fail = fail

    def execute(self, sql, params=None):
        if self.fail:
            raise OSError("database unreachable")
        self.calls.append((sql, params))


def test_each_start_is_recorded_with_its_commit(monkeypatch):
    """The worker has no HTTP endpoint, so its commit is checkable only if
    it writes it somewhere: one pipeline_runs row per start, which
    scripts/check_deployed.py reads."""
    import json

    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "9ce5722abc")
    conn = _RecordingConn()
    startup.record_start(conn, "worker", {"git_sha": "9ce5722abc", "model_version": "winprob2-20260910"})
    (sql, params), = conn.calls
    assert "INSERT INTO pipeline_runs" in sql
    assert params[0] == "worker_start"
    assert json.loads(params[1]) == {"git_sha": "9ce5722abc", "model_version": "winprob2-20260910"}


def test_a_failed_record_never_stops_a_start():
    lines: list[str] = []
    startup.record_start(_RecordingConn(fail=True), "api", {"git_sha": "x", "model_version": "y"}, log=lines.append)
    assert any("could not record" in line for line in lines)
