"""Tests for ``GET /api/v1/health`` and ``GET /api/v1/organization``."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import database
from app.core.config import Settings, get_settings
from app.main import app
from app.routers import health


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    """Re-read settings/engine for one test, then restore the cached ones."""
    caches = (get_settings, database.get_engine, database.get_session_factory)
    for cached in caches:
        cached.cache_clear()
    yield
    for cached in caches:
        cached.cache_clear()


def test_health_returns_503_when_database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead database gives 503 + status=degraded, never an unhandled 500."""
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/db", poolclass=NullPool)
    monkeypatch.setattr(health, "get_session_factory", lambda: async_sessionmaker(engine))

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"]["status"] == "error"


def test_missing_database_url_is_reported_not_crashed(
    monkeypatch: pytest.MonkeyPatch, fresh_settings: None
) -> None:
    """Neon not connected on Vercel → 503 with an actionable message, app still serves."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    client = TestClient(app)

    health_response = client.get("/api/v1/health")
    assert health_response.status_code == 503
    assert "DATABASE_URL is not set" in health_response.json()["database"]["error"]

    org_response = client.get("/api/v1/organization")
    assert org_response.status_code == 503
    assert "DATABASE_URL is not set" in org_response.json()["detail"]

    assert client.get("/api/v1/openapi.json").status_code == 200


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Production", "production"), (" preview ", "preview"), ("development", "local")],
)
def test_app_env_is_normalised(monkeypatch: pytest.MonkeyPatch, raw: str, expected: str) -> None:
    """A mistyped APP_ENV in Vercel must not crash the function at import."""
    monkeypatch.setenv("APP_ENV", raw)
    assert Settings().app_env == expected


def test_app_env_falls_back_to_vercel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without APP_ENV, Vercel's own VERCEL_ENV decides."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    assert Settings(_env_file=None).app_env == "preview"


def test_openapi_schema_is_served_under_api_prefix() -> None:
    """The web build generates its TypeScript types from this URL."""
    response = TestClient(app).get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert "/api/v1/health" in response.json()["paths"]


@pytest.mark.live_db
def test_health_against_neon() -> None:
    """Live check: Neon reachable and migrated to the latest revision."""
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200, response.text
    assert response.json()["database"]["schema_revision"] == "0002"


@pytest.mark.live_db
def test_organization_against_neon() -> None:
    """Live check: seeded organisation is returned."""
    response = TestClient(app).get("/api/v1/organization")
    assert response.status_code == 200, response.text
    assert response.json()["slug"] == "teg"
