"""Single source of truth for the local training Postgres connection
defaults.

Both api/src/config.py and .github/workflows/ci.yml import this, so the
connection string - in particular sslmode, which has broken CI and local
dev independently twice now (Phase 0 sessions 2 and 4) - can never drift
between them again.

Deliberately dependency-free (no pydantic import) so it can be imported
standalone without triggering config.py's Settings() validation, which
needs env vars this module has no business requiring just to report a
constant.

Also home to the Railway environment markers (Phase 2 session 4), for the
same reason: config.py's boundary validator needs them, and so do serving
modules that must not import config.py's full Settings just to ask "am I
deployed?".
"""

import os

LOCAL_DB_HOST = "localhost"
LOCAL_DB_PORT = 5433
LOCAL_DB_USER = "postgres"
LOCAL_DB_PASSWORD = "postgres"
LOCAL_DB_NAME = "cricket_training"
LOCAL_DB_SSLMODE = "disable"

LOCAL_DB_DEFAULT_URL = (
    f"postgresql://{LOCAL_DB_USER}:{LOCAL_DB_PASSWORD}@{LOCAL_DB_HOST}:{LOCAL_DB_PORT}"
    f"/{LOCAL_DB_NAME}?sslmode={LOCAL_DB_SSLMODE}"
)

# Supavisor pooler ports, per SPEC.md section 2.4. Session mode holds a
# backend for the life of the client connection (Railway, migrations, admin
# scripts); transaction mode holds one only for the life of a query (Vercel,
# anything serverless) and has no prepared statements. Named here rather than
# inline in config.py for the same reason LOCAL_DB_PORT is - one place, so a
# validator and a doc cannot drift.
SESSION_POOLER_PORT = 5432
TRANSACTION_POOLER_PORT = 6543


# Railway injects these into every deployment. Any one of them means "this
# process is running on Railway", which is the condition SPEC.md section
# 2.1's boundary turns on: serving never touches local Postgres.
# RAILWAY_SERVICE_NAME is deliberately NOT in this list - it is the
# service-identity variable serving/entrypoint.py falls back to, and using
# it for both jobs would conflate "which role am I" with "am I deployed".
RAILWAY_ENV_MARKERS = (
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_PROJECT_ID",
    "RAILWAY_SERVICE_ID",
)


def running_on_railway(env: "os._Environ[str] | dict[str, str] | None" = None) -> bool:
    """True when any Railway deployment marker is present.

    Takes `env` so tests can prove both branches without mutating the real
    process environment.
    """
    env = os.environ if env is None else env
    return any(env.get(marker) for marker in RAILWAY_ENV_MARKERS)


if __name__ == "__main__":
    # Lets ci.yml grab the value with a one-line `python -m db.defaults`
    # instead of a multi-line import-and-print.
    print(LOCAL_DB_DEFAULT_URL)
