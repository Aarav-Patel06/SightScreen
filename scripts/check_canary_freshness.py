"""CI: the leak canaries ran recently, on committed code, and passed.

They need the local corpus, so CI cannot run them (docs/ci-skipped-tests.md);
this checks the record eval/record_canaries.py commits instead. It fails when
the record is missing, records a failed or skipped canary, came from
uncommitted code, or is older than its `fresh_days`.

Staleness alone may be excused - the agent-eval pattern - by marking the file
with `stale`, `stale_reason` and `stale_clears_when`. A FAILED canary cannot
be: a leak detector that fired is not a paperwork problem.

    python scripts/check_canary_freshness.py [path]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "api" / "data" / "canary_results.json"


def problems(record: dict, now: datetime) -> tuple[list[str], list[str]]:
    """(failures that nothing excuses, staleness that a marker may excuse)."""
    hard = [f"{name} {r['status']}" for name, r in record["results"].items() if r["status"] != "passed"]
    if record.get("git_dirty"):
        hard.append("recorded from uncommitted code")
    age = now - datetime.fromisoformat(record["ran_at"])
    stale = []
    if age > timedelta(days=record.get("fresh_days", 14)):
        stale.append(f"last ran {age.days} days ago (limit {record.get('fresh_days', 14)}) at {record['git_sha'][:7]}")
    return hard, stale


def main(path: Path) -> int:
    if not path.exists():
        print(f"::error::{path.name} is missing - run `python -m eval.record_canaries` (last step of a catch-up) and commit it")
        return 1
    record = json.loads(path.read_text(encoding="utf-8"))
    hard, stale = problems(record, datetime.now(timezone.utc))
    marked = all(record.get(k) for k in ("stale", "stale_reason", "stale_clears_when"))
    if hard:
        print("::error::leak canary record FAILS: " + "; ".join(hard))
        return 1
    if stale and not marked:
        print("::error::leak canaries are STALE and the file does not say so: " + "; ".join(stale)
              + ". Run the catch-up (it ends with eval.record_canaries) and commit the result.")
        return 1
    if stale:
        print("::warning::leak canaries are stale, and the file says so: " + "; ".join(stale))
        print(f"  reason: {record['stale_reason']}\n  clears when: {record['stale_clears_when']}")
        return 0
    if marked:
        print("::error::canary_results.json is marked stale but is fresh - remove the marker")
        return 1
    gaps = {name: r["gap"] for name, r in record["results"].items()}
    print(f"leak canaries passed {record['ran_at']} at {record['git_sha'][:7]}: {gaps}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT))
