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
