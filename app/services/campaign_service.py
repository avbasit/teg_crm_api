"""Campaigns: list, create, edit, status, round-robin agents and website keys.

Rights (menu `campaigns`): view to read; create to add; edit for details, status
and website keys; assign to change the round-robin; delete to delete.
"""

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import invalid, not_found
from app.models.configuration import LeadStatus, Market, Program
from app.models.identity import Role, User
from app.models.intake import ApiKey
from app.models.leads import Campaign, CampaignAgent, Lead
from app.repositories.audit_repository import add_audit_log, diff
from app.schemas.campaigns import (
    DEFAULT_SOURCE,
    AgentIn,
    AgentOption,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    CampaignAgentOut,
    CampaignCounts,
    CampaignCreate,
    CampaignDetail,
    CampaignListItem,
    CampaignStats,
    CampaignUpdate,
    NamedRef,
    StatusCount,
)
from app.schemas.users import UserRef
from app.services.access import CurrentUser
from app.services.intake_service import now_utc

KEY_PREFIX = "tegk_"
_DETAIL_FIELDS = ["name", "source", "program_id", "market_id", "assignment_strategy"]


def hash_key(key: str) -> str:
    """SHA-256 of an API key (keys are long random strings, so a plain hash is enough)."""
    return hashlib.sha256(key.encode()).hexdigest()


def intake_url(request: Request) -> str:
    """Public address website forms post to."""
    base = get_settings().public_api_url or str(request.base_url)
    host = request.base_url.hostname or ""
    if base.startswith("http://") and host not in ("localhost", "127.0.0.1", "testserver"):
        base = "https://" + base.removeprefix("http://")  # Vercel terminates TLS in front of us
    return f"{base.rstrip('/')}{get_settings().api_prefix}/intake/leads"


def _audit(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    entity_id: Any,
    action: str,
    changes: dict[str, Any],
) -> None:
    add_audit_log(
        session,
        organization_id=current.organization_id,
        actor_user_id=current.user.id,
        entity_type="campaign",
        entity_id=entity_id,
        action=action,
        changes=changes,
        request=request,
    )


# --- Reading ------------------------------------------------------------------------------


async def get_campaign(
    session: AsyncSession, current: CurrentUser, campaign_id: uuid.UUID
) -> Campaign:
    """A campaign in the caller's organisation (404 otherwise)."""
    campaign = await session.scalar(
        select(Campaign)
        .where(
            Campaign.id == campaign_id,
            Campaign.organization_id == current.organization_id,
            Campaign.deleted_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )
    if campaign is None:
        raise not_found("Campaign")
    return campaign


def _ref(entity: Program | Market | None) -> NamedRef | None:
    return NamedRef(id=entity.id, name=entity.name) if entity else None


async def _counts_for(
    session: AsyncSession, ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, tuple[int, int]]]:
    if not ids:
        return {}, {}
    leads = dict(
        (
            await session.execute(
                select(Lead.campaign_id, func.count())
                .where(Lead.campaign_id.in_(ids), Lead.deleted_at.is_(None))
                .group_by(Lead.campaign_id)
            )
        ).all()
    )
    receiving = and_(CampaignAgent.is_active.is_(True), User.status == "active")
    agents = {
        row[0]: (row[1], row[2])
        for row in (
            await session.execute(
                select(
                    CampaignAgent.campaign_id,
                    func.count(),
                    func.count(case((receiving, 1))),
                )
                .join(User, User.id == CampaignAgent.user_id)
                .where(CampaignAgent.campaign_id.in_(ids))
                .group_by(CampaignAgent.campaign_id)
            )
        ).all()
    }
    return leads, agents


def _list_item(
    campaign: Campaign, leads: dict[uuid.UUID, int], agents: dict[uuid.UUID, tuple[int, int]]
) -> CampaignListItem:
    total, active = agents.get(campaign.id, (0, 0))
    return CampaignListItem(
        id=campaign.id,
        name=campaign.name,
        campaign_type=campaign.campaign_type,  # type: ignore[arg-type]
        source=campaign.source,
        status=campaign.status,  # type: ignore[arg-type]
        program=_ref(campaign.program),
        market=_ref(campaign.market),
        assignment_strategy=campaign.assignment_strategy,  # type: ignore[arg-type]
        agents_total=total,
        agents_active=active,
        leads_total=leads.get(campaign.id, 0),
        last_lead_at=campaign.last_lead_at,
        created_at=campaign.created_at,
    )


async def list_campaigns(
    session: AsyncSession,
    current: CurrentUser,
    *,
    status: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> tuple[list[CampaignListItem], int]:
    """A page of campaigns, newest lead activity first."""
    query = select(Campaign).where(
        Campaign.organization_id == current.organization_id, Campaign.deleted_at.is_(None)
    )
    if status:
        query = query.where(Campaign.status == status)
    if search:
        query = query.where(Campaign.name.ilike(f"%{search.strip()}%"))
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        (
            await session.scalars(
                query.order_by(
                    Campaign.last_lead_at.desc().nulls_last(), Campaign.created_at.desc()
                )
                .limit(limit)
                .offset(offset)
            )
        ).unique()
    )
    leads, agents = await _counts_for(session, [c.id for c in rows])
    return [_list_item(c, leads, agents) for c in rows], total


async def counts(session: AsyncSession, current: CurrentUser) -> CampaignCounts:
    """Campaigns per status tab."""
    rows = dict(
        (
            await session.execute(
                select(Campaign.status, func.count())
                .where(
                    Campaign.organization_id == current.organization_id,
                    Campaign.deleted_at.is_(None),
                )
                .group_by(Campaign.status)
            )
        ).all()
    )
    return CampaignCounts(**{s: rows.get(s, 0) for s in ("active", "paused", "archived", "draft")})


async def _agents(session: AsyncSession, campaign: Campaign) -> list[CampaignAgentOut]:
    rows = list(
        (
            await session.scalars(
                select(CampaignAgent)
                .where(CampaignAgent.campaign_id == campaign.id)
                .order_by(
                    CampaignAgent.rr_position, CampaignAgent.created_at, CampaignAgent.user_id
                )
                .execution_options(populate_existing=True)
            )
        ).unique()
    )
    assigned = dict(
        (
            await session.execute(
                select(Lead.owner_user_id, func.count())
                .where(Lead.campaign_id == campaign.id, Lead.deleted_at.is_(None))
                .group_by(Lead.owner_user_id)
            )
        ).all()
    )
    receiving = [
        a for a in rows if a.is_active and a.user.status == "active" and a.user.is_assignable
    ]
    next_id = receiving[0].user_id if receiving else None
    return [
        CampaignAgentOut(
            user=UserRef.model_validate(agent.user),
            user_status=agent.user.status,
            is_active=agent.is_active,
            is_next=campaign.assignment_strategy == "round_robin" and agent.user_id == next_id,
            receiving=agent in receiving,
            leads_assigned=assigned.get(agent.user_id, 0),
        )
        for agent in sorted(rows, key=lambda a: a.user.full_name.lower())
    ]


async def _stats(session: AsyncSession, campaign: Campaign) -> CampaignStats:
    base = and_(Lead.campaign_id == campaign.id, Lead.deleted_at.is_(None))
    since = now_utc() - timedelta(days=7)
    row = (
        await session.execute(
            select(
                func.count(),
                func.count(case((Lead.first_contacted_at.is_(None), 1))),
                func.count(case((Lead.owner_user_id.is_(None), 1))),
                func.count(case((Lead.is_duplicate.is_(True), 1))),
                func.count(case((Lead.created_at >= since, 1))),
            ).where(base)
        )
    ).one()
    by_status = (
        await session.execute(
            select(LeadStatus.id, LeadStatus.name, LeadStatus.color, func.count(Lead.id))
            .join(Lead, and_(Lead.status_id == LeadStatus.id, base))
            .group_by(LeadStatus.id, LeadStatus.name, LeadStatus.color, LeadStatus.sort_order)
            .order_by(LeadStatus.sort_order)
        )
    ).all()
    return CampaignStats(
        total=row[0],
        not_contacted=row[1],
        unassigned=row[2],
        duplicates=row[3],
        last_7_days=row[4],
        by_status=[StatusCount(id=r[0], name=r[1], color=r[2], count=r[3]) for r in by_status],
    )


async def detail(
    session: AsyncSession, request: Request, current: CurrentUser, campaign_id: uuid.UUID
) -> CampaignDetail:
    """Campaign page data."""
    campaign = await get_campaign(session, current, campaign_id)
    leads, agents = await _counts_for(session, [campaign.id])
    creator = (
        await session.get(User, campaign.created_by_user_id)
        if campaign.created_by_user_id
        else None
    )
    return CampaignDetail(
        **_list_item(campaign, leads, agents).model_dump(),
        agents=await _agents(session, campaign),
        stats=await _stats(session, campaign),
        created_by=UserRef.model_validate(creator) if creator else None,
        intake_url=intake_url(request),
    )


async def agent_options(session: AsyncSession, current: CurrentUser) -> list[AgentOption]:
    """Everyone who can receive leads (active or invited, and assignable)."""
    rows = await session.execute(
        select(User, Role.name)
        .join(Role, Role.id == User.role_id)
        .where(
            User.organization_id == current.organization_id,
            User.status.in_(("active", "invited")),
            User.is_assignable.is_(True),
        )
        .order_by(User.full_name)
    )
    return [
        AgentOption(
            id=user.id,
            full_name=user.full_name,
            username=user.username,
            role_name=role_name,
            status=user.status,
        )
        for user, role_name in rows.unique().all()
    ]


# --- Writing ------------------------------------------------------------------------------


async def _check_lookups(
    session: AsyncSession,
    current: CurrentUser,
    program_id: uuid.UUID | None,
    market_id: uuid.UUID | None,
) -> None:
    if program_id:
        program = await session.get(Program, program_id)
        if program is None or program.organization_id != current.organization_id:
            raise invalid("unknown_program", "That programme doesn't exist.")
    if market_id:
        market = await session.get(Market, market_id)
        if market is None or market.organization_id != current.organization_id:
            raise invalid("unknown_market", "That market doesn't exist.")


async def _check_agents(session: AsyncSession, current: CurrentUser, agents: list[AgentIn]) -> None:
    ids = {a.user_id for a in agents}
    if len(ids) != len(agents):
        raise invalid("duplicate_agent", "Each agent can be in the round-robin only once.")
    allowed = {o.id for o in await agent_options(session, current)}
    if ids - allowed:
        raise invalid("invalid_agent", "Agents must be active or invited users who take leads.")


async def create(
    session: AsyncSession, request: Request, current: CurrentUser, data: CampaignCreate
) -> Campaign:
    """Create an active campaign with its round-robin."""
    await _check_lookups(session, current, data.program_id, data.market_id)
    await _check_agents(session, current, data.agents)
    campaign = Campaign(
        organization_id=current.organization_id,
        name=data.name.strip(),
        campaign_type=data.campaign_type,
        source=data.source or DEFAULT_SOURCE[data.campaign_type],
        program_id=data.program_id,
        market_id=data.market_id,
        status="active",
        assignment_strategy=data.assignment_strategy,
        created_by_user_id=current.user.id,
    )
    session.add(campaign)
    await session.flush()
    session.add_all(
        CampaignAgent(campaign_id=campaign.id, user_id=a.user_id, is_active=a.is_active)
        for a in data.agents
    )
    _audit(
        session,
        request,
        current,
        campaign.id,
        "create",
        diff({}, {**data.model_dump(mode="json", exclude={"agents"}), "agents": len(data.agents)}),
    )
    await session.commit()
    return await get_campaign(session, current, campaign.id)


async def update(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    campaign_id: uuid.UUID,
    data: CampaignUpdate,
) -> Campaign:
    """Edit name, source, programme, market or assignment."""
    campaign = await get_campaign(session, current, campaign_id)
    changes = data.model_dump(exclude_unset=True)
    for field in ("name", "source", "assignment_strategy"):
        if field in changes and changes[field] is None:
            raise invalid("required", f"{field.replace('_', ' ').capitalize()} can't be empty.")
    await _check_lookups(session, current, changes.get("program_id"), changes.get("market_id"))
    before = {f: getattr(campaign, f) for f in _DETAIL_FIELDS}
    for field, value in changes.items():
        setattr(campaign, field, value.strip() if isinstance(value, str) else value)
    changed = diff(before, {f: getattr(campaign, f) for f in _DETAIL_FIELDS})
    if changed:
        _audit(session, request, current, campaign.id, "update", changed)
        await session.commit()
    return await get_campaign(session, current, campaign.id)


async def set_status(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    campaign_id: uuid.UUID,
    status: str,
) -> Campaign:
    """Activate, pause or archive (paused campaigns still take leads)."""
    campaign = await get_campaign(session, current, campaign_id)
    if campaign.status != status:
        _audit(
            session, request, current, campaign.id, "status", {"status": [campaign.status, status]}
        )
        campaign.status = status
        await session.commit()
    return await get_campaign(session, current, campaign.id)


async def delete(
    session: AsyncSession, request: Request, current: CurrentUser, campaign_id: uuid.UUID
) -> None:
    """Soft-delete a campaign; its leads keep the campaign name for history."""
    campaign = await get_campaign(session, current, campaign_id)
    campaign.deleted_at = now_utc()
    for key in await session.scalars(
        select(ApiKey).where(ApiKey.campaign_id == campaign.id, ApiKey.revoked_at.is_(None))
    ):
        key.revoked_at = campaign.deleted_at
    _audit(session, request, current, campaign.id, "delete", {"name": [campaign.name, None]})
    await session.commit()


async def set_agents(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    campaign_id: uuid.UUID,
    agents: list[AgentIn],
) -> None:
    """Replace the round-robin list. New agents join at the front of the queue."""
    campaign = await get_campaign(session, current, campaign_id)
    await _check_agents(session, current, agents)
    existing = {
        a.user_id: a
        for a in await session.scalars(
            select(CampaignAgent).where(CampaignAgent.campaign_id == campaign.id)
        )
    }
    front = min((a.rr_position for a in existing.values()), default=0)
    wanted = {a.user_id: a for a in agents}
    for user_id, row in existing.items():
        if user_id not in wanted:
            await session.delete(row)
    for user_id, item in wanted.items():
        if user_id in existing:
            existing[user_id].is_active = item.is_active
        else:
            session.add(
                CampaignAgent(
                    campaign_id=campaign.id,
                    user_id=user_id,
                    is_active=item.is_active,
                    rr_position=front,
                )
            )
    before = sorted(str(u) for u in existing)
    after = sorted(str(u) for u in wanted)
    if before != after:
        _audit(session, request, current, campaign.id, "agents", {"agents": [before, after]})
    await session.commit()


async def toggle_agent(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    campaign_id: uuid.UUID,
    user_id: uuid.UUID,
    is_active: bool,
) -> None:
    """Switch one agent's round-robin on or off."""
    campaign = await get_campaign(session, current, campaign_id)
    agent = await session.get(CampaignAgent, (campaign.id, user_id))
    if agent is None:
        raise not_found("Agent")
    if agent.is_active != is_active:
        agent.is_active = is_active
        _audit(
            session,
            request,
            current,
            campaign.id,
            "agent_toggle",
            {"agent": str(user_id), "is_active": [not is_active, is_active]},
        )
        await session.commit()


# --- Website keys -------------------------------------------------------------------------


def _key_out(key: ApiKey, creator: User | None) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id,
        name=key.name,
        key_prefix=key.key_prefix,
        allowed_origins=list(key.allowed_origins or []),
        created_by=UserRef.model_validate(creator) if creator else None,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
    )


async def list_keys(
    session: AsyncSession, current: CurrentUser, campaign_id: uuid.UUID
) -> list[ApiKeyOut]:
    """The campaign's website keys, newest first (revoked ones included)."""
    campaign = await get_campaign(session, current, campaign_id)
    rows = (
        await session.execute(
            select(ApiKey, User)
            .outerjoin(User, User.id == ApiKey.created_by_user_id)
            .where(ApiKey.campaign_id == campaign.id)
            .order_by(ApiKey.created_at.desc())
        )
    ).unique()
    return [_key_out(key, user) for key, user in rows.all()]


async def create_key(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    campaign_id: uuid.UUID,
    data: ApiKeyCreate,
) -> ApiKeyCreated:
    """Issue a website key for the campaign; the secret is returned only now."""
    campaign = await get_campaign(session, current, campaign_id)
    secret = KEY_PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(
        organization_id=current.organization_id,
        name=data.name.strip(),
        key_prefix=secret[:12],
        key_hash=hash_key(secret),
        scopes=["leads:create"],
        campaign_id=campaign.id,
        allowed_origins=data.allowed_origins,
        created_by_user_id=current.user.id,
    )
    session.add(key)
    await session.flush()
    _audit(
        session,
        request,
        current,
        campaign.id,
        "api_key_create",
        {"key": [None, key.key_prefix], "allowed_origins": [None, data.allowed_origins]},
    )
    await session.commit()
    await session.refresh(key)
    return ApiKeyCreated(
        **_key_out(key, current.user).model_dump(), key=secret, intake_url=intake_url(request)
    )


async def revoke_key(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    campaign_id: uuid.UUID,
    key_id: uuid.UUID,
) -> None:
    """Stop a key working immediately."""
    campaign = await get_campaign(session, current, campaign_id)
    key = await session.get(ApiKey, key_id)
    if key is None or key.campaign_id != campaign.id:
        raise not_found("Key")
    if key.revoked_at is None:
        key.revoked_at = now_utc()
        _audit(
            session,
            request,
            current,
            campaign.id,
            "api_key_revoke",
            {"key": [key.key_prefix, None]},
        )
        await session.commit()
