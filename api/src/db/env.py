"""Configuration reads that work in a container (Phase 2 session 4).

Every other module in this tree loads config with
`dotenv_values(REPO_ROOT / "api" / ".env")`. That call deliberately ignores
the real process environment, and `api/.env` is gitignored, so in a Railway
container those modules see an empty dict and exit. That is fine for them -
they are laptop-only training entry points - but nothing on a serving path
can use that pattern.

The precedence here is the one `tests/conftest.py` already relies on and CI
already depends on: real environment variables win, the .env file is the
local-development fallback. Same rule pydantic-settings applies, expressed
once so serving code and `config.py` cannot disagree about it.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

_file_values: dict[str, str | None] | None = None


class MissingConfig(RuntimeError):
    """A required setting is absent from both the environment and .env."""


def _from_file() -> dict[str, str | None]:
    # Read once. In a container the file never exists, so this is an empty
    # dict and every lookup falls through to os.environ.
    global _file_values
    if _file_values is None:
        _file_values = dict(dotenv_values(ENV_PATH)) if ENV_PATH.exists() else {}
    return _file_values


def env_value(key: str, default: str | None = None) -> str | None:
    """Environment first, then api/.env, then the default."""
    value = os.environ.get(key)
    if value:
        return value
    return _from_file().get(key) or default


def require_env(key: str, *, why: str = "") -> str:
    value = env_value(key)
    if not value:
        suffix = f" {why}" if why else ""
        raise MissingConfig(
            f"{key} is not set (checked the environment and {ENV_PATH}).{suffix}"
        )
    return value
