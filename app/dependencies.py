"""Request dependencies: the signed-in user and permission checks.

Usage in a router::

    @router.get("", dependencies=[RequirePermission("users", "view")])
    async def list_users(current: CurrentUserDep, session: DbSession): ...
"""

from typing import Annotated

from fastapi import Depends, Request

from app.core.config import get_settings
from app.core.database import DbSession
from app.core.errors import AppError, forbidden
from app.services import auth_service
from app.services.access import Action, CurrentUser

NOT_SIGNED_IN = AppError(401, "not_authenticated", "Please sign in to continue.")
PASSWORD_CHANGE_REQUIRED = AppError(
    403, "password_change_required", "Please set a new password before continuing."
)


async def get_session_user(request: Request, session: DbSession) -> CurrentUser:
    """The signed-in user, even if they still have to change their password."""
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise NOT_SIGNED_IN
    current = await auth_service.authenticate(session, token)
    if current is None:
        raise NOT_SIGNED_IN
    return current


async def get_current_user(
    current: Annotated[CurrentUser, Depends(get_session_user)],
) -> CurrentUser:
    """The signed-in user, blocked until a forced password change is done."""
    if current.user.must_change_password:
        raise PASSWORD_CHANGE_REQUIRED
    return current


SessionUserDep = Annotated[CurrentUser, Depends(get_session_user)]
"""Signed-in user; allowed while a password change is pending (me, logout, change password)."""

CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
"""Signed-in user with a usable account. Use for every normal endpoint."""


def RequirePermission(menu_key: str, action: Action) -> object:  # noqa: N802 - reads as a declaration
    """Dependency that rejects the request unless the user has ``action`` on ``menu_key``."""

    async def check(current: CurrentUserDep) -> CurrentUser:
        if not current.can(menu_key, action):
            raise forbidden()
        return current

    return Depends(check)
