"""`/teams`: teams of agents and their managers. Rights: menu `teams`."""

import uuid

from fastapi import APIRouter, Request, Response, status

from app.core.database import DbSession
from app.dependencies import CurrentUserDep, RequirePermission
from app.schemas.common import ErrorResponse
from app.schemas.teams import TeamCreate, TeamMembersUpdate, TeamOut, TeamUpdate
from app.services import team_service

router = APIRouter(prefix="/teams", tags=["teams"], responses={403: {"model": ErrorResponse}})
ERRORS = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.get("", response_model=list[TeamOut], dependencies=[RequirePermission("teams", "view")])
async def list_teams(current: CurrentUserDep, session: DbSession) -> list[TeamOut]:
    """Teams you can see (Sales Managers: their own teams)."""
    return [TeamOut.model_validate(t) for t in await team_service.list_teams(session, current)]


@router.post(
    "",
    response_model=TeamOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    dependencies=[RequirePermission("teams", "create")],
)
async def create_team(
    body: TeamCreate, request: Request, current: CurrentUserDep, session: DbSession
) -> TeamOut:
    """Create a team with managers and members."""
    return TeamOut.model_validate(await team_service.create_team(session, request, current, body))


@router.get(
    "/{team_id}",
    response_model=TeamOut,
    responses=ERRORS,
    dependencies=[RequirePermission("teams", "view")],
)
async def get_team(team_id: uuid.UUID, current: CurrentUserDep, session: DbSession) -> TeamOut:
    """One team."""
    return TeamOut.model_validate(await team_service.get_team(session, current, team_id))


@router.patch(
    "/{team_id}",
    response_model=TeamOut,
    responses=ERRORS,
    dependencies=[RequirePermission("teams", "edit")],
)
async def update_team(
    team_id: uuid.UUID,
    body: TeamUpdate,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> TeamOut:
    """Rename a team or change its description."""
    team = await team_service.update_team(session, request, current, team_id, body)
    return TeamOut.model_validate(team)


@router.put(
    "/{team_id}/members",
    response_model=TeamOut,
    responses=ERRORS,
    dependencies=[RequirePermission("teams", "edit")],
)
async def set_members(
    team_id: uuid.UUID,
    body: TeamMembersUpdate,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> TeamOut:
    """Replace the team's managers and members."""
    team = await team_service.set_members(session, request, current, team_id, body.members)
    return TeamOut.model_validate(team)


@router.delete(
    "/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERRORS,
    dependencies=[RequirePermission("teams", "delete")],
)
async def delete_team(
    team_id: uuid.UUID, request: Request, current: CurrentUserDep, session: DbSession
) -> Response:
    """Delete a team. Its users are not affected."""
    await team_service.delete_team(session, request, current, team_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
