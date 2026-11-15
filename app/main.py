"""FastAPI application factory and ASGI entry point (``app.main:app``)."""

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app import __version__
from app.core.config import get_settings
from app.routers import health, organization


def create_app() -> FastAPI:
    """Build the FastAPI app with every versioned router mounted under ``/api/v1``."""
    settings = get_settings()
    prefix = settings.api_prefix
    show_docs = settings.app_env != "production"

    app = FastAPI(
        title="TEG CRM API",
        version=__version__,
        description="Back end for TEG CRM. All routes are versioned under `/api/v1`.",
        # Served in production too: the web build generates its types from it.
        openapi_url=f"{prefix}/openapi.json",
        docs_url=f"{prefix}/docs" if show_docs else None,
        redoc_url=None,
    )
    app.include_router(health.router, prefix=prefix)
    app.include_router(organization.router, prefix=prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(f"{prefix}/docs" if show_docs else f"{prefix}/health")

    return app


app = create_app()
