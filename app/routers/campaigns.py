"""`/campaigns`: campaign list and page, round-robin agents, website keys.

Rights (menu `campaigns`): view to read; create to add; edit for details, status
and website keys; assign to change who is in the round-robin; delete to delete.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response, status

from app.core.database import DbSession
from app.dependencies import CurrentUserDep, RequirePermission
from app.schemas.campaigns import (
    AgentOption,
    AgentsSet,
    AgentToggle,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    CampaignCounts,
    CampaignCreate,
    CampaignDetail,
    CampaignListItem,
    CampaignStatusChange,
    CampaignUpdate,
)
from app.schemas.common import ErrorResponse, Page
from app.schemas.integrations import AdLinkOut, AdLinksSet
from app.services import campaign_service, meta_links

router = APIRouter(
    prefix="/campaigns", tags=["campaigns"], responses={403: {"model": ErrorResponse}}
)
ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
MENU = "campaigns"
VIEW = RequirePermission(MENU, "view")
EDIT = RequirePermission(MENU, "edit")
ASSIGN = RequirePermission(MENU, "assign")


@router.get("", response_model=Page[CampaignListItem], dependencies=[VIEW])
async def list_campaigns(
    current: CurrentUserDep,
    session: DbSession,
    status_: Annotated[
        Literal["draft", "active", "paused", "archived"] | None, Query(alias="status")
    ] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CampaignListItem]:
    """Campaigns by status tab, with lead and agent counts."""
    items, total = await campaign_service.list_campaigns(
        session, current, status=status_, search=search, limit=limit, offset=offset
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/counts", response_model=CampaignCounts, dependencies=[VIEW])
async def campaign_counts(current: CurrentUserDep, session: DbSession) -> CampaignCounts:
    """How many campaigns are in each tab."""
    return await campaign_service.counts(session, current)


@router.get("/agent-options", response_model=list[AgentOption], dependencies=[VIEW])
async def agent_options(current: CurrentUserDep, session: DbSession) -> list[AgentOption]:
    """People who can be put in a round-robin."""
    return await campaign_service.agent_options(session, current)


@router.post(
    "",
    response_model=CampaignDetail,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    dependencies=[RequirePermission(MENU, "create")],
)
async def create_campaign(
    body: CampaignCreate, request: Request, current: CurrentUserDep, session: DbSession
) -> CampaignDetail:
    """Create a campaign (active straight away) with its round-robin agents."""
    campaign = await campaign_service.create(session, request, current, body)
    return await campaign_service.detail(session, request, current, campaign.id)


@router.get("/{campaign_id}", response_model=CampaignDetail, responses=ERRORS, dependencies=[VIEW])
async def get_campaign(
    campaign_id: uuid.UUID, request: Request, current: CurrentUserDep, session: DbSession
) -> CampaignDetail:
    """Campaign page: details, round-robin, lead counts."""
    return await campaign_service.detail(session, request, current, campaign_id)


@router.patch(
    "/{campaign_id}", response_model=CampaignDetail, responses=ERRORS, dependencies=[EDIT]
)
async def update_campaign(
    campaign_id: uuid.UUID,
    body: CampaignUpdate,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> CampaignDetail:
    """Edit details."""
    await campaign_service.update(session, request, current, campaign_id, body)
    return await campaign_service.detail(session, request, current, campaign_id)


@router.patch(
    "/{campaign_id}/status", response_model=CampaignDetail, responses=ERRORS, dependencies=[EDIT]
)
async def change_status(
    campaign_id: uuid.UUID,
    body: CampaignStatusChange,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> CampaignDetail:
    """Activate, pause or archive. Paused campaigns still accept and assign leads."""
    await campaign_service.set_status(session, request, current, campaign_id, body.status)
    return await campaign_service.detail(session, request, current, campaign_id)


@router.delete(
    "/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERRORS,
    dependencies=[RequirePermission(MENU, "delete")],
)
async def delete_campaign(
    campaign_id: uuid.UUID, request: Request, current: CurrentUserDep, session: DbSession
) -> Response:
    """Delete a campaign (its leads stay; its website keys stop working)."""
    await campaign_service.delete(session, request, current, campaign_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{campaign_id}/agents", response_model=CampaignDetail, responses=ERRORS, dependencies=[ASSIGN]
)
async def set_agents(
    campaign_id: uuid.UUID,
    body: AgentsSet,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> CampaignDetail:
    """Replace who is in the round-robin."""
    await campaign_service.set_agents(session, request, current, campaign_id, body.agents)
    return await campaign_service.detail(session, request, current, campaign_id)


@router.patch(
    "/{campaign_id}/agents/{user_id}",
    response_model=CampaignDetail,
    responses=ERRORS,
    dependencies=[ASSIGN],
)
async def toggle_agent(
    campaign_id: uuid.UUID,
    user_id: uuid.UUID,
    body: AgentToggle,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> CampaignDetail:
    """Switch one agent on or off for this campaign."""
    await campaign_service.toggle_agent(
        session, request, current, campaign_id, user_id, body.is_active
    )
    return await campaign_service.detail(session, request, current, campaign_id)


@router.get(
    "/{campaign_id}/api-keys", response_model=list[ApiKeyOut], responses=ERRORS, dependencies=[EDIT]
)
async def list_keys(
    campaign_id: uuid.UUID, current: CurrentUserDep, session: DbSession
) -> list[ApiKeyOut]:
    """Website keys for this campaign."""
    return await campaign_service.list_keys(session, current, campaign_id)


@router.post(
    "/{campaign_id}/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    dependencies=[EDIT],
)
async def create_key(
    campaign_id: uuid.UUID,
    body: ApiKeyCreate,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> ApiKeyCreated:
    """Issue a website key. The secret is shown only in this response."""
    return await campaign_service.create_key(session, request, current, campaign_id, body)


@router.delete(
    "/{campaign_id}/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERRORS,
    dependencies=[EDIT],
)
async def revoke_key(
    campaign_id: uuid.UUID,
    key_id: uuid.UUID,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> Response:
    """Revoke a website key (it stops working at once)."""
    await campaign_service.revoke_key(session, request, current, campaign_id, key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{campaign_id}/meta-links",
    response_model=list[AdLinkOut],
    responses=ERRORS,
    dependencies=[VIEW],
)
async def meta_links_list(
    campaign_id: uuid.UUID, current: CurrentUserDep, session: DbSession
) -> list[AdLinkOut]:
    """Meta ad campaigns, ad sets and ads that feed this campaign."""
    return await meta_links.list_links(session, current, campaign_id)


@router.put(
    "/{campaign_id}/meta-links",
    response_model=list[AdLinkOut],
    responses=ERRORS,
    dependencies=[EDIT],
)
async def meta_links_set(
    campaign_id: uuid.UUID,
    body: AdLinksSet,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> list[AdLinkOut]:
    """Link this campaign to Meta (the most specific link wins when a lead arrives)."""
    return await meta_links.set_links(session, request, current, campaign_id, body.links)
