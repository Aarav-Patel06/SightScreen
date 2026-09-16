"""Fail-fast environment configuration for the Python service.

Import `settings` from this module instead of reading os.environ directly.
Fields belonging to phases not yet built are Optional here and will be
tightened to required as those phases land (see README's env var table).
"""

import re
from urllib.parse import urlparse

from pydantic import Field, ValidationError, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from db.defaults import LOCAL_DB_PORT as LOCAL_DB_EXPECTED_PORT
from db.defaults import running_on_railway

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

# The two Railway services share one image; SERVICE_ROLE picks the process.
SERVICE_ROLES = frozenset({"api", "worker"})

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

    # Phase 0 - data foundation. Optional at the field level and re-required
    # for local runs by _enforce_database_boundary below, because a Railway
    # container must NOT have them - see SPEC.md section 2.1. Declaring them
    # required here would make the serving image unstartable.
    local_database_url: str | None = Field(default=None, alias="LOCAL_DATABASE_URL")
    cricsheet_data_dir: str | None = Field(default=None, alias="CRICSHEET_DATA_DIR")

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

    # Phase 2 session 4 - deployment. One image serves both Railway services;
    # SERVICE_ROLE picks which process starts (see serving/entrypoint.py).
    service_role: str = Field(default="api", alias="SERVICE_ROLE")
    # The model version this container is pinned to. Checked against
    # Supabase's active row at startup; a mismatch refuses to serve.
    model_version: str | None = Field(default=None, alias="MODEL_VERSION")

    # Phase 6 - agent
    agent_sql_role_db_url: str | None = Field(default=None, alias="AGENT_SQL_ROLE_DB_URL")
    agent_tool_shared_secret: str | None = Field(default=None, alias="AGENT_TOOL_SHARED_SECRET")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    @field_validator("local_database_url")
    @classmethod
    def _check_local_db_port(cls, value: str | None) -> str | None:
        if not value:
            return value
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

    # Populated by _strip_and_record_whitespace below. Reported by
    # serving/startup.py's banner and by /health, because a warning printed
    # once at boot scrolls out of a log within minutes.
    whitespace_stripped: tuple[str, ...] = ()
    # Set but empty - treated as absent, and named so the cause is findable.
    blank_variables: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _strip_and_record_whitespace(cls, values):
        """Strip surrounding whitespace from every setting, and remember which
        ones needed it.

        A value pasted into a dashboard field picks up a trailing newline or a
        leading space astonishingly easily, and the failure is invisible: a
        connection string with a trailing space presents as an authentication
        failure, which sends you looking at the password. Silent acceptance is
        the thing to avoid - stripping is safe, pretending it did not happen
        is not.
        """
        if not isinstance(values, dict):
            return values
        cleaned, affected, blank = {}, [], []
        for key, value in values.items():
            if not isinstance(value, str):
                cleaned[key] = value
                continue
            stripped = value.strip()
            if stripped != value:
                affected.append(str(key))
            if stripped == "":
                # A blank variable is ABSENT, not present-and-empty. Without
                # this, SUPABASE_URL="" satisfies "Field required" and the
                # service starts with an unusable value - which is exactly
                # what an unresolved ${{shared.NAME}} reference produces on
                # Railway. Found on the first real deploy: six references
                # resolved to empty strings and two of them sailed through
                # validation.
                blank.append(str(key))
                continue
            cleaned[key] = stripped
        if affected:
            cleaned["whitespace_stripped"] = tuple(sorted(affected))
        if blank:
            cleaned["blank_variables"] = tuple(sorted(blank))
        return cleaned

    @field_validator("service_role")
    @classmethod
    def _check_service_role(cls, value: str) -> str:
        if value not in SERVICE_ROLES:
            raise ValueError(
                f"SERVICE_ROLE must be one of {sorted(SERVICE_ROLES)}, got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _enforce_database_boundary(self) -> "Settings":
        """SPEC.md section 2.1, enforced rather than trusted.

        "Training never touches Supabase; serving never touches local
        Postgres." That has been a convention held up by nothing since Phase
        0. A deployed container that can see LOCAL_DATABASE_URL is one
        careless variable away from a serving path reading the training
        corpus - or, worse, a training script writing to Supabase.

        Same shape as _require_pooler_host above: default-deny, a specific
        diagnostic for the mistake someone will actually make, and a citation
        so the reader can check the rule rather than trust the message.
        """
        if running_on_railway():
            if self.local_database_url:
                raise ValueError(
                    "LOCAL_DATABASE_URL is set in a Railway environment. Serving never "
                    "touches local Postgres (SPEC.md section 2.1), and a laptop's "
                    "Postgres is unreachable from Railway regardless - so this variable "
                    "can only mislead. Delete it from the Railway service's variables."
                )
            if not self.supabase_session_pooler_url:
                blank_hint = (
                    f" Set but blank: {', '.join(self.blank_variables)}. An unresolved "
                    "${{shared.NAME}} reference resolves to an empty string."
                    if self.blank_variables
                    else ""
                )
                raise ValueError(
                    "SUPABASE_SESSION_POOLER_URL is required on Railway - it is the only "
                    f"database a deployed service may use (SPEC.md sections 2.1 and 2.4).{blank_hint}"
                )
            if not self.model_version:
                raise ValueError(
                    "MODEL_VERSION is required on Railway. A serving container must be "
                    "pinned to an explicit model version so its predictions can be "
                    "attributed; see SPEC.md section 2.1."
                )
        else:
            missing = [
                type(self).model_fields[name].alias or name
                for name in ("local_database_url", "cricsheet_data_dir")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    f"{', '.join(missing)} must be set outside Railway - local tooling "
                    "and training need them. (On Railway they are forbidden instead.)"
                )
        return self

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


def _load_settings() -> "Settings":
    """Instantiate Settings, but never let a failure print the environment.

    pydantic's ValidationError repr embeds `input_value`, which for a
    BaseSettings failure is the entire collected environment - SUPABASE_SECRET_KEY
    and LIVE_API_KEY included. Railway captures deploy output, so an
    unsanitised failure would write live credentials into a log that outlives
    the container. Found by running the section 2.1 boundary check in a real
    container and reading what it printed.

    Direct construction (`Settings(...)`) is deliberately left raising
    ValidationError, which is what tests/test_config.py asserts on.
    """
    try:
        return Settings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'settings'}: {error['msg']}"
            for error in exc.errors()
        )
        raise SystemExit(f"configuration rejected - {problems}") from None


settings = _load_settings()
