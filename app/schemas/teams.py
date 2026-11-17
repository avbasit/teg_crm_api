"""Team schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel
from app.schemas.users import UserRef

TeamRole = Literal["manager", "member"]


class TeamMemberOut(ApiModel):
    """A member and their role in the team."""

    user: UserRef
    team_role: TeamRole


class TeamOut(ApiModel):
    """A team with its managers and members."""

    id: uuid.UUID
    name: str
    description: str | None
    members: list[TeamMemberOut]
    created_at: datetime
    updated_at: datetime


class TeamMemberIn(BaseModel):
    """Member to put in a team."""

    user_id: uuid.UUID
    team_role: TeamRole = "member"


class TeamCreate(BaseModel):
    """`POST /teams`."""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    members: list[TeamMemberIn] = []


class TeamUpdate(BaseModel):
    """`PATCH /teams/{id}`."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class TeamMembersUpdate(BaseModel):
    """`PUT /teams/{id}/members`: the complete new member list."""

    members: list[TeamMemberIn]
