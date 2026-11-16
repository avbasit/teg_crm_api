"""Application settings, loaded from environment variables (and ``.env`` locally).

On Vercel the Neon integration injects ``DATABASE_URL`` (and ``POSTGRES_URL``);
locally it comes from the git-ignored ``.env`` at the repository root.

Settings never raise at import time: a missing or odd value must not crash the
whole function. ``/api/v1/health`` reports configuration problems instead.
"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

_ENV_ALIASES = {"development": "local", "dev": "local", "prod": "production"}


class Settings(BaseSettings):
    """Typed runtime configuration.

    Attributes:
        app_env: ``local`` / ``preview`` / ``production``. Taken from ``APP_ENV``,
            falling back to Vercel's ``VERCEL_ENV``; ``production`` hides the docs.
        database_url: Neon *pooled* connection string (PgBouncer); ``None`` if unset.
        organization_slug: Tenant served by this deployment.
        api_prefix: Path prefix for every versioned route.
    """

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    app_env: str = Field(default_factory=lambda: os.environ.get("VERCEL_ENV") or "local")
    database_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DATABASE_URL", "POSTGRES_URL")
    )
    organization_slug: str = "teg"
    api_prefix: str = "/api/v1"

    @field_validator("app_env")
    @classmethod
    def _normalise_app_env(cls, value: str) -> str:
        value = value.strip().lower()
        return _ENV_ALIASES.get(value, value)

    @field_validator("database_url")
    @classmethod
    def _blank_url_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (read once, then cached)."""
    return Settings()
