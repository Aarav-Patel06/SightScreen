"""Unit tests for api/src/config.py's fail-fast behaviour.

These construct `Settings` directly with `_env_file=None` so the test is
isolated from any real .env file or ambient shell environment.
"""

from pathlib import Path

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError

from config import Settings
from db.defaults import LOCAL_DB_DEFAULT_URL

# Settings reads os.environ as well as the env file, and _env_file=None does
# nothing about the former. ci.yml exports LOCAL_DATABASE_URL into the job,
# which is why these passed on a laptop and failed in CI for ten runs.
pytestmark = pytest.mark.usefixtures("settings_env_isolated")


REQUIRED_KWARGS = {
    "local_database_url": "postgresql://postgres:postgres@localhost:5433/cricket_training",
    "cricsheet_data_dir": "./data/cricsheet",
    "supabase_url": "https://example.supabase.co",
    "supabase_secret_key": "sb_secret_test",
}


def test_settings_loads_with_all_required_vars():
    settings = Settings(_env_file=None, **REQUIRED_KWARGS)
    assert settings.port == 8000
    assert settings.supabase_session_pooler_url is None
    assert settings.anthropic_api_key is None


@pytest.mark.parametrize("missing_key", list(REQUIRED_KWARGS))
def test_settings_fails_fast_on_missing_required_var(monkeypatch, missing_key):
    for env_name in (
        "LOCAL_DATABASE_URL",
        "CRICSHEET_DATA_DIR",
        "SUPABASE_URL",
        "SUPABASE_SECRET_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)

    kwargs = {k: v for k, v in REQUIRED_KWARGS.items() if k != missing_key}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)


def test_settings_rejects_local_db_port_mismatch():
    kwargs = {
        **REQUIRED_KWARGS,
        "local_database_url": "postgresql://postgres:postgres@localhost:5432/cricket_training",
    }
    with pytest.raises(ValidationError, match="5433"):
        Settings(_env_file=None, **kwargs)


def test_settings_rejects_supabase_url_with_rest_path():
    kwargs = {**REQUIRED_KWARGS, "supabase_url": "https://example.supabase.co/rest/v1/"}
    with pytest.raises(ValidationError, match="/rest/"):
        Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize(
    ("field", "placeholder_value"),
    [
        ("supabase_url", "https://your-project.supabase.co"),
        ("supabase_secret_key", "sb_secret_..."),
        ("cricsheet_data_dir", "changeme"),
    ],
)
def test_settings_rejects_placeholder_values(field, placeholder_value):
    kwargs = {**REQUIRED_KWARGS, field: placeholder_value}
    with pytest.raises(ValidationError, match="placeholder"):
        Settings(_env_file=None, **kwargs)


def test_settings_ignores_placeholder_values_on_not_yet_active_phases():
    # live_api_key/agent_tool_shared_secret/anthropic_api_key belong to
    # Phase 2/6, which aren't built yet - their .env.example placeholders
    # sitting unfilled in a real .env must not block Settings() from loading.
    kwargs = {
        **REQUIRED_KWARGS,
        "live_api_key": "changeme",
        "agent_tool_shared_secret": "changeme",
        "anthropic_api_key": "sk-ant-...",
    }
    settings = Settings(_env_file=None, **kwargs)
    assert settings.live_api_key == "changeme"


def _pooler_url(port: int) -> str:
    return f"postgresql://postgres.abcxyzref:pw@aws-0-ap-south-1.pooler.supabase.com:{port}/postgres"


# Session mode and transaction mode are DIFFERENT ports and are not
# interchangeable (SPEC.md section 2.4). Until 2026-09-18 this file used a
# single VALID_POOLER_URL on port 5432 and asserted it was valid in the
# TRANSACTION field - so the test suite actively enforced the wrong thing
# while the validator checked only the host.
VALID_POOLER_URL = _pooler_url(5432)
SESSION_POOLER_URL = _pooler_url(5432)
TRANSACTION_POOLER_URL = _pooler_url(6543)


@pytest.mark.parametrize(
    "field,url",
    [
        ("supabase_session_pooler_url", SESSION_POOLER_URL),
        ("supabase_transaction_pooler_url", TRANSACTION_POOLER_URL),
        # agent_sql_role_db_url has no port rule - Phase 6 has not decided
        # which mode its read-only role uses, and inventing one here would
        # be enforcing a decision nobody has made.
        ("agent_sql_role_db_url", SESSION_POOLER_URL),
    ],
)
def test_settings_accepts_a_real_pooler_url(field, url):
    kwargs = {**REQUIRED_KWARGS, field: url}
    settings = Settings(_env_file=None, **kwargs)
    assert getattr(settings, field) == url


@pytest.mark.parametrize(
    "field,wrong_url,expected_port",
    [
        ("supabase_session_pooler_url", TRANSACTION_POOLER_URL, 5432),
        ("supabase_transaction_pooler_url", SESSION_POOLER_URL, 6543),
    ],
)
def test_settings_rejects_the_wrong_pooler_port(field, wrong_url, expected_port):
    """Both directions, because both are real mistakes and neither shows up
    in development - session mode in a serverless function exhausts the pool
    under concurrency, and transaction mode in the worker breaks SET and temp
    tables intermittently."""
    kwargs = {**REQUIRED_KWARGS, field: wrong_url}
    with pytest.raises(ValidationError, match=str(expected_port)):
        Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize(
    "field", ["supabase_session_pooler_url", "supabase_transaction_pooler_url", "agent_sql_role_db_url"]
)
def test_settings_rejects_direct_connection_host(field):
    direct_url = "postgresql://postgres.abcxyzref:pw@db.abcxyzref.supabase.co:5432/postgres"
    kwargs = {**REQUIRED_KWARGS, field: direct_url}
    with pytest.raises(ValidationError, match="IPv6"):
        Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize(
    "field", ["supabase_session_pooler_url", "supabase_transaction_pooler_url", "agent_sql_role_db_url"]
)
def test_settings_rejects_non_pooler_host(field):
    other_host_url = "postgresql://postgres.abcxyzref:pw@some-other-host.example.com:5432/postgres"
    kwargs = {**REQUIRED_KWARGS, field: other_host_url}
    with pytest.raises(ValidationError, match="pooler host"):
        Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize(
    "field,port",
    [("supabase_session_pooler_url", 5432), ("supabase_transaction_pooler_url", 6543)],
)
def test_settings_rejects_bare_postgres_username_on_pooler_urls(field, port):
    # The port must be the CORRECT one for the field, so that the username
    # check is what fires. With a wrong port the port validator rejects first
    # and this test passes for the wrong reason.
    bare_username_url = (
        f"postgresql://postgres:pw@aws-0-ap-south-1.pooler.supabase.com:{port}/postgres"
    )
    kwargs = {**REQUIRED_KWARGS, field: bare_username_url}
    with pytest.raises(ValidationError, match="postgres.<PROJECT_REF>"):
        Settings(_env_file=None, **kwargs)


def test_settings_does_not_check_username_pattern_on_agent_sql_role_url():
    # agent_sql_role_db_url gets its own role/username in Phase 6 - it must
    # never be forced into the postgres.<ref> superuser pattern.
    kwargs = {
        **REQUIRED_KWARGS,
        "agent_sql_role_db_url": "postgresql://agent_ro:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres",
    }
    settings = Settings(_env_file=None, **kwargs)
    assert settings.agent_sql_role_db_url == kwargs["agent_sql_role_db_url"]


def test_env_example_matches_canonical_local_db_default():
    # sslmode has broken CI and local dev independently twice (sessions 2
    # and 4) because this value was hand-duplicated across files. This test
    # is the enforcement mechanism for db/defaults.py actually being the
    # one source of truth - a manual edit to just one copy now fails CI
    # instead of waiting for a third incident to notice.
    env_example_path = Path(__file__).resolve().parent.parent / "api" / ".env.example"
    documented_value = dotenv_values(env_example_path)["LOCAL_DATABASE_URL"]
    assert documented_value == LOCAL_DB_DEFAULT_URL
