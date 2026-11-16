"""FastAPI application factory and ASGI entry point (``app.main:app``)."""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.core.config import get_settings
from app.core.database import DatabaseNotConfiguredError
from app.routers import health, organization

# Vercel captures stdout/stderr into the function logs.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


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

    @app.exception_handler(DatabaseNotConfiguredError)
    async def database_not_configured(_: Request, exc: DatabaseNotConfiguredError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(exc)}
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        content: dict[str, object] = {"detail": exc.detail}
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            # TEMPORARY routing diagnostics for the Vercel deployment; remove once fixed.
            content["path_received"] = request.scope.get("path")
            content["root_path"] = request.scope.get("root_path")
            content["routing_headers"] = {
                k: v
                for k, v in request.headers.items()
                if k.startswith(("x-matched", "x-vercel-forwarded", "x-forwarded-host", "x-now"))
                or k in {"x-vercel-deployment-url", "x-invoke-path", "x-original-url"}
            }
        return JSONResponse(content, status_code=exc.status_code, headers=exc.headers)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(f"{prefix}/docs" if show_docs else f"{prefix}/health")

    return app


app = create_app()
