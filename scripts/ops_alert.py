"""Open, update or close one "[ops]" issue per scheduled check.

Only push-triggered CI may gate a deploy (SPEC.md section 15, 2026-09-28).
Railway's "Wait for CI" waits on EVERY check suite on the commit it deploys,
and a scheduled or dispatched run attaches to main's head commit as one - so a
failed nightly data run made Railway skip code deploys of that commit. Every
scheduled workflow therefore always concludes success, and reports a failure
here instead: an issue that @-mentions the owner, commented on while it keeps
failing, closed by the first run that passes.

    python scripts/ops_alert.py --title "Cricsheet daily ingest is failing" --failing true \
        --details "golden parity: 1 difference" --run-url URL

Needs `gh` and GH_TOKEN (the job's GITHUB_TOKEN, with issues: write).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess

PREFIX = "[ops] "


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def report(title: str, failing: bool, details: str, run_url: str, gh=_gh) -> str:
    title = PREFIX + title
    issues = json.loads(gh("issue", "list", "--state", "open", "--search", f'"{title}" in:title',
                           "--json", "number,title", "--limit", "20"))
    open_issue = next((i["number"] for i in issues if i["title"] == title), None)
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    if failing:
        body = f"{details}\n\nRun: {run_url}"
        if open_issue is None:
            gh("issue", "create", "--title", title, "--body", f"@{owner} {body}" if owner else body)
            return "opened"
        gh("issue", "comment", str(open_issue), "--body", f"Still failing. {body}")
        return "commented"
    if open_issue is not None:
        gh("issue", "comment", str(open_issue), "--body", f"Passing again: {run_url}")
        gh("issue", "close", str(open_issue))
        return "closed"
    return "nothing to do"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--failing", required=True, choices=("true", "false"))
    parser.add_argument("--details", default="")
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    print(report(args.title, args.failing == "true", args.details, args.run_url))
