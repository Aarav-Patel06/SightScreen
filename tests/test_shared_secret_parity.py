"""The web app and the tool service must agree on the shared secret.

Found by running /ask end to end on 2026-09-22: `api/.env` and
`web/.env.local` held DIFFERENT values for AGENT_TOOL_SHARED_SECRET - 45
characters against 43 - and nothing anywhere noticed. The tool service
answered every request with `{"detail":"unauthorized"}`.

Why that is worth a test rather than a note: the failure does not look like
a configuration problem. §10.1a authenticates web -> FastAPI with this
header, so a mismatch surfaces as every data question failing while the
password gate, the page and the model call all work perfectly. The obvious
reading is "the agent is broken", and the actual cause is two files that
were never compared.

The same drift between Vercel and Railway produces the same symptom in
production, where there is no local file to diff against.

Skipped rather than failed when either file is absent: CI checks out a
repository with no .env at all, and a test that cannot run there must not
pretend the secrets match.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ENV = REPO_ROOT / "api" / ".env"
WEB_ENV = REPO_ROOT / "web" / ".env.local"

KEY = "AGENT_TOOL_SHARED_SECRET"


def _value(path: Path) -> str | None:
    if not path.exists():
        return None
    raw = dotenv_values(path).get(KEY)
    return raw.strip() if raw else None


def test_the_shared_secret_matches_across_the_two_env_files():
    api_secret = _value(API_ENV)
    web_secret = _value(WEB_ENV)
    if api_secret is None or web_secret is None:
        pytest.skip(f"{KEY} not set in both {API_ENV} and {WEB_ENV}")

    # Never print the values. A failing test that dumps a shared secret into
    # CI logs has traded one problem for a worse one.
    assert api_secret == web_secret, (
        f"{KEY} differs between api/.env ({len(api_secret)} chars) and "
        f"web/.env.local ({len(web_secret)} chars). Every agent tool call will "
        "return 401 and it will look like the agent is broken, not like a "
        "configuration mismatch. Set both to the same value."
    )


def test_the_secret_is_not_a_placeholder():
    """`changeme` in api/.env.example is a real value someone can copy."""
    for path in (API_ENV, WEB_ENV):
        secret = _value(path)
        if secret is None:
            continue
        assert "changeme" not in secret.lower(), f"{path} still holds the example {KEY}"
        assert len(secret) >= 16, f"{path}'s {KEY} is too short to be a real secret"
