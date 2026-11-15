"""Tests for ``GET /api/v1/health`` and ``GET /api/v1/organization``."""

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_db_session
from app.main import app


def test_health_returns_503_when_database_unreachable() -> None:
    """A dead database gives 503 + status=degraded, never an unhandled 500."""
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/db", poolclass=NullPool)
    factory = async_sessionmaker(engine)

    async def unreachable_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = unreachable_session
    try:
        response = TestClient(app).get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"]["status"] == "error"


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
