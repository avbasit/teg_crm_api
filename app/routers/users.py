"""`/users`: staff accounts (Users module). Rights: menu `users`."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response, status

from app.core.database import DbSession
from app.dependencies import CurrentUserDep, RequirePermission
from app.models.identity import User
from app.repositories import user_repository
from app.schemas.common import ErrorResponse, Page
from app.schemas.users import (
    ResetPasswordRequest,
    UserCreate,
    UserOut,
    UserStatusCounts,
    UserTeam,
    UserUpdate,
)
from app.services import user_service
from app.services.access import visible_user_ids

router = APIRouter(prefix="/users", tags=["users"], responses={403: {"model": ErrorResponse}})
ERRORS = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


async def _out(session: DbSession, users: list[User]) -> list[UserOut]:
    """Response models with each user's teams attached (one extra query per page)."""
    teams = await user_repository.teams_for_users(session, [u.id for u in users])
    return [
        UserOut.model_validate(user).model_copy(
            update={
                "teams": [
                    UserTeam(id=team_id, name=name, team_role=role)
                    for team_id, name, role in teams.get(user.id, [])
                ]
            }
        )
        for user in users
    ]


async def _one(session: DbSession, user: User) -> UserOut:
    return (await _out(session, [user]))[0]


@router.get(
    "/counts",
    response_model=UserStatusCounts,
    dependencies=[RequirePermission("users", "view")],
)
async def count_users(current: CurrentUserDep, session: DbSession) -> UserStatusCounts:
    """How many users (in your scope) are active, paused, archived and invited."""
    visible = await visible_user_ids(session, current, "users")
    counts = await user_repository.count_by_status(
        session, organization_id=current.organization_id, visible_ids=visible
    )
    return UserStatusCounts(**counts)


@router.get("", response_model=Page[UserOut], dependencies=[RequirePermission("users", "view")])
async def list_users(
    current: CurrentUserDep,
    session: DbSession,
    status_filter: Annotated[
        Literal["active", "paused", "archived", "invited"] | None, Query(alias="status")
    ] = None,
    role_id: uuid.UUID | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[UserOut]:
    """Users within your data scope (Sales Managers: their teams), filterable by tab and search."""
    visible = await visible_user_ids(session, current, "users")
    users, total = await user_repository.list_users(
        session,
        organization_id=current.organization_id,
        visible_ids=visible,
        status=status_filter,
        role_id=role_id,
        search=search,
        limit=limit,
        offset=offset,
    )
    return Page(items=await _out(session, users), total=total, limit=limit, offset=offset)


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    dependencies=[RequirePermission("users", "create")],
)
async def create_user(
    body: UserCreate, request: Request, current: CurrentUserDep, session: DbSession
) -> UserOut:
    """Add a user with a temporary password. They must choose their own at first sign-in."""
    user = await user_service.create_user(session, request, current, body)
    return await _one(session, user)


@router.get(
    "/{user_id}",
    response_model=UserOut,
    responses=ERRORS,
    dependencies=[RequirePermission("users", "view")],
)
async def get_user(user_id: uuid.UUID, current: CurrentUserDep, session: DbSession) -> UserOut:
    """One user (404 if outside your scope)."""
    return await _one(session, await user_service.get_visible_user(session, current, user_id))


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    responses=ERRORS,
    dependencies=[RequirePermission("users", "edit")],
)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> UserOut:
    """Change details, role or manager. Only for people below your own role."""
    user = await user_service.update_user(session, request, current, user_id, body)
    return await _one(session, user)


async def _set_status(
    user_id: uuid.UUID,
    new_status: str,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> UserOut:
    user = await user_service.set_status(session, request, current, user_id, new_status)
    return await _one(session, user)


@router.post(
    "/{user_id}/pause",
    response_model=UserOut,
    responses=ERRORS,
    dependencies=[RequirePermission("users", "edit")],
)
async def pause_user(
    user_id: uuid.UUID, request: Request, current: CurrentUserDep, session: DbSession
) -> UserOut:
    """Temporarily block sign-in (signs the user out everywhere)."""
    return await _set_status(user_id, "paused", request, current, session)


@router.post(
    "/{user_id}/activate",
    response_model=UserOut,
    responses=ERRORS,
    dependencies=[RequirePermission("users", "edit")],
)
async def activate_user(
    user_id: uuid.UUID, request: Request, current: CurrentUserDep, session: DbSession
) -> UserOut:
    """Re-enable a paused or archived user."""
    return await _set_status(user_id, "active", request, current, session)


@router.post(
    "/{user_id}/archive",
    response_model=UserOut,
    responses=ERRORS,
    dependencies=[RequirePermission("users", "delete")],
)
async def archive_user(
    user_id: uuid.UUID, request: Request, current: CurrentUserDep, session: DbSession
) -> UserOut:
    """Archive a leaver (kept for history, can't sign in)."""
    return await _set_status(user_id, "archived", request, current, session)


@router.post(
    "/{user_id}/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERRORS,
    dependencies=[RequirePermission("users", "edit")],
)
async def reset_password(
    user_id: uuid.UUID,
    body: ResetPasswordRequest,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> Response:
    """Set a temporary password; the user must change it at next sign-in."""
    await user_service.reset_password(session, request, current, user_id, body.temporary_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
