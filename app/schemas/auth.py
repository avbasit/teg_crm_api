"""Sign-in and current-user schemas."""

import uuid

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel


class LoginRequest(BaseModel):
    """`POST /auth/login`."""

    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    """`POST /auth/change-password`."""

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ProfileUpdate(BaseModel):
    """`PATCH /auth/me`: your own name and contact details (send only what changes)."""

    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    email: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    phone: str | None = Field(default=None, max_length=40)


class RoleSummary(ApiModel):
    """Role as shown next to a user."""

    id: uuid.UUID
    key: str
    name: str
    hierarchy_level: int
    data_scope: str


class MeUser(ApiModel):
    """The signed-in user's own details."""

    id: uuid.UUID
    username: str
    full_name: str
    email: str | None
    phone: str | None
    avatar_url: str | None
    must_change_password: bool
    role: RoleSummary


class MenuRightsOut(BaseModel):
    """Rights on one menu."""

    view: bool
    create: bool
    edit: bool
    delete: bool
    export: bool
    assign: bool
    data_scope: str


class MenuItem(BaseModel):
    """Sidebar entry the user may open."""

    key: str
    label: str
    route: str | None
    icon: str | None
    children: list["MenuItem"] = []


class MeResponse(BaseModel):
    """`GET /auth/me`: who is signed in, what they may do, and their sidebar."""

    user: MeUser
    permissions: dict[str, MenuRightsOut]
    menus: list[MenuItem]
