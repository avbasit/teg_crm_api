"""Application error type and its JSON response format.

Every handled error returns ``{"detail": "<message for people>", "code": "<machine code>"}``
so the web app can show the message and branch on the code.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """An expected failure with an HTTP status, a stable code and a readable message."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        """Create the error.

        Args:
            status_code: HTTP status to return.
            code: Stable snake_case identifier, e.g. ``invalid_credentials``.
            detail: Message that can be shown to the user as-is.
        """
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def not_found(entity: str) -> AppError:
    """404 for a missing (or out-of-scope) record."""
    return AppError(404, "not_found", f"{entity} not found.")


def forbidden(detail: str = "You don't have permission to do that.") -> AppError:
    """403 for a permission or hierarchy violation."""
    return AppError(403, "forbidden", detail)


def conflict(code: str, detail: str) -> AppError:
    """409 for a uniqueness or state conflict."""
    return AppError(409, code, detail)


def invalid(code: str, detail: str) -> AppError:
    """422 for a business-rule validation failure."""
    return AppError(422, code, detail)


def register_error_handlers(app: FastAPI) -> None:
    """Attach the :class:`AppError` handler to ``app``."""

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code}
        )
