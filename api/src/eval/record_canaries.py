"""Run the leak canaries and commit what they said (SPEC.md section 9.1).

The canaries need the local corpus, so CI cannot run them: for weeks the only
record that they passed was a sentence. This runs THE TESTS THEMSELVES -
pytest, not a copy of their logic - and writes api/data/canary_results.json,
which is committed. CI's `canary-freshness` job fails when that file is
missing, records a failure, or is older than FRESH_DAYS, unless it is marked
stale with a reason (the agent-eval results' pattern).

Run it as the LAST step of a catch-up, after match_states and the summaries
are rebuilt: `ingest.daily_cricsheet catchup-local` prints the sequence. Run
earlier, it would test the corpus as it was before the new matches.

    python -m eval.record_canaries          # from api/, with api/.env
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
RESULTS_PATH = REPO_ROOT / "api" / "data" / "canary_results.json"
FRESH_DAYS = 14

CANARIES = {
    "splits_leak": "tests/eval/test_splits.py::test_a_ball_level_leak_scores_implausibly_well",
    "splits_no_leak_control": "tests/eval/test_splits.py::test_the_canary_stays_quiet_without_a_leak",
    "lightgbm_shuffled_split": "tests/models/test_win_prob_2nd.py::test_canary_shows_a_loud_gap_for_lightgbm",
}
# The lines each test prints, so the committed record carries the numbers.
_GAP = {
    "splits_leak": re.compile(r"leak canary: .*gap ([+-]?\d+\.\d+)"),
    "splits_no_leak_control": re.compile(r"no-leak control: .*gap ([+-]?\d+\.\d+)"),
    "lightgbm_shuffled_split": re.compile(r"honest Brier: .*gap: ([+-]?\d+\.\d+)"),
}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO_ROOT).stdout.strip()


def _corpus() -> dict:
    url = dotenv_values(REPO_ROOT / "api" / ".env").get("LOCAL_DATABASE_URL")
    with psycopg.connect(url) as conn:
        matches, newest = conn.execute(
            "SELECT count(DISTINCT match_id), max(match_date) FROM match_states").fetchone()
    return {"matches_in_match_states": matches, "newest_match_date": str(newest)}


def run() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "canaries.xml"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *CANARIES.values(), "-q", "-s", "-p", "no:cacheprovider",
             f"--junitxml={junit}"],
            capture_output=True, text=True, cwd=REPO_ROOT / "api",
        )
        cases = {}
        if junit.exists():
            for case in ET.parse(junit).getroot().iter("testcase"):
                failed = case.find("failure") is not None or case.find("error") is not None
                skipped = case.find("skipped") is not None
                cases[case.get("name")] = "failed" if failed else ("skipped" if skipped else "passed")

    results = {}
    for name, node in CANARIES.items():
        status = cases.get(node.split("::")[1], "not run")
        found = _GAP[name].search(proc.stdout)
        results[name] = {"test": node, "status": status, "gap": float(found.group(1)) if found else None}
    record = {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain", "--", "api/src", "tests")),
        "corpus": _corpus(),
        "results": results,
        # Skipped is not passed: a canary that did not run proves nothing.
        "passed": all(r["status"] == "passed" for r in results.values()),
        "fresh_days": FRESH_DAYS,
    }
    RESULTS_PATH.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    return record


if __name__ == "__main__":
    outcome = run()
    print(json.dumps(outcome, indent=1))
    print(f"written to {RESULTS_PATH.relative_to(REPO_ROOT)} - commit it")
    sys.exit(0 if outcome["passed"] else 1)
