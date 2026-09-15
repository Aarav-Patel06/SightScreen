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
