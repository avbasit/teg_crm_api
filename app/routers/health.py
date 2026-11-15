"""`/health`: liveness plus a real database round-trip, used by Vercel checks and the web app."""

import time

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings
from app.core.database import DbSession
from app.schemas.health import DatabaseHealth, HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Service and database health")
async def get_health(response: Response, session: DbSession) -> HealthResponse:
    """Check that the API is running and can query Neon.

    Returns HTTP 200 when healthy and 503 when the database is unreachable, so
    uptime monitors can alert on the status code alone.
    """
    try:
        started = time.perf_counter()
        await session.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        server_version = await session.scalar(text("SHOW server_version"))
        database = DatabaseHealth(
            status="ok",
            latency_ms=latency_ms,
            schema_revision=revision,
            server_version=server_version,
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "unhealthy"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        database = DatabaseHealth(status="error", error=type(exc).__name__)

    return HealthResponse(
        status="ok" if database.status == "ok" else "degraded",
        version=__version__,
        environment=get_settings().app_env,
        database=database,
    )
