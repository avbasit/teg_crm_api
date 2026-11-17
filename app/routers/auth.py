"""`/auth`: sign in, sign out, current user, change password."""

from dataclasses import asdict

from fastapi import APIRouter, Request, Response, status

from app.core.config import get_settings
from app.core.database import DbSession
from app.dependencies import CurrentUserDep, SessionUserDep
from app.repositories import identity_repository
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MenuItem,
    MenuRightsOut,
    MeResponse,
    MeUser,
    ProfileUpdate,
)
from app.schemas.common import ErrorResponse
from app.services import auth_service
from app.services.access import CurrentUser, build_menu_tree

router = APIRouter(prefix="/auth", tags=["auth"])

ERRORS = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    423: {"model": ErrorResponse},
}


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_max_days * 24 * 3600,  # server-side expiry is the real limit
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
    )


async def _me(session: DbSession, current: CurrentUser) -> MeResponse:
    menus = await identity_repository.get_menus(session)
    return MeResponse(
        user=MeUser.model_validate(current.user),
        permissions={
            key: MenuRightsOut(**asdict(rights)) for key, rights in current.rights.items()
        },
        menus=[MenuItem.model_validate(item) for item in build_menu_tree(menus, current)],
    )


@router.post("/login", response_model=MeResponse, responses=ERRORS, summary="Sign in")
async def login(
    body: LoginRequest, request: Request, response: Response, session: DbSession
) -> MeResponse:
    """Check username and password, then set the session cookie.

    After 5 wrong passwords the account locks for 15 minutes (423). If
    `user.must_change_password` is true, the app must show the change-password
    screen: every other endpoint answers 403 `password_change_required` until then.
    """
    current, token = await auth_service.login(session, request, body.username, body.password)
    _set_session_cookie(response, token)
    return await _me(session, current)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
async def logout(
    request: Request, response: Response, session: DbSession, current: SessionUserDep
) -> Response:
    """End the current session and clear the cookie."""
    await auth_service.logout(session, request, current)
    response.status_code = status.HTTP_204_NO_CONTENT
    _clear_session_cookie(response)
    return response


@router.get("/me", response_model=MeResponse, responses=ERRORS, summary="Current user")
async def me(session: DbSession, current: SessionUserDep) -> MeResponse:
    """Who is signed in, their rights per menu and the sidebar they should see."""
    return await _me(session, current)


@router.patch(
    "/me",
    response_model=MeResponse,
    responses={409: {"model": ErrorResponse}, **ERRORS},
    summary="Update my profile",
)
async def update_me(
    body: ProfileUpdate, request: Request, session: DbSession, current: CurrentUserDep
) -> MeResponse:
    """Change your own name, email or phone (My Account). Your role is changed by an admin."""
    await auth_service.update_profile(
        session, request, current, body.model_dump(exclude_unset=True)
    )
    return await _me(session, current)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERRORS,
    summary="Change my password",
)
async def change_password(
    body: ChangePasswordRequest, request: Request, session: DbSession, current: SessionUserDep
) -> Response:
    """Set a new password (min. 10 characters, not containing the username).

    Clears the forced-change flag and signs out the user's other sessions.
    """
    await auth_service.change_password(
        session, request, current, body.current_password, body.new_password
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
