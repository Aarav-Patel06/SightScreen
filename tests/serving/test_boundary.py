"""The SPEC.md section 2.1 database boundary, enforced rather than trusted
(Phase 2 session 4, Decision 2).

"Training never touches Supabase; serving never touches local Postgres" was a
convention held up by nothing from Phase 0 until now. These tests are the
thing holding it up.

The same shape as tests/test_config.py: construct Settings directly with
_env_file=None so api/.env cannot supply values behind the test's back - a
real trap, since that file carries LOCAL_DATABASE_URL and would make every
"Railway" case fail for the wrong reason.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import Settings
from db.defaults import RAILWAY_ENV_MARKERS, running_on_railway

# Settings reads os.environ as well as the env file, and _env_file=None does
# nothing about the former. ci.yml exports LOCAL_DATABASE_URL into the job,
# which is why these passed on a laptop and failed in CI for ten runs.
pytestmark = pytest.mark.usefixtures("settings_env_isolated")

SUPABASE = {
    "SUPABASE_URL": "https://abcdefgh.supabase.co",
    "SUPABASE_SECRET_KEY": "sb_secret_notaplaceholder",
    "SUPABASE_SESSION_POOLER_URL":
        "postgresql://postgres.abcdefgh:pw@aws-0-eu-west-2.pooler.supabase.com:5432/postgres",
}
LOCAL_DB = "postgresql://postgres:postgres@localhost:5433/cricket_training"


def _settings(**overrides):
    return Settings(_env_file=None, **{**SUPABASE, **overrides})


# --- running_on_railway ---------------------------------------------------


def test_no_markers_is_not_railway():
    assert running_on_railway({}) is False
    assert running_on_railway({"PORT": "8000", "RAILWAY_SERVICE_NAME": "worker"}) is False


@pytest.mark.parametrize("marker", RAILWAY_ENV_MARKERS)
def test_any_deployment_marker_is_railway(marker):
    assert running_on_railway({marker: "something"}) is True


def test_service_name_alone_is_not_a_deployment_marker():
    """RAILWAY_SERVICE_NAME answers "which role", not "am I deployed". Using
    it for both would mean a locally-exported service name silently turned on
    the boundary rules."""
    assert "RAILWAY_SERVICE_NAME" not in RAILWAY_ENV_MARKERS


# --- The boundary ---------------------------------------------------------


def test_railway_with_local_database_url_refuses(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p1")
    with pytest.raises(ValidationError, match="section 2.1"):
        _settings(LOCAL_DATABASE_URL=LOCAL_DB, MODEL_VERSION="winprob2-20260910")


def test_railway_without_local_database_url_starts(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p1")
    settings = _settings(MODEL_VERSION="winprob2-20260910")
    assert settings.local_database_url is None
    assert settings.service_role == "api"


def test_railway_requires_the_pooler_url(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p1")
    with pytest.raises(ValidationError, match="SUPABASE_SESSION_POOLER_URL is required"):
        Settings(
            _env_file=None,
            SUPABASE_URL=SUPABASE["SUPABASE_URL"],
            SUPABASE_SECRET_KEY=SUPABASE["SUPABASE_SECRET_KEY"],
            MODEL_VERSION="winprob2-20260910",
        )


def test_railway_requires_a_pinned_model_version(monkeypatch):
    """An unpinned container would serve whatever happened to be active,
    which makes its predictions unattributable after the fact."""
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p1")
    with pytest.raises(ValidationError, match="MODEL_VERSION is required"):
        _settings()


def test_local_development_still_requires_the_local_database(monkeypatch):
    """The boundary must not become a loophole that lets laptop tooling run
    without the corpus configured."""
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    with pytest.raises(ValidationError, match="must be set outside Railway"):
        _settings(CRICSHEET_DATA_DIR="./data/cricsheet")


def test_local_development_unaffected(monkeypatch):
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    settings = _settings(LOCAL_DATABASE_URL=LOCAL_DB, CRICSHEET_DATA_DIR="./data/cricsheet")
    assert settings.local_database_url == LOCAL_DB
    assert settings.model_version is None


@pytest.mark.parametrize("role", ["api", "worker"])
def test_valid_service_roles(monkeypatch, role):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p1")
    assert _settings(MODEL_VERSION="v", SERVICE_ROLE=role).service_role == role


def test_unknown_service_role_refuses(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p1")
    with pytest.raises(ValidationError, match="SERVICE_ROLE"):
        _settings(MODEL_VERSION="v", SERVICE_ROLE="webserver")


def test_the_2_4_pooler_allowlist_still_fires_on_railway(monkeypatch):
    """Decision 2 must not have loosened Phase 0's section 2.4 check."""
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p1")
    with pytest.raises(ValidationError, match="IPv6"):
        _settings(
            MODEL_VERSION="v",
            SUPABASE_SESSION_POOLER_URL="postgresql://postgres.ab:pw@db.ab.supabase.co:5432/postgres",
        )


# --- values that arrive damaged rather than absent -------------------------
# Found on the first real Railway deploy, not by review.


def test_a_blank_variable_is_treated_as_absent(monkeypatch):
    """An unresolved ${{shared.NAME}} reference resolves to an EMPTY STRING,
    not to nothing.

    That is the bug this test exists for: `SUPABASE_URL=""` satisfied
    pydantic's "Field required", so two of the six references sailed through
    validation and the service only failed later, on a different variable,
    with a misleading message. A blank variable is absent.
    """
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    with pytest.raises(ValidationError, match="Field required"):
        Settings(
            _env_file=None,
            SUPABASE_URL="",
            SUPABASE_SECRET_KEY="",
            SUPABASE_SESSION_POOLER_URL="",
            LOCAL_DATABASE_URL=LOCAL_DB,
            CRICSHEET_DATA_DIR="./d",
        )


def test_blank_variables_are_named_in_the_boundary_error(monkeypatch):
    """The name, never the value - and the hint about where empty strings
    come from, because that is the non-obvious part."""
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p1")
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            SUPABASE_URL=SUPABASE["SUPABASE_URL"],
            SUPABASE_SECRET_KEY=SUPABASE["SUPABASE_SECRET_KEY"],
            SUPABASE_SESSION_POOLER_URL="",
            MODEL_VERSION="winprob2-20260910",
        )
    message = str(exc.value)
    assert "SUPABASE_SESSION_POOLER_URL" in message
    assert "shared.NAME" in message


@pytest.mark.parametrize("raw", ["  https://abcdefgh.supabase.co", "https://abcdefgh.supabase.co\n"])
def test_surrounding_whitespace_is_stripped_and_recorded(monkeypatch, raw):
    """A pasted value picks up a trailing newline astonishingly easily, and a
    connection string with one presents as an AUTH failure - which sends you
    looking at the password. Stripping is safe; silence is not."""
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    settings = Settings(
        _env_file=None,
        SUPABASE_URL=raw,
        SUPABASE_SECRET_KEY=SUPABASE["SUPABASE_SECRET_KEY"],
        SUPABASE_SESSION_POOLER_URL=SUPABASE["SUPABASE_SESSION_POOLER_URL"],
        LOCAL_DATABASE_URL=LOCAL_DB,
        CRICSHEET_DATA_DIR="./d",
    )
    assert settings.supabase_url == "https://abcdefgh.supabase.co"
    assert "SUPABASE_URL" in settings.whitespace_stripped


def test_clean_values_record_no_whitespace(monkeypatch):
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    settings = _settings(LOCAL_DATABASE_URL=LOCAL_DB, CRICSHEET_DATA_DIR="./d")
    assert settings.whitespace_stripped == ()
    assert settings.blank_variables == ()


def test_the_isolation_fixture_covers_every_settings_field():
    """The fixture is only as good as its list, and a list is exactly the
    kind of thing that goes stale the next time a field is added. Derived
    fields are computed by validators, never read from the environment."""
    derived = {"BLANK_VARIABLES", "WHITESPACE_STRIPPED"}
    from conftest import SETTINGS_ENV_VARS

    from_environment = {name.upper() for name in Settings.model_fields} - derived
    assert from_environment == set(SETTINGS_ENV_VARS)
