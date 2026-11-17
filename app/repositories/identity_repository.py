"""Queries for users, roles, menu rights, sessions and data scope."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import AppMenu, Role, RoleMenuPermission, TeamMember, User, UserSession


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    """User by username (case-insensitive; the column is ``citext``)."""
    return await session.scalar(select(User).where(User.username == username.strip()))


async def get_user(
    session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID
) -> User | None:
    """User by id within an organisation."""
    return await session.scalar(
        select(User).where(User.id == user_id, User.organization_id == organization_id)
    )


async def get_active_session(
    session: AsyncSession, token_hash: str, now: datetime
) -> UserSession | None:
    """Unrevoked, unexpired session for ``token_hash``."""
    return await session.scalar(
        select(UserSession).where(
            UserSession.token_hash == token_hash,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
    )


async def revoke_user_sessions(
    session: AsyncSession,
    user_id: uuid.UUID,
    now: datetime,
    *,
    keep_session_id: uuid.UUID | None = None,
) -> None:
    """Sign a user out everywhere (optionally keeping the current session)."""
    query = update(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    )
    if keep_session_id:
        query = query.where(UserSession.id != keep_session_id)
    await session.execute(query.values(revoked_at=now))


async def get_menus(session: AsyncSession) -> list[AppMenu]:
    """All menus in sidebar order."""
    return list(await session.scalars(select(AppMenu).order_by(AppMenu.sort_order)))


async def get_role_permissions(
    session: AsyncSession, role_id: uuid.UUID
) -> list[RoleMenuPermission]:
    """Rights rows of one role."""
    return list(
        await session.scalars(
            select(RoleMenuPermission).where(RoleMenuPermission.role_id == role_id)
        )
    )


async def get_role(
    session: AsyncSession, organization_id: uuid.UUID, role_id: uuid.UUID
) -> Role | None:
    """Role by id within an organisation."""
    return await session.scalar(
        select(Role).where(Role.id == role_id, Role.organization_id == organization_id)
    )


async def list_roles(session: AsyncSession, organization_id: uuid.UUID) -> list[Role]:
    """Roles, most senior first."""
    return list(
        await session.scalars(
            select(Role)
            .where(Role.organization_id == organization_id)
            .order_by(Role.hierarchy_level.desc(), Role.name)
        )
    )


async def subtree_user_ids(session: AsyncSession, user_id: uuid.UUID) -> set[uuid.UUID]:
    """The user plus everyone below them in the reporting line (recursive)."""
    tree = select(User.id).where(User.id == user_id).cte("tree", recursive=True)
    tree = tree.union(select(User.id).where(User.reports_to_user_id == tree.c.id))
    return set(await session.scalars(select(tree.c.id)))


async def team_user_ids(session: AsyncSession, user_id: uuid.UUID) -> set[uuid.UUID]:
    """The user plus every member of the teams they manage."""
    managed = select(TeamMember.team_id).where(
        TeamMember.user_id == user_id, TeamMember.team_role == "manager"
    )
    members = await session.scalars(
        select(TeamMember.user_id).where(TeamMember.team_id.in_(managed))
    )
    return {user_id, *members}


async def count_active_super_admins(session: AsyncSession, organization_id: uuid.UUID) -> int:
    """Active users holding the super_admin role."""
    rows = await session.scalars(
        select(User.id)
        .join(Role, Role.id == User.role_id)
        .where(
            User.organization_id == organization_id,
            Role.key == "super_admin",
            User.status == "active",
        )
    )
    return len(list(rows))
