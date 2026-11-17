"""`/roles`: roles and the menu-rights grid (Settings › Roles & Permissions)."""

import uuid

from fastapi import APIRouter, Depends, Request

from app.core.database import DbSession
from app.core.errors import forbidden
from app.dependencies import CurrentUserDep, RequirePermission
from app.schemas.common import ErrorResponse
from app.schemas.roles import RoleOut, RolePermissionsOut, RolePermissionsUpdate
from app.services import role_service
from app.services.access import CurrentUser

router = APIRouter(prefix="/roles", tags=["roles"], responses={403: {"model": ErrorResponse}})


async def _can_see_roles(current: CurrentUserDep) -> CurrentUser:
    # Needed by the role dropdown in Users, and by the Roles & Permissions screen.
    if not (current.can("users", "view") or current.can("settings.roles", "view")):
        raise forbidden()
    return current


@router.get("", response_model=list[RoleOut], dependencies=[Depends(_can_see_roles)])
async def list_roles(current: CurrentUserDep, session: DbSession) -> list[RoleOut]:
    """All roles, most senior first, with how many users hold each."""
    return await role_service.list_roles(session, current)


@router.get(
    "/{role_id}/permissions",
    response_model=RolePermissionsOut,
    responses={404: {"model": ErrorResponse}},
    dependencies=[RequirePermission("settings.roles", "view")],
)
async def get_permissions(
    role_id: uuid.UUID, current: CurrentUserDep, session: DbSession
) -> RolePermissionsOut:
    """Rights grid of one role. `editable` says whether you may change it."""
    return await role_service.get_permissions(session, current, role_id)


@router.put(
    "/{role_id}/permissions",
    response_model=RolePermissionsOut,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    dependencies=[RequirePermission("settings.roles", "edit")],
)
async def update_permissions(
    role_id: uuid.UUID,
    body: RolePermissionsUpdate,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> RolePermissionsOut:
    """Change rights. The Super Admin role is locked; others only below your level."""
    return await role_service.update_permissions(session, request, current, role_id, body)
