"""Users, roles, menu rights, teams and sessions (see migration 0001 §1-2)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import CITEXT, INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin
from app.models.enums import DataScopeType, TeamMemberRoleType, UserStatusType


class AppMenu(TimestampMixin, Base):
    """Global catalogue of screens; the unit of menu-based rights."""

    __tablename__ = "app_menus"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_key: Mapped[str | None] = mapped_column(ForeignKey("app_menus.key"))
    label: Mapped[str] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(SmallInteger)
    is_available: Mapped[bool] = mapped_column(Boolean)


class Role(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """A role: hierarchy level plus default data scope."""

    __tablename__ = "roles"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    hierarchy_level: Mapped[int] = mapped_column(SmallInteger)
    data_scope: Mapped[str] = mapped_column(DataScopeType)
    is_system: Mapped[bool] = mapped_column(Boolean)

    permissions: Mapped[list["RoleMenuPermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class RoleMenuPermission(TimestampMixin, Base):
    """Rights of one role on one menu."""

    __tablename__ = "role_menu_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    menu_key: Mapped[str] = mapped_column(ForeignKey("app_menus.key"), primary_key=True)
    can_view: Mapped[bool] = mapped_column(Boolean, default=False)
    can_create: Mapped[bool] = mapped_column(Boolean, default=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False)
    can_export: Mapped[bool] = mapped_column(Boolean, default=False)
    can_assign: Mapped[bool] = mapped_column(Boolean, default=False)
    data_scope: Mapped[str | None] = mapped_column(DataScopeType)

    role: Mapped[Role] = relationship(back_populates="permissions")


class User(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Staff account."""

    __tablename__ = "users"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id"))
    reports_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    username: Mapped[str] = mapped_column(CITEXT)
    email: Mapped[str | None] = mapped_column(CITEXT)
    full_name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(UserStatusType, default="invited")
    is_assignable: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legacy_jaldi_id: Mapped[int | None] = mapped_column(Integer)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[Role] = relationship(lazy="joined")
    manager: Mapped["User | None"] = relationship(
        remote_side="User.id", lazy="joined", join_depth=1
    )


class Team(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Group of agents run by one or more managers."""

    __tablename__ = "teams"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    members: Mapped[list["TeamMember"]] = relationship(
        back_populates="team", cascade="all, delete-orphan", lazy="selectin"
    )


class TeamMember(TimestampMixin, Base):
    """Membership of a user in a team, as manager or member."""

    __tablename__ = "team_members"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    team_role: Mapped[str] = mapped_column(TeamMemberRoleType, default="member")

    team: Mapped[Team] = relationship(back_populates="members")
    user: Mapped[User] = relationship(lazy="joined")


class UserSession(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """A signed-in browser session (only the token hash is stored)."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
