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
"""

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

if __name__ == "__main__":
    # Lets ci.yml grab the value with a one-line `python -m db.defaults`
    # instead of a multi-line import-and-print.
    print(LOCAL_DB_DEFAULT_URL)
