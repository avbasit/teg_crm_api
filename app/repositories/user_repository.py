"""User listing queries for the Users module."""

import uuid

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Team, TeamMember, User


async def list_users(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    visible_ids: set[uuid.UUID] | None,
    status: str | None,
    role_id: uuid.UUID | None,
    search: str | None,
    limit: int,
    offset: int,
) -> tuple[list[User], int]:
    """Page of users (alphabetical) plus total, limited to ``visible_ids`` when given."""
    query: Select[tuple[User]] = select(User).where(User.organization_id == organization_id)
    if visible_ids is not None:
        query = query.where(User.id.in_(visible_ids))
    if status:
        query = query.where(User.status == status)
    if role_id:
        query = query.where(User.role_id == role_id)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                User.full_name.ilike(pattern),
                User.username.ilike(pattern),
                User.email.ilike(pattern),
            )
        )
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.scalars(query.order_by(User.full_name).limit(limit).offset(offset))
    return list(rows.unique()), total


async def count_by_status(
    session: AsyncSession, *, organization_id: uuid.UUID, visible_ids: set[uuid.UUID] | None
) -> dict[str, int]:
    """Users per status (for the tab badges), limited to ``visible_ids`` when given."""
    query = (
        select(User.status, func.count())
        .where(User.organization_id == organization_id)
        .group_by(User.status)
    )
    if visible_ids is not None:
        query = query.where(User.id.in_(visible_ids))
    counts = {"active": 0, "paused": 0, "archived": 0, "invited": 0}
    counts.update({status: count for status, count in (await session.execute(query)).all()})
    return counts


async def teams_for_users(
    session: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[uuid.UUID, str, str]]]:
    """``{user_id: [(team_id, team_name, team_role), ...]}`` for the given users."""
    if not user_ids:
        return {}
    rows = await session.execute(
        select(TeamMember.user_id, Team.id, Team.name, TeamMember.team_role)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.user_id.in_(user_ids))
        .order_by(Team.name)
    )
    result: dict[uuid.UUID, list[tuple[uuid.UUID, str, str]]] = {}
    for user_id, team_id, name, role in rows.all():
        result.setdefault(user_id, []).append((team_id, name, role))
    return result
