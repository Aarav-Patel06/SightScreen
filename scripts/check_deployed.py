"""Is production running main, and does the agent path answer? (SPEC.md section 15, 2026-09-27)

For seven days in September 2026 both Railway services ran week-old code while
every health check was green, because nothing compared what was deployed with
what was pushed. This does, from outside, and costs nothing: no model call.

  sha     Each service's reported commit, polled until it is `--expect` or a
          later commit on main, for up to `--wait-minutes`:
            web     GET /api/version                (VERCEL_GIT_COMMIT_SHA)
            api     GET /health -> git_sha          (RAILWAY_GIT_COMMIT_SHA)
            worker  newest pipeline_runs 'worker_start' row -> git_sha
                    (it has no HTTP endpoint; serving/startup.record_start)
          The window allows for Railway's "Wait for CI": it deploys only
          after CI passes, so a service can lag the push by CI's run time plus
          its own build - about 5 minutes measured, 20 allowed.

  smoke   The agent's tool service, without the model:
            - every tool route, with no secret, answers 401 (mounted, and gated);
            - the web route, with no session, answers 401 (no model call made);
            - with AGENT_TOOL_SHARED_SECRET set: get_live_prediction and
              get_player_form answer 200, and the corpus tools answer 503
              "agent corpus ..." - EXPECTED while the corpus is deferred
              (SPEC.md section 0a). A 200 there means the corpus arrived:
              the check fails so CORPUS_DEFERRED gets turned off deliberately.

    python scripts/check_deployed.py sha --expect <sha>
    python scripts/check_deployed.py smoke
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

WEB = "https://sight-screen-rose.vercel.app"
API = "https://api-production-5fa3.up.railway.app"

# SPEC.md section 0a: production has no corpus, deferred on cost.
CORPUS_DEFERRED = True
CORPUS_TOOLS = {"resolve_entity": {"name": "India", "kind": "team"}}
# query_ball_data and get_matchup are left out of the authenticated half: they
# log to agent_query_log, and a smoke check should write nothing.
ANSWERING_TOOLS = {
    "get_live_prediction": {"match_id": 3},
    "get_player_form": {"name": "Virat Kohli", "kind": "player"},
}
ALL_TOOLS = ("resolve_entity", "query_ball_data", "get_matchup", "get_player_form", "get_live_prediction")


def _request(url: str, *, body: dict | None = None, headers: dict | None = None) -> tuple[int, str]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as reply:
            return reply.status, reply.read().decode()[:300]
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()[:300]
    except Exception as error:  # noqa: BLE001 - never echo a request (it may carry the secret)
        return 0, type(error).__name__


def reported() -> dict[str, str]:
    out = {}
    status, body = _request(f"{WEB}/api/version")
    out["web"] = json.loads(body).get("git_sha", "unknown") if status == 200 else f"unreachable ({status})"
    status, body = _request(f"{API}/health")
    out["api"] = json.loads(body).get("git_sha", "unknown") if status == 200 else f"unreachable ({status})"
    url = os.environ.get("SUPABASE_SESSION_POOLER_URL")
    if not url:
        out["worker"] = "unchecked (SUPABASE_SESSION_POOLER_URL not set)"
    else:
        import psycopg

        with psycopg.connect(url, connect_timeout=30) as conn:
            row = conn.execute(
                "SELECT counts->>'git_sha' FROM pipeline_runs WHERE pipeline = 'worker_start' "
                "ORDER BY started_at DESC LIMIT 1").fetchone()
        out["worker"] = row[0] if row else "no start recorded"
    return out


def _at_or_after(expected: str, sha: str) -> bool:
    """`sha` is `expected` or a later commit (expected is its ancestor)."""
    if sha == expected:
        return True
    if len(sha) < 7 or not all(c in "0123456789abcdef" for c in sha):
        return False
    return subprocess.run(["git", "merge-base", "--is-ancestor", expected, sha], capture_output=True).returncode == 0


def check_sha(expected: str, wait_minutes: float) -> int:
    deadline = time.monotonic() + wait_minutes * 60
    while True:
        now = reported()
        behind = {name: sha for name, sha in now.items() if not _at_or_after(expected, sha)}
        if not behind:
            print(f"deployed: every service is on {expected[:7]} or later: "
                  + ", ".join(f"{k} {v[:7]}" for k, v in now.items()))
            return 0
        if time.monotonic() >= deadline:
            print(f"::error::NOT DEPLOYED {wait_minutes:g} minutes after {expected[:7]} passed CI: "
                  + ", ".join(f"{k} is on {v[:7] if len(v) >= 7 and ' ' not in v else v}" for k, v in behind.items()))
            return 1
        print(f"waiting on {', '.join(behind)} ...", flush=True)
        time.sleep(30)


def smoke() -> int:
    failures, notes = [], []
    for tool in ALL_TOOLS:
        status, _ = _request(f"{API}/agent/{tool}", body={})
        if status != 401:
            failures.append(f"{tool} without a secret answered {status}, not 401")
    status, _ = _request(f"{WEB}/api/agent", body={"question": "smoke"})
    if status != 401:
        failures.append(f"/api/agent without a session answered {status}, not 401")

    secret = (os.environ.get("AGENT_TOOL_SHARED_SECRET") or "").strip()
    if not secret:
        notes.append("authenticated half skipped: AGENT_TOOL_SHARED_SECRET not set here")
    else:
        headers = {"x-agent-secret": secret}
        for tool, body in ANSWERING_TOOLS.items():
            status, text = _request(f"{API}/agent/{tool}", body=body, headers=headers)
            if status != 200:
                failures.append(f"{tool} answered {status}: {text[:80]}")
        for tool, body in CORPUS_TOOLS.items():
            status, text = _request(f"{API}/agent/{tool}", body=body, headers=headers)
            if CORPUS_DEFERRED and status == 503 and "agent corpus" in text:
                notes.append(f"{tool}: 503 as expected (corpus deferred)")
            elif CORPUS_DEFERRED and status == 200:
                failures.append(f"{tool} answered 200: the corpus is reachable - set CORPUS_DEFERRED = False")
            elif not CORPUS_DEFERRED and status == 200:
                pass
            else:
                failures.append(f"{tool} answered {status}: {text[:80]}")
    for note in notes:
        print(f"::notice::{note}")
    if failures:
        print("::error::agent smoke check FAILED: " + "; ".join(failures))
        return 1
    print("agent smoke check passed (no model call made)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sha")
    s.add_argument("--expect", required=True)
    s.add_argument("--wait-minutes", type=float, default=20)
    sub.add_parser("smoke")
    args = parser.parse_args()
    sys.exit(check_sha(args.expect, args.wait_minutes) if args.command == "sha" else smoke())
