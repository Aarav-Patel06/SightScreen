"""Unit tests for api/src/config.py's fail-fast behaviour.

These construct `Settings` directly with `_env_file=None` so the test is
isolated from any real .env file or ambient shell environment.
"""

import pytest
from pydantic import ValidationError

from config import Settings

REQUIRED_KWARGS = {
    "local_database_url": "postgresql://postgres:postgres@localhost:5432/cricket_training",
    "cricsheet_data_dir": "./data/cricsheet",
    "supabase_url": "https://example.supabase.co",
    "supabase_secret_key": "sb_secret_test",
}


def test_settings_loads_with_all_required_vars():
    settings = Settings(_env_file=None, **REQUIRED_KWARGS)
    assert settings.port == 8000
    assert settings.supabase_db_url is None
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
