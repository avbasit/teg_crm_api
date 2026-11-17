"""The signed-in user's effective rights: menu permissions, data scope and hierarchy checks."""

import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import AppMenu, Role, User, UserSession
from app.repositories import identity_repository

Action = Literal["view", "create", "edit", "delete", "export", "assign"]
ACTIONS: tuple[Action, ...] = ("view", "create", "edit", "delete", "export", "assign")


@dataclass(frozen=True)
class MenuRights:
    """What a role may do on one menu."""

    view: bool = False
    create: bool = False
    edit: bool = False
    delete: bool = False
    export: bool = False
    assign: bool = False
    data_scope: str = "own"

    def allows(self, action: Action) -> bool:
        """Whether ``action`` is granted."""
        return bool(getattr(self, action))


@dataclass
class CurrentUser:
    """Everything a request needs to know about who is calling."""

    user: User
    session: UserSession
    rights: dict[str, MenuRights] = field(default_factory=dict)

    @property
    def role(self) -> Role:
        """The user's role."""
        return self.user.role

    @property
    def organization_id(self) -> uuid.UUID:
        """The user's organisation."""
        return self.user.organization_id

    @property
    def is_super_admin(self) -> bool:
        """Whether the user is a Super Admin (manages everyone, edits permissions)."""
        return self.user.role.key == "super_admin"

    def can(self, menu_key: str, action: Action) -> bool:
        """Whether the user may perform ``action`` on ``menu_key``."""
        rights = self.rights.get(menu_key)
        return bool(rights and rights.allows(action))

    def scope(self, menu_key: str) -> str:
        """Data scope for ``menu_key`` (``own`` / ``team`` / ``subtree`` / ``all``)."""
        rights = self.rights.get(menu_key)
        return rights.data_scope if rights else "own"


async def load_rights(session: AsyncSession, role: Role) -> dict[str, MenuRights]:
    """Effective rights per menu for ``role`` (menu override, else role default scope)."""
    rows = await identity_repository.get_role_permissions(session, role.id)
    return {
        row.menu_key: MenuRights(
            view=row.can_view,
            create=row.can_create,
            edit=row.can_edit,
            delete=row.can_delete,
            export=row.can_export,
            assign=row.can_assign,
            data_scope=row.data_scope or role.data_scope,
        )
        for row in rows
    }


async def visible_user_ids(
    session: AsyncSession, current: CurrentUser, menu_key: str
) -> set[uuid.UUID] | None:
    """User ids whose records ``current`` may see on ``menu_key``; ``None`` means everyone."""
    scope = current.scope(menu_key)
    if scope == "all":
        return None
    if scope == "subtree":
        return await identity_repository.subtree_user_ids(session, current.user.id)
    if scope == "team":
        return await identity_repository.team_user_ids(session, current.user.id)
    return {current.user.id}


def can_manage(actor: CurrentUser, target_role: Role) -> bool:
    """Hierarchy rule: manage only users whose role is below yours (Super Admin: everyone)."""
    return actor.is_super_admin or target_role.hierarchy_level < actor.role.hierarchy_level


def build_menu_tree(menus: list[AppMenu], current: CurrentUser) -> list[dict[str, object]]:
    """Sidebar: available menus the user can view, nested under their groups."""
    visible_leaves = {
        menu.key
        for menu in menus
        if menu.route and menu.is_available and current.can(menu.key, "view")
    }
    tree: list[dict[str, object]] = []
    for menu in menus:
        if menu.parent_key is not None:
            continue
        children = [
            {"key": child.key, "label": child.label, "route": child.route, "icon": child.icon}
            for child in menus
            if child.parent_key == menu.key and child.key in visible_leaves
        ]
        if menu.route and menu.key in visible_leaves:
            tree.append(
                {
                    "key": menu.key,
                    "label": menu.label,
                    "route": menu.route,
                    "icon": menu.icon,
                    "children": [],
                }
            )
        elif not menu.route and children:
            tree.append(
                {
                    "key": menu.key,
                    "label": menu.label,
                    "route": None,
                    "icon": menu.icon,
                    "children": children,
                }
            )
    return tree
