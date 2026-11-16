"""`/health`: liveness plus a real database round-trip, used by Vercel checks and the web app."""

import logging
import time

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings
from app.core.database import DatabaseNotConfiguredError, get_session_factory
from app.schemas.health import DatabaseHealth, HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


async def check_database() -> DatabaseHealth:
    """Run ``SELECT 1`` and read the schema revision; never raises."""
    try:
        async with get_session_factory()() as session:
            started = time.perf_counter()
            await session.execute(text("SELECT 1"))
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
            server_version = await session.scalar(text("SHOW server_version"))
    except DatabaseNotConfiguredError as exc:
        logger.error("Health check: %s", exc)
        return DatabaseHealth(status="error", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - any failure means "unhealthy"
        logger.exception("Health check: database query failed")
        return DatabaseHealth(status="error", error=type(exc).__name__)
    return DatabaseHealth(
        status="ok", latency_ms=latency_ms, schema_revision=revision, server_version=server_version
    )


@router.get("/health", response_model=HealthResponse, summary="Service and database health")
async def get_health(response: Response) -> HealthResponse:
    """Check that the API is running and can query Neon.

    Returns HTTP 200 when healthy and 503 when the database is unreachable or not
    configured, so uptime monitors can alert on the status code alone.
    """
    database = await check_database()
    if database.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if database.status == "ok" else "degraded",
        version=__version__,
        environment=get_settings().app_env,
        database=database,
    )
