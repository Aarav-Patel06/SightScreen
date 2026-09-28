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
