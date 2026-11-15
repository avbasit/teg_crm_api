"""Application settings, loaded from environment variables (and ``.env`` locally).

On Vercel the Neon integration injects ``DATABASE_URL``; locally it comes from
the git-ignored ``.env`` at the repository root.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed runtime configuration.

    Attributes:
        app_env: Deployment environment; ``production`` hides interactive docs.
        database_url: Neon *pooled* connection string (PgBouncer).
        organization_slug: Tenant served by this deployment.
        api_prefix: Path prefix for every versioned route.
    """

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    app_env: Literal["local", "preview", "production"] = "local"
    database_url: str
    organization_slug: str = "teg"
    api_prefix: str = "/api/v1"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (read once, then cached)."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
