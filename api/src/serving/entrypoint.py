"""Container entrypoint: one image, two Railway services (Phase 2 session 4).

Both services share ~100% of their dependencies, so one image is right even
though one process is not. They are separate services because their
lifecycles genuinely differ: the API is request-scoped and horizontally
scalable, while the worker is a singleton holding per-match in-memory state
that two replicas polling the same match would corrupt.

SERVICE_ROLE picks the process rather than a per-service start command,
because both Railway services share root directory `api` and therefore cannot
have different config files - and a start command typed into the dashboard is
invisible from the repo. One environment variable is explicit, greppable, and
testable locally with `docker run -e SERVICE_ROLE=worker`.
"""

from __future__ import annotations

import sys

from serving.startup import service_role


def main(argv: list[str] | None = None) -> None:
    role = (argv[0] if argv else None) or service_role()
    if role == "worker":
        from serving.live_loop import run

        run()
        return
    if role == "api":
        import uvicorn

        from config import settings

        uvicorn.run("serving.app:app", host="0.0.0.0", port=settings.port, log_level="info")
        return
    sys.exit(f"unknown SERVICE_ROLE {role!r} - expected 'api' or 'worker'")


if __name__ == "__main__":
    main(sys.argv[1:])
