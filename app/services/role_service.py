"""Roles & Permissions screen rules."""

import uuid

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import forbidden, invalid, not_found
from app.models.identity import Role, User
from app.repositories import identity_repository
from app.repositories.audit_repository import add_audit_log
from app.schemas.roles import PermissionRow, RoleOut, RolePermissionsOut, RolePermissionsUpdate
from app.services.access import ACTIONS, CurrentUser, can_manage

_FLAG = {action: f"can_{action}" for action in ACTIONS}


async def list_roles(session: AsyncSession, current: CurrentUser) -> list[RoleOut]:
    """Roles, most senior first, with user counts (archived users excluded)."""
    roles = await identity_repository.list_roles(session, current.organization_id)
    counts = dict(
        (
            await session.execute(
                select(User.role_id, func.count())
                .where(User.organization_id == current.organization_id, User.status != "archived")
                .group_by(User.role_id)
            )
        ).all()
    )
    return [
        RoleOut.model_validate(role).model_copy(update={"user_count": counts.get(role.id, 0)})
        for role in roles
    ]


def _editable(current: CurrentUser, role: Role) -> bool:
    # The Super Admin role is locked so nobody can remove their own way back in.
    return (
        role.key != "super_admin"
        and current.can("settings.roles", "edit")
        and can_manage(current, role)
    )


async def get_permissions(
    session: AsyncSession, current: CurrentUser, role_id: uuid.UUID
) -> RolePermissionsOut:
    """The full rights grid of one role."""
    role = await identity_repository.get_role(session, current.organization_id, role_id)
    if role is None:
        raise not_found("Role")
    menus = await identity_repository.get_menus(session)
    labels = {menu.key: menu.label for menu in menus}
    rows = {
        row.menu_key: row
        for row in await identity_repository.get_role_permissions(session, role.id)
    }
    grid = [
        PermissionRow(
            menu_key=menu.key,
            label=menu.label,
            group_label=labels.get(menu.parent_key) if menu.parent_key else None,
            is_available=menu.is_available,
            data_scope=rows[menu.key].data_scope if menu.key in rows else None,
            **{
                action: bool(menu.key in rows and getattr(rows[menu.key], flag))
                for action, flag in _FLAG.items()
            },
        )
        for menu in menus
        if menu.route  # leaf screens only; groups have no rights of their own
    ]
    return RolePermissionsOut(
        role=RoleOut.model_validate(role), editable=_editable(current, role), permissions=grid
    )


async def update_permissions(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    role_id: uuid.UUID,
    data: RolePermissionsUpdate,
) -> RolePermissionsOut:
    """Change rights rows of a role (audited per menu)."""
    role = await identity_repository.get_role(session, current.organization_id, role_id)
    if role is None:
        raise not_found("Role")
    if not _editable(current, role):
        raise forbidden(
            "The Super Admin role can't be edited."
            if role.key == "super_admin"
            else "You can only edit roles below your own."
        )

    rows = {
        row.menu_key: row
        for row in await identity_repository.get_role_permissions(session, role.id)
    }
    changes: dict[str, dict[str, list[object]]] = {}
    for update in data.permissions:
        row = rows.get(update.menu_key)
        if row is None:
            raise invalid("unknown_menu", f"Unknown screen '{update.menu_key}'.")
        wanted = {action: getattr(update, action) for action in ACTIONS}
        if not wanted["view"] and any(wanted[a] for a in ACTIONS if a != "view"):
            raise invalid("view_required", f"'{update.menu_key}': other rights need View as well.")
        menu_changes: dict[str, list[object]] = {}
        for action, flag in _FLAG.items():
            if getattr(row, flag) != wanted[action]:
                menu_changes[action] = [getattr(row, flag), wanted[action]]
                setattr(row, flag, wanted[action])
        if row.data_scope != update.data_scope:
            menu_changes["data_scope"] = [row.data_scope, update.data_scope]
            row.data_scope = update.data_scope
        if menu_changes:
            changes[update.menu_key] = menu_changes

    if changes:
        add_audit_log(
            session,
            organization_id=current.organization_id,
            actor_user_id=current.user.id,
            entity_type="role",
            entity_id=role.id,
            action="permission_change",
            changes={"role": role.key, "menus": changes},
            request=request,
        )
        await session.commit()
    return await get_permissions(session, current, role.id)
