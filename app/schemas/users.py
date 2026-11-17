"""User management schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.auth import RoleSummary
from app.schemas.common import ApiModel

USERNAME_PATTERN = r"^[A-Za-z0-9._-]{3,50}$"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserRef(ApiModel):
    """Minimal user reference (e.g. a manager)."""

    id: uuid.UUID
    full_name: str
    username: str


class UserTeam(BaseModel):
    """A team the user belongs to."""

    id: uuid.UUID
    name: str
    team_role: Literal["manager", "member"]


class UserStatusCounts(BaseModel):
    """`GET /users/counts`: numbers for the status tabs."""

    active: int
    paused: int
    archived: int
    invited: int


class UserOut(ApiModel):
    """A staff account as shown in the Users module."""

    id: uuid.UUID
    username: str
    full_name: str
    email: str | None
    phone: str | None
    avatar_url: str | None
    status: Literal["active", "paused", "archived", "invited"]
    is_assignable: bool
    must_change_password: bool
    role: RoleSummary
    manager: UserRef | None
    teams: list[UserTeam] = []
    last_login_at: datetime | None
    last_active_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class UserCreate(BaseModel):
    """`POST /users`: the account starts "invited" until the user sets their own password."""

    username: str = Field(pattern=USERNAME_PATTERN, description="3-50 letters, digits, . _ -")
    full_name: str = Field(min_length=1, max_length=150)
    email: str | None = Field(default=None, pattern=EMAIL_PATTERN, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    role_id: uuid.UUID
    reports_to_user_id: uuid.UUID | None = None
    is_assignable: bool = True
    temporary_password: str = Field(min_length=1, max_length=256)

    _clean = field_validator("email", "phone", mode="before")(_clean_optional)


class UserUpdate(BaseModel):
    """`PATCH /users/{id}`: send only fields to change (`reports_to_user_id: null` clears it)."""

    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    email: str | None = Field(default=None, pattern=EMAIL_PATTERN, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    role_id: uuid.UUID | None = None
    reports_to_user_id: uuid.UUID | None = None
    is_assignable: bool | None = None

    _clean = field_validator("email", "phone", mode="before")(_clean_optional)


class ResetPasswordRequest(BaseModel):
    """`POST /users/{id}/reset-password`."""

    temporary_password: str = Field(min_length=1, max_length=256)
