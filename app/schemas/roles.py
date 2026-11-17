"""Roles and the menu-rights grid."""

import uuid
from typing import Literal

from pydantic import BaseModel

from app.schemas.common import ApiModel

DataScope = Literal["own", "team", "subtree", "all"]


class RoleOut(ApiModel):
    """A role with how many users hold it."""

    id: uuid.UUID
    key: str
    name: str
    description: str | None
    hierarchy_level: int
    data_scope: DataScope
    is_system: bool
    user_count: int = 0


class PermissionRow(BaseModel):
    """Rights of a role on one screen, with the screen's labels for display."""

    menu_key: str
    label: str
    group_label: str | None
    is_available: bool
    view: bool
    create: bool
    edit: bool
    delete: bool
    export: bool
    assign: bool
    data_scope: DataScope | None


class RolePermissionsOut(BaseModel):
    """`GET /roles/{id}/permissions`."""

    role: RoleOut
    editable: bool
    permissions: list[PermissionRow]


class PermissionUpdate(BaseModel):
    """One row of `PUT /roles/{id}/permissions`. `data_scope: null` = role default."""

    menu_key: str
    view: bool = False
    create: bool = False
    edit: bool = False
    delete: bool = False
    export: bool = False
    assign: bool = False
    data_scope: DataScope | None = None


class RolePermissionsUpdate(BaseModel):
    """`PUT /roles/{id}/permissions`: rows to change (others stay as they are)."""

    permissions: list[PermissionUpdate]
