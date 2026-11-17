"""FastAPI application factory and ASGI entry point (``app.main:app``)."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from app import __version__
from app.core.config import get_settings
from app.core.database import DatabaseNotConfiguredError
from app.core.errors import register_error_handlers
from app.routers import audit_logs, auth, health, organization, roles, settings, teams, users

# Vercel captures stdout/stderr into the function logs.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app() -> FastAPI:
    """Build the FastAPI app with every versioned router mounted under ``/api/v1``."""
    config = get_settings()
    prefix = config.api_prefix
    show_docs = config.app_env != "production"

    app = FastAPI(
        title="TEG CRM API",
        version=__version__,
        description=(
            "Back end for TEG CRM. All routes are versioned under `/api/v1`.\n\n"
            "Authentication is a session cookie set by `POST /auth/login`. "
            "Errors return `{detail, code}`."
        ),
        # Served in production too: the web build generates its types from it.
        openapi_url=f"{prefix}/openapi.json",
        docs_url=f"{prefix}/docs" if show_docs else None,
        redoc_url=None,
    )
    for module in (health, organization, auth, users, roles, teams, settings, audit_logs):
        app.include_router(module.router, prefix=prefix)
    register_error_handlers(app)

    @app.middleware("http")
    async def require_json_for_writes(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """CSRF guard: state-changing requests must be JSON.

        Browsers can't send `application/json` cross-site without a CORS preflight,
        and this API allows no cross-origin requests, so a forged form post from
        another site is rejected before it reaches any route.
        """
        content_type = request.headers.get("content-type", "")
        if request.method in _UNSAFE_METHODS and not content_type.startswith("application/json"):
            return JSONResponse(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                content={
                    "detail": "Send requests as JSON (Content-Type: application/json).",
                    "code": "json_required",
                },
            )
        return await call_next(request)

    @app.exception_handler(DatabaseNotConfiguredError)
    async def database_not_configured(_: Request, exc: DatabaseNotConfiguredError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc), "code": "database_not_configured"},
        )

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(f"{prefix}/docs" if show_docs else f"{prefix}/health")

    return app


app = create_app()
