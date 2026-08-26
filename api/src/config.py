"""Fail-fast environment configuration for the Python service.

Import `settings` from this module instead of reading os.environ directly.
Fields belonging to phases not yet built are Optional here and will be
tightened to required as those phases land (see README's env var table).
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Phase 0 - data foundation
    local_database_url: str = Field(alias="LOCAL_DATABASE_URL")
    cricsheet_data_dir: str = Field(alias="CRICSHEET_DATA_DIR")

    # Phase 0/2 - Supabase (serving DB)
    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_secret_key: str = Field(alias="SUPABASE_SECRET_KEY")
    supabase_db_url: str | None = Field(default=None, alias="SUPABASE_DB_URL")

    # Phase 2 - live pipeline
    live_api_provider: str | None = Field(default=None, alias="LIVE_API_PROVIDER")
    live_api_key: str | None = Field(default=None, alias="LIVE_API_KEY")
    port: int = Field(default=8000, alias="PORT")

    # Phase 6 - agent
    agent_sql_role_db_url: str | None = Field(default=None, alias="AGENT_SQL_ROLE_DB_URL")
    agent_tool_shared_secret: str | None = Field(default=None, alias="AGENT_TOOL_SHARED_SECRET")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")


settings = Settings()
