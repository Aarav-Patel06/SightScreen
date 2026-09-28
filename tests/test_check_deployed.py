"""scripts/check_deployed.py fails on each way production can be wrong, and
treats the deferred corpus's 503 as expected rather than as a failure."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_deployed", Path(__file__).resolve().parent.parent / "scripts" / "check_deployed.py")
check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check)


def _fake(monkeypatch, *, corpus=(503, '{"detail":"agent corpus unreachable"}'), unauth=401, live=200):
    def request(url, *, body=None, headers=None):
        authed = bool(headers)
        if url.endswith("/api/agent"):
            return 401, ""
        if not authed:
            return unauth, ""
        if url.endswith("/resolve_entity"):
            return corpus
        return live, "{}"
    monkeypatch.setattr(check, "_request", request)
    monkeypatch.setenv("AGENT_TOOL_SHARED_SECRET", "s" * 40)


def test_the_deferred_corpus_503_passes(monkeypatch):
    _fake(monkeypatch)
    assert check.smoke() == 0


def test_an_ungated_route_fails(monkeypatch):
    _fake(monkeypatch, unauth=200)
    assert check.smoke() == 1


def test_a_broken_answering_tool_fails(monkeypatch):
    _fake(monkeypatch, live=500)
    assert check.smoke() == 1


def test_the_corpus_arriving_fails_until_the_flag_is_turned_off(monkeypatch):
    _fake(monkeypatch, corpus=(200, "{}"))
    assert check.smoke() == 1


def test_a_service_behind_main_fails_after_the_window(monkeypatch):
    monkeypatch.setattr(check, "reported", lambda: {"web": "a" * 40, "api": "b" * 40, "worker": "a" * 40})
    monkeypatch.setattr(check, "_at_or_after", lambda expected, sha: sha == expected)
    assert check.check_sha("a" * 40, wait_minutes=0) == 1
    assert check.check_sha("b" * 40, wait_minutes=0) == 1


def test_every_service_on_main_passes(monkeypatch):
    monkeypatch.setattr(check, "reported", lambda: {"web": "a" * 40, "api": "a" * 40, "worker": "a" * 40})
    assert check.check_sha("a" * 40, wait_minutes=0) == 0


def test_a_long_or_garbled_health_reply_is_read_not_crashed_on(monkeypatch):
    import json as _json

    long_health = _json.dumps({"git_sha": "c" * 40, "model_notes": "x" * 2000})
    replies = {check.WEB + "/api/version": (200, "<html>restarting"), check.API + "/health": (200, long_health)}
    monkeypatch.setattr(check, "_request", lambda url, **_: replies[url])
    monkeypatch.delenv("SUPABASE_SESSION_POOLER_URL", raising=False)
    now = check.reported()
    assert now["api"] == "c" * 40
    assert now["web"] == "unreadable reply"


def test_main_is_fetched_before_ancestry_is_judged(monkeypatch):
    """A service on a commit newer than this checkout must not read as behind."""
    calls = []
    monkeypatch.setattr(check.subprocess, "run", lambda args, **kw: calls.append(args) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr(check, "reported", lambda: {"web": "b" * 40, "api": "b" * 40, "worker": "b" * 40})
    assert check.check_sha("a" * 40, wait_minutes=0) == 0
    assert calls[0][:3] == ["git", "fetch", "--quiet"]


def test_the_expected_commit_is_the_newest_ci_pass_that_has_settled():
    import json as _json
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    runs = [{"headSha": "new", "updatedAt": (now - timedelta(minutes=5)).isoformat()},
            {"headSha": "settled", "updatedAt": (now - timedelta(minutes=25)).isoformat()},
            {"headSha": "old", "updatedAt": (now - timedelta(hours=3)).isoformat()}]
    assert check.settled_ci_commit(20, gh=lambda *a: _json.dumps(runs)) == "settled"


# --- The worker's heartbeat (session 3) -------------------------------------------
#
# Each hourly fixture check writes a `worker_heartbeat` pipeline_runs row, even
# when nothing is picked. A worker that has stopped checking - crashed, wedged,
# holding on quota, unable to write - is otherwise silent until a match it
# should have picked goes unpredicted.

_OPS_SPEC = importlib.util.spec_from_file_location(
    "ops_alert", Path(__file__).resolve().parent.parent / "scripts" / "ops_alert.py")
ops = importlib.util.module_from_spec(_OPS_SPEC)
_OPS_SPEC.loader.exec_module(ops)


class _FakeGh:
    def __init__(self, open_titles=()):
        self.open = {n: t for n, t in enumerate(open_titles, start=1)}
        self.created = []

    def __call__(self, *args):
        import json as _json

        if args[:2] == ("issue", "list"):
            return _json.dumps([{"number": n, "title": t} for n, t in self.open.items()])
        if args[:2] == ("issue", "create"):
            self.created.append(args[args.index("--title") + 1])
        if args[:2] == ("issue", "close"):
            self.open.pop(int(args[2]))
        return ""


def _beat(monkeypatch, minutes_ago):
    from datetime import datetime, timedelta, timezone

    when = None if minutes_ago is None else datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    monkeypatch.setattr(check, "newest_heartbeat", lambda: when)


def test_a_stale_heartbeat_opens_the_issue(monkeypatch):
    _beat(monkeypatch, 150)
    code = check.heartbeat(max_age_minutes=120)
    gh = _FakeGh()
    outcome = ops.report(check.HEARTBEAT_ISSUE, failing=code == 1, details="", run_url="u", gh=gh)
    assert code == 1
    assert (outcome, gh.created) == ("opened", ["[ops] Worker heartbeat missing"])


def test_no_heartbeat_at_all_is_stale(monkeypatch):
    _beat(monkeypatch, None)
    assert check.heartbeat(max_age_minutes=120) == 1


def test_a_fresh_heartbeat_closes_the_issue(monkeypatch):
    _beat(monkeypatch, 30)
    code = check.heartbeat(max_age_minutes=120)
    gh = _FakeGh(open_titles=["[ops] Worker heartbeat missing"])
    outcome = ops.report(check.HEARTBEAT_ISSUE, failing=code == 1, details="", run_url="u", gh=gh)
    assert (code, outcome, gh.open) == (0, "closed", {})


def test_the_hourly_workflow_reports_the_heartbeat():
    """The check only matters if the Deployed workflow runs it and hands its
    outcome to ops_alert under the issue's title."""
    workflow = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "deployed.yml").read_text()
    assert "check_deployed.py heartbeat" in workflow
    assert f'--title "{check.HEARTBEAT_ISSUE}"' in workflow
    assert "steps.heartbeat.outcome == 'failure'" in workflow
