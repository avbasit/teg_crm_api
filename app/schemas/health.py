"""Health-check response schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class DatabaseHealth(BaseModel):
    """Result of the database round-trip."""

    status: Literal["ok", "error"]
    latency_ms: float | None = Field(None, description="Round-trip time of `SELECT 1`.")
    schema_revision: str | None = Field(None, description="Alembic revision applied to the DB.")
    server_version: str | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    """`GET /api/v1/health`."""

    status: Literal["ok", "degraded"]
    version: str
    environment: str
    database: DatabaseHealth
