"""Fail-fast environment configuration for the Python service.

Import `settings` from this module instead of reading os.environ directly.
Fields belonging to phases not yet built are Optional here and will be
tightened to required as those phases land (see README's env var table).
"""

import re
from urllib.parse import urlparse

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from db.defaults import LOCAL_DB_PORT as LOCAL_DB_EXPECTED_PORT

# LOCAL_DB_EXPECTED_PORT comes from db/defaults.py - the single source of
# truth for the local Postgres connection defaults, also used by ci.yml.
# Kept as a constant here rather than parsed from docker-compose.yml so
# config loading has no filesystem dependency beyond .env.

# Substrings that only ever appear in the *.env.example templates. If one of
# these shows up in a real value, someone copied the placeholder instead of
# filling it in.
_PLACEHOLDER_MARKERS = ("changeme", "your-project", "...")

# Supavisor pooler logins are "postgres.<PROJECT_REF>", not bare "postgres" -
# see SPEC.md section 2.4.
_POOLER_USERNAME_RE = re.compile(r"^postgres\.[^:@/]+$")

# Fields whose phase is active as of Phase 0. Fields for phases not yet
# built (Phase 2 live pipeline, Phase 6 agent) are intentionally excluded -
# their .env.example placeholders are fine to sit unfilled until those
# phases actually start reading them.
_ACTIVE_PLACEHOLDER_FIELDS = (
    "local_database_url",
    "cricsheet_data_dir",
    "supabase_url",
    "supabase_secret_key",
    "supabase_session_pooler_url",
    "supabase_transaction_pooler_url",
)


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Phase 0 - data foundation
    local_database_url: str = Field(alias="LOCAL_DATABASE_URL")
    cricsheet_data_dir: str = Field(alias="CRICSHEET_DATA_DIR")

    # Phase 0/2 - Supabase (serving DB)
    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_secret_key: str = Field(alias="SUPABASE_SECRET_KEY")
    # Both go through Supavisor - never the direct "db.<ref>.supabase.co"
    # endpoint, which is IPv6-only and unreachable from Railway or Docker's
    # default bridge network (SPEC.md section 2.4).
    supabase_session_pooler_url: str | None = Field(
        default=None, alias="SUPABASE_SESSION_POOLER_URL"
    )
    supabase_transaction_pooler_url: str | None = Field(
        default=None, alias="SUPABASE_TRANSACTION_POOLER_URL"
    )

    # Phase 2 - live pipeline
    live_api_provider: str | None = Field(default=None, alias="LIVE_API_PROVIDER")
    live_api_key: str | None = Field(default=None, alias="LIVE_API_KEY")
    port: int = Field(default=8000, alias="PORT")

    # Phase 6 - agent
    agent_sql_role_db_url: str | None = Field(default=None, alias="AGENT_SQL_ROLE_DB_URL")
    agent_tool_shared_secret: str | None = Field(default=None, alias="AGENT_TOOL_SHARED_SECRET")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    @field_validator("local_database_url")
    @classmethod
    def _check_local_db_port(cls, value: str) -> str:
        port = urlparse(value).port
        if port != LOCAL_DB_EXPECTED_PORT:
            raise ValueError(
                f"LOCAL_DATABASE_URL must use port {LOCAL_DB_EXPECTED_PORT} to match "
                f"docker-compose.yml's postgres port mapping, got {port!r}"
            )
        return value

    @field_validator("supabase_url")
    @classmethod
    def _check_supabase_url_shape(cls, value: str) -> str:
        if "/rest/" in value:
            raise ValueError(
                "SUPABASE_URL must be the bare project URL (e.g. "
                "https://xxxx.supabase.co) with no /rest/... path suffix"
            )
        return value

    @field_validator(
        "supabase_session_pooler_url",
        "supabase_transaction_pooler_url",
        "agent_sql_role_db_url",
    )
    @classmethod
    def _require_pooler_host(cls, value: str | None, info: ValidationInfo) -> str | None:
        if not value:
            return value
        env_name = cls.model_fields[info.field_name].alias or info.field_name
        host = urlparse(value).hostname or ""
        if "pooler.supabase.com" in host:
            return value
        # Allowlist, not a blocklist for "db." - rejecting only the specific
        # direct-host mistake catches one instance, not the whole class.
        if host.startswith("db."):
            raise ValueError(
                f"{env_name} points at the direct connection host ({host!r}), which "
                "resolves over IPv6 only and is unreachable from Railway or Docker's "
                "default bridge network - use the Supavisor pooler host instead. "
                "See SPEC.md section 2.4."
            )
        raise ValueError(
            f"{env_name}'s host ({host!r}) isn't a Supavisor pooler host "
            "(*.pooler.supabase.com) - see SPEC.md section 2.4."
        )

    @field_validator("supabase_session_pooler_url", "supabase_transaction_pooler_url")
    @classmethod
    def _check_pooler_username(cls, value: str | None, info: ValidationInfo) -> str | None:
        if not value:
            return value
        env_name = cls.model_fields[info.field_name].alias or info.field_name
        username = urlparse(value).username or ""
        if username and not _POOLER_USERNAME_RE.match(username):
            raise ValueError(
                f"{env_name}'s username is {username!r}, expected the pooler form "
                "'postgres.<PROJECT_REF>' (not bare 'postgres') - see SPEC.md section 2.4."
            )
        return value

    @model_validator(mode="after")
    def _reject_placeholder_values(self) -> "Settings":
        for field_name in _ACTIVE_PLACEHOLDER_FIELDS:
            value = getattr(self, field_name)
            if isinstance(value, str) and _looks_like_placeholder(value):
                env_name = type(self).model_fields[field_name].alias or field_name
                raise ValueError(
                    f"{env_name} still holds an example placeholder value - "
                    "put a real value in your .env file"
                )
        return self


settings = Settings()
