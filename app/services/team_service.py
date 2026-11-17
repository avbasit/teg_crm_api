"""Teams: create, rename, set members, delete (Teams screen)."""

import uuid

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import conflict, invalid, not_found
from app.models.identity import Team, TeamMember, User
from app.repositories.audit_repository import add_audit_log, diff
from app.schemas.teams import TeamCreate, TeamMemberIn, TeamUpdate
from app.services.access import CurrentUser

MENU = "teams"


async def list_teams(session: AsyncSession, current: CurrentUser) -> list[Team]:
    """All teams, or only your own teams when your scope on Teams isn't `all`."""
    query = select(Team).where(Team.organization_id == current.organization_id).order_by(Team.name)
    if current.scope(MENU) != "all":
        mine = select(TeamMember.team_id).where(TeamMember.user_id == current.user.id)
        query = query.where(Team.id.in_(mine))
    return list(await session.scalars(query))


async def get_team(session: AsyncSession, current: CurrentUser, team_id: uuid.UUID) -> Team:
    """One team inside the caller's scope (else 404)."""
    for team in await list_teams(session, current):
        if team.id == team_id:
            return team
    raise not_found("Team")


async def _validated_members(
    session: AsyncSession, current: CurrentUser, members: list[TeamMemberIn]
) -> list[TeamMemberIn]:
    ids = [m.user_id for m in members]
    if len(ids) != len(set(ids)):
        raise invalid("duplicate_member", "Each person can appear in a team only once.")
    if not ids:
        return members
    found = set(
        await session.scalars(
            select(User.id).where(
                User.id.in_(ids),
                User.organization_id == current.organization_id,
                User.status != "archived",
            )
        )
    )
    if found != set(ids):
        raise invalid("invalid_member", "Team members must be existing, non-archived users.")
    return members


def _member_snapshot(team: Team) -> dict[str, str]:
    return {str(m.user_id): m.team_role for m in team.members}


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if "uq_teams_organization_id_name" in str(exc.orig):
            raise conflict("team_name_taken", "A team with that name already exists.") from exc
        raise


async def create_team(
    session: AsyncSession, request: Request, current: CurrentUser, data: TeamCreate
) -> Team:
    """Create a team with its members."""
    members = await _validated_members(session, current, data.members)
    team = Team(
        organization_id=current.organization_id,
        name=data.name.strip(),
        description=data.description,
        members=[TeamMember(user_id=m.user_id, team_role=m.team_role) for m in members],
    )
    session.add(team)
    await session.flush()
    add_audit_log(
        session,
        organization_id=current.organization_id,
        actor_user_id=current.user.id,
        entity_type="team",
        entity_id=team.id,
        action="create",
        changes={"name": [None, team.name], "members": [None, _member_snapshot(team)]},
        request=request,
    )
    await _commit(session)
    return await _reload(session, team.id)


async def _reload(session: AsyncSession, team_id: uuid.UUID) -> Team:
    """Fresh copy of a team with members and their users (after members changed)."""
    team = await session.scalar(
        select(Team)
        .where(Team.id == team_id)
        .options(selectinload(Team.members).joinedload(TeamMember.user))
        .execution_options(populate_existing=True)
    )
    if team is None:
        raise not_found("Team")
    return team


async def update_team(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    team_id: uuid.UUID,
    data: TeamUpdate,
) -> Team:
    """Rename or describe a team."""
    team = await get_team(session, current, team_id)
    before = {"name": team.name, "description": team.description}
    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "name":
            if not value:
                raise invalid("name_required", "Team name can't be empty.")
            value = value.strip()
        setattr(team, field, value)
    changes = diff(before, {"name": team.name, "description": team.description})
    if changes:
        add_audit_log(
            session,
            organization_id=current.organization_id,
            actor_user_id=current.user.id,
            entity_type="team",
            entity_id=team.id,
            action="update",
            changes=changes,
            request=request,
        )
        await _commit(session)
    return team


async def set_members(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    team_id: uuid.UUID,
    members: list[TeamMemberIn],
) -> Team:
    """Replace the member list (managers and members)."""
    team = await get_team(session, current, team_id)
    members = await _validated_members(session, current, members)
    before = _member_snapshot(team)
    team.members.clear()
    await session.flush()
    team.members.extend(TeamMember(user_id=m.user_id, team_role=m.team_role) for m in members)
    after = {str(m.user_id): m.team_role for m in members}
    if before != after:
        add_audit_log(
            session,
            organization_id=current.organization_id,
            actor_user_id=current.user.id,
            entity_type="team",
            entity_id=team.id,
            action="members_change",
            changes={"members": [before, after]},
            request=request,
        )
    await _commit(session)
    return await _reload(session, team.id)


async def delete_team(
    session: AsyncSession, request: Request, current: CurrentUser, team_id: uuid.UUID
) -> None:
    """Delete a team (its users are not affected)."""
    team = await get_team(session, current, team_id)
    add_audit_log(
        session,
        organization_id=current.organization_id,
        actor_user_id=current.user.id,
        entity_type="team",
        entity_id=team.id,
        action="delete",
        changes={"name": [team.name, None], "members": [_member_snapshot(team), None]},
        request=request,
    )
    await session.delete(team)
    await _commit(session)
