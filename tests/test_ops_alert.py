"""scripts/ops_alert.py: one issue per check, opened once, closed on recovery."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ops_alert", Path(__file__).resolve().parent.parent / "scripts" / "ops_alert.py")
ops = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ops)


class FakeGh:
    def __init__(self, open_titles=()):
        self.open = {n: t for n, t in enumerate(open_titles, start=1)}
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args[:2])
        if args[:2] == ("issue", "list"):
            return json.dumps([{"number": n, "title": t} for n, t in self.open.items()])
        if args[:2] == ("issue", "close"):
            self.open.pop(int(args[2]))
        return ""


def test_a_failure_opens_one_issue_and_then_comments():
    gh = FakeGh()
    assert ops.report("Production is behind main", True, "worker on 50c2b9f", "u", gh=gh) == "opened"
    gh2 = FakeGh(["[ops] Production is behind main"])
    assert ops.report("Production is behind main", True, "still", "u", gh=gh2) == "commented"
    assert ("issue", "create") not in gh2.calls


def test_a_pass_closes_the_open_issue_and_otherwise_does_nothing():
    gh = FakeGh(["[ops] Production is behind main"])
    assert ops.report("Production is behind main", False, "", "u", gh=gh) == "closed"
    assert gh.open == {}
    assert ops.report("Production is behind main", False, "", "u", gh=FakeGh()) == "nothing to do"


def test_a_similar_title_is_not_mistaken_for_this_check():
    gh = FakeGh(["[ops] Production is behind main - old"])
    assert ops.report("Production is behind main", True, "x", "u", gh=gh) == "opened"
