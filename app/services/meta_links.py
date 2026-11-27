"""Linking CRM campaigns to Meta ad campaigns, ad sets and ads (Jaldi's model).

A CRM campaign can be linked to whole ad campaigns, to individual ad sets or to
single ads. When a lead arrives, the most specific link wins (ad, then ad set,
then ad campaign); see ``meta_service._route``. Each Meta item feeds at most one
CRM campaign, so linking it here moves it from wherever it was linked before.
"""

import uuid
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import invalid, not_found
from app.core.secret_box import decrypt
from app.integrations.meta import MetaError, get_meta
from app.models.ads import Ad, AdAccount, AdCampaign, AdSet, Integration
from app.models.leads import Campaign, Lead
from app.repositories.audit_repository import add_audit_log
from app.schemas.integrations import AdLinkIn, AdLinkOut, AdNode, LinkedTo
from app.services.access import CurrentUser
from app.services.intake_service import now_utc
from app.services.meta_service import PROVIDER, get_integration

EDGES = {"campaigns": "campaigns", "adsets": "adsets", "ads": "ads"}


async def _account(
    session: AsyncSession, current: CurrentUser, ad_account_id: uuid.UUID
) -> tuple[Integration, AdAccount]:
    integration = await get_integration(session, current.organization_id)
    if integration is None or integration.status != "connected":
        raise invalid("not_connected", "Connect Meta first (Settings › Integrations).")
    account = next((a for a in integration.ad_accounts if a.id == ad_account_id), None)
    if account is None:
        raise not_found("Ad account")
    return integration, account


async def _names(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(select(Campaign.id, Campaign.name).where(Campaign.id.in_(ids)))
    return dict(rows.all())


async def browse(
    session: AsyncSession,
    current: CurrentUser,
    ad_account_id: uuid.UUID,
    level: str,
    parent: str | None,
) -> list[AdNode]:
    """Live list of an ad account's campaigns, a campaign's ad sets or an ad set's ads."""
    integration, account = await _account(session, current, ad_account_id)
    if level != "campaigns" and not parent:
        raise invalid("parent_required", "Choose the campaign or ad set first.")
    node = f"act_{account.external_account_id}" if level == "campaigns" else str(parent)
    try:
        items = await get_meta().children(
            node, EDGES[level], decrypt(integration.access_token_encrypted or "")
        )
    except MetaError as exc:
        raise invalid("meta_error", f"Meta: {exc}") from exc

    external_ids = [str(item["id"]) for item in items]
    model = {"campaigns": AdCampaign, "adsets": AdSet, "ads": Ad}[level]
    query = select(model.external_id, model.crm_campaign_id).where(
        model.external_id.in_(external_ids), model.crm_campaign_id.is_not(None)
    )
    if model is AdCampaign:
        query = query.where(
            AdCampaign.organization_id == current.organization_id, AdCampaign.provider == PROVIDER
        )
    links = dict((await session.execute(query)).all())
    names = await _names(session, set(links.values()))
    return [
        AdNode(
            external_id=str(item["id"]),
            name=item.get("name") or str(item["id"]),
            status=item.get("effective_status"),
            linked_to=LinkedTo(
                id=links[str(item["id"])], name=names.get(links[str(item["id"])], "")
            )
            if str(item["id"]) in links
            else None,
        )
        for item in items
    ]


async def _upsert(
    session: AsyncSession, org_id: uuid.UUID, link: AdLinkIn
) -> tuple[AdCampaign, AdSet | None, Ad | None]:
    ad_campaign = await session.scalar(
        select(AdCampaign).where(
            AdCampaign.organization_id == org_id,
            AdCampaign.provider == PROVIDER,
            AdCampaign.external_id == link.campaign_id,
        )
    )
    if ad_campaign is None:
        ad_campaign = AdCampaign(
            organization_id=org_id,
            provider=PROVIDER,
            external_id=link.campaign_id,
            name=link.campaign_name,
        )
        session.add(ad_campaign)
        await session.flush()
    ad_campaign.name = link.campaign_name or ad_campaign.name
    ad_campaign.ad_account_id = link.ad_account_id
    if link.level == "campaign":
        return ad_campaign, None, None
    if not link.adset_id:
        raise invalid("adset_required", "An ad set or ad link needs its ad set.")
    ad_set = await session.scalar(
        select(AdSet).where(
            AdSet.ad_campaign_id == ad_campaign.id, AdSet.external_id == link.adset_id
        )
    )
    if ad_set is None:
        ad_set = AdSet(
            ad_campaign_id=ad_campaign.id,
            external_id=link.adset_id,
            name=link.adset_name or link.adset_id,
        )
        session.add(ad_set)
        await session.flush()
    ad_set.name = link.adset_name or ad_set.name
    if link.level == "adset":
        return ad_campaign, ad_set, None
    if not link.ad_id:
        raise invalid("ad_required", "An ad link needs the ad.")
    ad = await session.scalar(
        select(Ad).where(Ad.ad_set_id == ad_set.id, Ad.external_id == link.ad_id)
    )
    if ad is None:
        ad = Ad(ad_set_id=ad_set.id, external_id=link.ad_id, name=link.ad_name or link.ad_id)
        session.add(ad)
        await session.flush()
    ad.name = link.ad_name or ad.name
    return ad_campaign, ad_set, ad


async def list_links(
    session: AsyncSession, current: CurrentUser, campaign_id: uuid.UUID
) -> list[AdLinkOut]:
    """Everything in Meta that feeds this CRM campaign."""
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None or campaign.organization_id != current.organization_id:
        raise not_found("Campaign")
    since = now_utc() - timedelta(days=30)
    accounts = dict((await session.execute(select(AdAccount.id, AdAccount.name))).all())
    links: list[AdLinkOut] = []

    async def leads_for(condition: Any) -> int:
        return (
            await session.scalar(
                select(func.count())
                .select_from(Lead)
                .join(Ad, Ad.id == Lead.ad_id)
                .join(AdSet, AdSet.id == Ad.ad_set_id)
                .where(condition, Lead.created_at >= since, Lead.deleted_at.is_(None))
            )
            or 0
        )

    for row in await session.scalars(
        select(AdCampaign)
        .where(AdCampaign.crm_campaign_id == campaign.id)
        .order_by(AdCampaign.name)
    ):
        links.append(
            AdLinkOut(
                level="campaign",
                ad_account_id=row.ad_account_id or uuid.UUID(int=0),
                ad_account_name=accounts.get(row.ad_account_id),
                campaign_id=row.external_id,
                campaign_name=row.name,
                leads_last_30_days=await leads_for(AdSet.ad_campaign_id == row.id),
            )  # fmt: skip
        )
    for ad_set, parent in (
        await session.execute(
            select(AdSet, AdCampaign)
            .join(AdCampaign, AdCampaign.id == AdSet.ad_campaign_id)
            .where(AdSet.crm_campaign_id == campaign.id)
            .order_by(AdCampaign.name, AdSet.name)
        )
    ).all():
        links.append(
            AdLinkOut(
                level="adset",
                ad_account_id=parent.ad_account_id or uuid.UUID(int=0),
                ad_account_name=accounts.get(parent.ad_account_id),
                campaign_id=parent.external_id,
                campaign_name=parent.name,
                adset_id=ad_set.external_id,
                adset_name=ad_set.name,
                leads_last_30_days=await leads_for(AdSet.id == ad_set.id),
            )  # fmt: skip
        )
    for ad, ad_set, parent in (
        await session.execute(
            select(Ad, AdSet, AdCampaign)
            .join(AdSet, AdSet.id == Ad.ad_set_id)
            .join(AdCampaign, AdCampaign.id == AdSet.ad_campaign_id)
            .where(Ad.crm_campaign_id == campaign.id)
            .order_by(AdCampaign.name, AdSet.name, Ad.name)
        )
    ).all():
        links.append(
            AdLinkOut(
                level="ad",
                ad_account_id=parent.ad_account_id or uuid.UUID(int=0),
                ad_account_name=accounts.get(parent.ad_account_id),
                campaign_id=parent.external_id,
                campaign_name=parent.name,
                adset_id=ad_set.external_id,
                adset_name=ad_set.name,
                ad_id=ad.external_id,
                ad_name=ad.name,
                leads_last_30_days=await leads_for(Ad.id == ad.id),
            )  # fmt: skip
        )
    return links


async def set_links(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    campaign_id: uuid.UUID,
    links: list[AdLinkIn],
) -> list[AdLinkOut]:
    """Replace the campaign's Meta links (items move here from any other campaign)."""
    campaign = await session.get(Campaign, campaign_id)
    if (
        campaign is None
        or campaign.organization_id != current.organization_id
        or campaign.deleted_at
    ):
        raise not_found("Campaign")
    integration = await get_integration(session, current.organization_id)
    account_ids = {a.id for a in integration.ad_accounts} if integration else set()
    if any(link.ad_account_id not in account_ids for link in links):
        raise invalid("unknown_ad_account", "Choose an ad account from the connected Meta account.")
    before = len(await list_links(session, current, campaign.id))
    for model in (AdCampaign, AdSet, Ad):  # clear this campaign's old links
        await session.execute(
            update(model).where(model.crm_campaign_id == campaign.id).values(crm_campaign_id=None)
        )
    for link in links:
        ad_campaign, ad_set, ad = await _upsert(session, current.organization_id, link)
        target = ad or ad_set or ad_campaign
        target.crm_campaign_id = campaign.id
    add_audit_log(
        session,
        organization_id=current.organization_id,
        actor_user_id=current.user.id,
        entity_type="campaign",
        entity_id=campaign.id,
        action="meta_links",
        changes={"links": [before, len(links)]},
        request=request,
    )
    await session.commit()
    return await list_links(session, current, campaign.id)
