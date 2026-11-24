"""FastAPI application factory and ASGI entry point (``app.main:app``)."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from app import __version__
from app.core.config import get_settings
from app.core.database import DatabaseNotConfiguredError
from app.core.errors import register_error_handlers
from app.routers import (
    audit_logs,
    auth,
    campaigns,
    dashboard,
    health,
    imports,
    intake,
    leads,
    organization,
    reports,
    roles,
    settings,
    tasks,
    teams,
    users,
)

# Vercel captures stdout/stderr into the function logs.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# Only POST can be sent cross-site by a plain HTML form; PUT/PATCH/DELETE from another
# origin always need a CORS preflight, which this API grants only for `/intake`.
_FORM_METHODS = {"POST"}


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
    for module in (
        health,
        organization,
        auth,
        dashboard,
        reports,
        leads,
        tasks,
        campaigns,
        imports,
        intake,
        users,
        roles,
        teams,
        settings,
        audit_logs,
    ):
        app.include_router(module.router, prefix=prefix)
    register_error_handlers(app)
    intake_path = f"{prefix}/intake/"

    @app.middleware("http")
    async def require_json_for_writes(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """CSRF guard: POST requests must be JSON.

        Browsers can't send `application/json` cross-site without a CORS preflight,
        and this API allows cross-origin requests only to the cookie-less `/intake`
        endpoint, so a forged form post from another site never reaches a signed-in route.
        """
        content_type = request.headers.get("content-type", "")
        if request.method in _FORM_METHODS and not content_type.startswith("application/json"):
            return JSONResponse(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                content={
                    "detail": "Send requests as JSON (Content-Type: application/json).",
                    "code": "json_required",
                },
            )
        return await call_next(request)

    @app.middleware("http")
    async def open_cors_for_intake(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Let website forms on other sites post leads to `/intake/*` (and nothing else).

        No cookies are accepted cross-site (no `Allow-Credentials`); the website key
        authenticates the request, and a key can be limited to named sites.
        """
        if not request.url.path.startswith(intake_path):
            return await call_next(request)
        cors = {
            "Access-Control-Allow-Origin": request.headers.get("origin") or "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Api-Key",
            "Access-Control-Max-Age": "86400",
            "Vary": "Origin",
        }
        if request.method == "OPTIONS":
            return Response(status_code=status.HTTP_204_NO_CONTENT, headers=cors)
        response = await call_next(request)
        response.headers.update(cors)
        return response

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
