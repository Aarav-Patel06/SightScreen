"""Record real CricketData responses as offline test fixtures (SPEC.md
section 4.2, Phase 2 session 2, Decision 6).

Every test in tests/ingest/test_cricketdata.py runs against these files.
No test may hit the network or burn quota - that rule is why this script
exists and why it is run by hand, once, rather than from a test.

The API key is stripped from every recorded body before it is written
(responses echo it back in the top-level `apikey` field).

Budget: capped at MAX_HITS. The script refuses to exceed it.

Usage (from the api/ directory, with api/.env configured):
    python -m ingest.record_fixtures
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "cricketdata"

BASE_URL = "https://api.cricapi.com/v1"
MAX_HITS = 10

# (fixture name, endpoint, extra params). The two failure cases are real
# responses, not hand-written: CricketData returns these exact bodies for a
# match whose ball-by-ball/scorecard it does not carry, which is the common
# case rather than the exception (see the session's provider spike).
REQUESTS: list[tuple[str, str, dict[str, str]]] = [
    ("current_matches", "currentMatches", {"offset": "0"}),
    ("cric_score", "cricScore", {}),
    ("matches_page0", "matches", {"offset": "0"}),
    # A match whose bbb IS enabled - proves the payload is an extras log.
    ("match_bbb_extras_only", "match_bbb", {"offset": "0", "id": "ae5ed385-d052-468d-84e9-55ccccb2e18f"}),
    # Real failure bodies, both hit during the spike.
    ("match_bbb_unavailable", "match_bbb", {"offset": "0", "id": "2519e9f2-5fe8-482e-906b-d85ab9d6a5d1"}),
    ("match_scorecard_missing", "match_scorecard", {"offset": "0", "id": "2519e9f2-5fe8-482e-906b-d85ab9d6a5d1"}),
]


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LIVE_API_KEY"):
        sys.exit(f"LIVE_API_KEY must be set in {ENV_PATH}")
    return env


def _fetch(endpoint: str, params: dict[str, str], api_key: str) -> dict:
    query = urllib.parse.urlencode({"apikey": api_key, **params})
    with urllib.request.urlopen(f"{BASE_URL}/{endpoint}?{query}", timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _strip_key(body: dict) -> dict:
    """Responses echo the key back in `apikey`. Never commit it."""
    if "apikey" in body:
        body["apikey"] = "REDACTED"
    return body


def main() -> None:
    api_key = _env()["LIVE_API_KEY"]
    if len(REQUESTS) > MAX_HITS:
        sys.exit(f"refusing to run: {len(REQUESTS)} requests exceeds the {MAX_HITS}-hit budget")

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, endpoint, params in REQUESTS:
        body = _strip_key(_fetch(endpoint, params, api_key))
        path = FIXTURE_DIR / f"{name}.json"
        path.write_text(json.dumps(body, indent=1), encoding="utf-8")
        info = body.get("info") or {}
        print(
            f"{name:28s} status={body.get('status'):8s} "
            f"hitsToday={info.get('hitsToday')} -> {path.name}"
        )

    print(f"\n{len(REQUESTS)} fixtures written to {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
