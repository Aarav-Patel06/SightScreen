"""scripts/check_canary_freshness.py goes red on every way the record can be
wrong, and a failed canary cannot be excused by a stale marker."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_canary_freshness", Path(__file__).resolve().parent.parent / "scripts" / "check_canary_freshness.py")
check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check)


def _record(tmp_path, *, days_old=1, status="passed", dirty=False, **extra) -> Path:
    record = {
        "ran_at": (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat(timespec="seconds"),
        "git_sha": "abc1234def", "git_dirty": dirty, "fresh_days": 14, "passed": status == "passed",
        "results": {"splits_leak": {"status": status, "gap": 0.0268},
                    "lightgbm_shuffled_split": {"status": "passed", "gap": 0.1315}},
        **extra,
    }
    path = tmp_path / "canary_results.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_a_fresh_passing_record_is_green(tmp_path):
    assert check.main(_record(tmp_path)) == 0


def test_missing_failed_skipped_dirty_and_stale_are_red(tmp_path):
    assert check.main(tmp_path / "absent.json") == 1
    assert check.main(_record(tmp_path, status="failed")) == 1
    assert check.main(_record(tmp_path, status="skipped")) == 1
    assert check.main(_record(tmp_path, dirty=True)) == 1
    assert check.main(_record(tmp_path, days_old=15)) == 1


MARKER = {"stale": True, "stale_reason": "corpus machine away", "stale_clears_when": "next catch-up"}


def test_a_marker_excuses_staleness_only(tmp_path):
    assert check.main(_record(tmp_path, days_old=15, **MARKER)) == 0
    assert check.main(_record(tmp_path, days_old=15, status="failed", **MARKER)) == 1


def test_a_marker_on_a_fresh_record_is_red(tmp_path):
    assert check.main(_record(tmp_path, **MARKER)) == 1
