"""Run ONLY the agent tool endpoints, for local development.

    python -m agent_tools.serve            # 127.0.0.1:8000

Production serves these from `serving.app`, which also mounts /predict and
runs the deployment checks in `serving/startup.py` - model-version pinning,
reference freshness, the database boundary. Those are right for the
prediction service and irrelevant to executing a tool, and requiring them
here would mean you cannot work on the agent without a pinned MODEL_VERSION.

So this mounts the router alone, which is the same thing
tests/agent/test_tool_auth.py does and for the same reason. What it
therefore does NOT exercise is the mounting itself - if you want to know
that `serving.app` still carries these routes, that is what test_tool_auth
asserts.

Reads AGENT_SQL_ROLE_DB_URL and AGENT_TOOL_SHARED_SECRET from api/.env like
everything else. Without the first, the four corpus tools return 503 and
get_player_form still answers, which is a confusing half-working state - so
it is checked up front and refused loudly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    env = dotenv_values(_ENV_PATH)
    missing = [
        name
        for name in ("AGENT_SQL_ROLE_DB_URL", "AGENT_TOOL_SHARED_SECRET")
        if not (os.environ.get(name) or env.get(name) or "").strip()
    ]
    if missing:
        # Loudly, because the alternative is four tools returning 503 while a
        # fifth answers normally - which reads as "the agent is broken"
        # rather than as "one variable is unset".
        return _fail(
            f"{', '.join(missing)} not set (checked the environment and {_ENV_PATH}).\n"
            "Without them the corpus tools return 503 and only get_player_form answers, "
            "which looks like a broken agent rather than a missing variable.\n"
            "Build the replica with scripts/setup-replica.ps1 and put the "
            "AGENT_SQL_ROLE_DB_URL it prints into api/.env."
        )

    import uvicorn
    from fastapi import FastAPI

    from agent_tools import routes

    app = FastAPI(title="SightScreen agent tools (local development)")
    app.include_router(routes.router)

    print(f"agent tools on http://{args.host}:{args.port} - five endpoints under /agent")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
