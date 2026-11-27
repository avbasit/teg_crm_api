"""Meta lead ads: connect, webhook intake, missed-lead sync and daily spend sync.

Decisions (25 Nov 2026), enforced here:
- One CRM campaign per Facebook ad campaign, **created automatically** on its first lead.
- Until go-live, leads go to **Unassigned** (``settings.assign_mode = "unassigned"``);
  switching to ``"campaign"`` uses the campaign's round-robin and duplicate routing.
- No backfill of past leads on connect; the sync only looks back a day at most.
- Tokens are stored encrypted; the long-lived user token lasts about 60 days, Page
  tokens don't expire. An expired token marks the integration "needs reconnect".
"""

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, forbidden, invalid
from app.core.secret_box import SecretBoxError, decrypt, encrypt, read_state, sign_state
from app.integrations.meta import MetaError, get_meta, valid_signature
from app.models.ads import (
    Ad,
    AdAccount,
    AdCampaign,
    AdInsightDaily,
    AdSet,
    Integration,
    IntegrationPage,
)
from app.models.intake import WebhookEvent
from app.models.leads import Campaign, Lead
from app.models.organization import Organization
from app.repositories.audit_repository import add_audit_log
from app.services.access import CurrentUser
from app.services.intake_service import Assignment, Incoming, Ingestor, Outcome, now_utc

log = logging.getLogger(__name__)
PROVIDER = "meta"
ASSIGN_MODES = ("unassigned", "campaign")
LEAD_ACTIONS = ("lead", "onsite_conversion.lead_grouped", "leadgen_grouped")
NOT_CONFIGURED = AppError(
    503,
    "meta_not_configured",
    "The Meta app isn't set up yet. See docs/integrations-setup.md (Vercel variables).",
)


def redirect_uri() -> str:
    """Where Facebook sends the user back (through the web app, so the cookie is present)."""
    config = get_settings()
    return f"{config.public_web_url.rstrip('/')}{config.api_prefix}/integrations/meta/callback"


def webhook_url() -> str:
    """The URL to register under Webhooks → Page → leadgen."""
    config = get_settings()
    base = (config.public_api_url or "https://tegcrmapi.vercel.app").rstrip("/")
    return f"{base}{config.api_prefix}/integrations/meta/webhook"


async def get_integration(session: AsyncSession, organization_id: uuid.UUID) -> Integration | None:
    """The organisation's Meta integration, if it was ever connected."""
    return await session.scalar(
        select(Integration)
        .where(Integration.organization_id == organization_id, Integration.provider == PROVIDER)
        .execution_options(populate_existing=True)
    )


def _audit(
    session: AsyncSession,
    request: Request | None,
    current: CurrentUser,
    action: str,
    changes: dict[str, Any],
) -> None:
    add_audit_log(
        session,
        organization_id=current.organization_id,
        actor_user_id=current.user.id,
        entity_type="integration",
        entity_id=PROVIDER,
        action=action,
        changes=changes,
        request=request,
    )


# --- Connect --------------------------------------------------------------------------------


def connect_url(current: CurrentUser) -> str:
    """The Facebook sign-in link (state is signed and expires in 15 minutes)."""
    if not get_settings().meta_configured:
        raise NOT_CONFIGURED
    state = sign_state({"u": str(current.user.id), "o": str(current.organization_id)})
    return get_meta().authorize_url(state, redirect_uri())


async def finish_connect(
    session: AsyncSession, request: Request, current: CurrentUser, code: str, state: str
) -> Integration:
    """Exchange the code, store tokens, list Pages and ad accounts, subscribe the Page."""
    if not get_settings().meta_configured:
        raise NOT_CONFIGURED
    try:
        data = read_state(state)
    except SecretBoxError as exc:
        raise invalid("invalid_state", str(exc)) from exc
    if data.get("u") != str(current.user.id):
        raise forbidden("Finish connecting with the same TEG CRM account that started it.")

    meta = get_meta()
    try:
        token, expires_in = await meta.exchange_code(code, redirect_uri())
        me = await meta.me(token)
        pages = await meta.pages(token)
        accounts = await meta.ad_accounts(token)
    except MetaError as exc:
        raise invalid("meta_error", f"Meta refused the connection: {exc}") from exc

    integration = await get_integration(session, current.organization_id)
    if integration is None:
        integration = Integration(
            organization_id=current.organization_id,
            provider=PROVIDER,
            settings={"assign_mode": "unassigned"},
            scopes=[],
            pages=[],  # loaded collections: no lazy load in async code
            ad_accounts=[],
        )
        session.add(integration)
    integration.status = "connected"
    integration.external_user_id = str(me.get("id"))
    integration.external_user_name = me.get("name")
    integration.access_token_encrypted = encrypt(token)
    integration.token_expires_at = now_utc() + timedelta(seconds=expires_in) if expires_in else None
    integration.connected_by_user_id = current.user.id
    integration.connected_at = now_utc()
    integration.last_error = None
    await session.flush()

    known_pages = {p.external_page_id: p for p in integration.pages}
    for page in pages:
        row = known_pages.get(str(page["id"]))
        if row is None:
            row = IntegrationPage(
                integration_id=integration.id, external_page_id=str(page["id"]), name=page["name"]
            )
            session.add(row)
            integration.pages.append(row)
        row.name = page["name"]
        row.page_token_encrypted = (
            encrypt(page["access_token"]) if page.get("access_token") else None
        )
    known_accounts = {a.external_account_id: a for a in integration.ad_accounts}
    for account in accounts:
        row = known_accounts.get(str(account["account_id"]))
        if row is None:
            row = AdAccount(
                integration_id=integration.id,
                external_account_id=str(account["account_id"]),
                name=account.get("name") or account["account_id"],
                currency=account.get("currency") or "USD",
            )
            session.add(row)
            integration.ad_accounts.append(row)
        row.name = account.get("name") or row.name
        row.currency = account.get("currency") or row.currency
        row.timezone = account.get("timezone_name")
    # TEG has one Page and one ad account (decision 25 Nov 2026): pick them automatically.
    if len(integration.pages) == 1:
        integration.pages[0].is_selected = True
    if len(integration.ad_accounts) == 1:
        integration.ad_accounts[0].is_selected = True
    await _subscribe_selected(integration)
    _audit(
        session,
        request,
        current,
        "connect",
        {"as": [None, integration.external_user_name], "pages": [None, len(pages)]},
    )
    await session.commit()
    return integration


async def _subscribe_selected(integration: Integration) -> None:
    errors = []
    for page in integration.pages:
        if not page.is_selected or page.subscribed_at or not page.page_token_encrypted:
            continue
        try:
            await get_meta().subscribe_page(
                page.external_page_id, decrypt(page.page_token_encrypted)
            )
            page.subscribed_at = now_utc()
        except MetaError as exc:
            errors.append(f"{page.name}: {exc}")
    integration.last_error = "; ".join(errors) or None


async def set_selection(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    page_ids: list[uuid.UUID],
    account_ids: list[uuid.UUID],
) -> Integration:
    """Choose which Pages send leads and which ad accounts have spend synced."""
    integration = await _connected(session, current)
    for page in integration.pages:
        page.is_selected = page.id in page_ids
        if not page.is_selected:
            page.subscribed_at = None
    for account in integration.ad_accounts:
        account.is_selected = account.id in account_ids
    await _subscribe_selected(integration)
    _audit(
        session,
        request,
        current,
        "selection",
        {"pages": [None, len(page_ids)], "ad_accounts": [None, len(account_ids)]},
    )
    await session.commit()
    return integration


async def set_assign_mode(
    session: AsyncSession, request: Request, current: CurrentUser, mode: str
) -> Integration:
    """Unassigned (until go-live) or campaign round-robin."""
    integration = await _connected(session, current)
    before = (integration.settings or {}).get("assign_mode", "unassigned")
    integration.settings = {**(integration.settings or {}), "assign_mode": mode}
    _audit(session, request, current, "assign_mode", {"assign_mode": [before, mode]})
    await session.commit()
    return integration


async def disconnect(session: AsyncSession, request: Request, current: CurrentUser) -> None:
    """Stop receiving leads: tokens are deleted (reconnecting restores everything)."""
    integration = await get_integration(session, current.organization_id)
    if integration is None:
        return
    integration.status = "disconnected"
    integration.access_token_encrypted = None
    for page in integration.pages:
        page.page_token_encrypted = None
        page.subscribed_at = None
    _audit(session, request, current, "disconnect", {"status": ["connected", "disconnected"]})
    await session.commit()


async def _connected(session: AsyncSession, current: CurrentUser) -> Integration:
    integration = await get_integration(session, current.organization_id)
    if integration is None or integration.status == "disconnected":
        raise invalid("not_connected", "Connect Meta first.")
    return integration


# --- Leads ----------------------------------------------------------------------------------


def _created(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


async def _ad_campaign(
    session: AsyncSession, org_id: uuid.UUID, external_id: str, name: str
) -> AdCampaign:
    row = await session.scalar(
        select(AdCampaign).where(
            AdCampaign.organization_id == org_id,
            AdCampaign.provider == PROVIDER,
            AdCampaign.external_id == external_id,
        )
    )
    if row is None:
        row = AdCampaign(
            organization_id=org_id, provider=PROVIDER, external_id=external_id, name=name
        )
        session.add(row)
        await session.flush()
    elif name and row.name != name:
        row.name = name
    return row


async def _ad(session: AsyncSession, campaign: AdCampaign, data: dict[str, Any]) -> Ad | None:
    """Upsert ad set and ad from a lead or insight row (needs adset_id and ad_id)."""
    if not data.get("adset_id") or not data.get("ad_id"):
        return None
    ad_set = await session.scalar(
        select(AdSet).where(
            AdSet.ad_campaign_id == campaign.id, AdSet.external_id == str(data["adset_id"])
        )
    )
    if ad_set is None:
        ad_set = AdSet(
            ad_campaign_id=campaign.id,
            external_id=str(data["adset_id"]),
            name=data.get("adset_name") or str(data["adset_id"]),
        )
        session.add(ad_set)
        await session.flush()
    elif data.get("adset_name"):
        ad_set.name = data["adset_name"]
    ad = await session.scalar(
        select(Ad).where(Ad.ad_set_id == ad_set.id, Ad.external_id == str(data["ad_id"]))
    )
    if ad is None:
        ad = Ad(
            ad_set_id=ad_set.id,
            external_id=str(data["ad_id"]),
            name=data.get("ad_name") or str(data["ad_id"]),
        )
        session.add(ad)
        await session.flush()
    elif data.get("ad_name"):
        ad.name = data["ad_name"]
    return ad


async def _crm_campaign(
    session: AsyncSession, org_id: uuid.UUID, ad_campaign: AdCampaign
) -> Campaign:
    """The CRM campaign for an ad campaign, created on its first lead."""
    if ad_campaign.crm_campaign_id:
        campaign = await session.get(Campaign, ad_campaign.crm_campaign_id)
        if campaign is not None and campaign.deleted_at is None:
            return campaign
    campaign = Campaign(
        organization_id=org_id,
        name=ad_campaign.name[:120],
        campaign_type="paid_social",
        source="facebook",
        status="active",
        assignment_strategy="round_robin",
    )
    session.add(campaign)
    await session.flush()
    ad_campaign.crm_campaign_id = campaign.id
    return campaign


async def _route(
    session: AsyncSession, org_id: uuid.UUID, ad_campaign: AdCampaign, ad: Ad | None
) -> Campaign:
    """The CRM campaign for a lead: the most specific link wins (ad, ad set, ad campaign).

    Links are set on Campaign › Meta ads (Jaldi's model). An ad nobody linked still
    gets a CRM campaign (created on its first lead), so no lead is ever lost.
    """
    candidates: list[uuid.UUID | None] = []
    if ad is not None:
        ad_set = await session.get(AdSet, ad.ad_set_id)
        candidates += [ad.crm_campaign_id, ad_set.crm_campaign_id if ad_set else None]
    for campaign_id in candidates:
        if campaign_id:
            campaign = await session.get(Campaign, campaign_id)
            if campaign is not None and campaign.deleted_at is None:
                return campaign
    return await _crm_campaign(session, org_id, ad_campaign)


async def ingest_lead(
    session: AsyncSession,
    integration: Integration,
    data: dict[str, Any],
    form_name: str | None = None,
) -> Outcome:
    """Create a CRM lead from a Meta lead (idempotent on the leadgen id)."""
    external_id = str(data["id"])
    exists = await session.scalar(
        select(Lead.id).where(
            Lead.organization_id == integration.organization_id,
            Lead.source == "facebook",
            Lead.external_lead_id == external_id,
        )
    )
    if exists:
        return Outcome("skipped", reason="Already received.")
    org = await session.get(Organization, integration.organization_id)
    assert org is not None
    if data.get("campaign_id"):
        ad_campaign = await _ad_campaign(
            session,
            org.id,
            str(data["campaign_id"]),
            data.get("campaign_name") or "Facebook campaign",
        )
    else:  # organic form leads (from the Page, not an ad) share one campaign
        ad_campaign = await _ad_campaign(
            session, org.id, "organic", "Facebook lead forms (organic)"
        )
    ad = await _ad(session, ad_campaign, data)
    campaign = await _route(session, org.id, ad_campaign, ad)
    answers = {
        str(field.get("name")): ", ".join(str(v) for v in field.get("values") or [])
        for field in data.get("field_data") or []
    }
    mode = (integration.settings or {}).get("assign_mode", "unassigned")
    ingestor = await Ingestor.create(
        session,
        org,
        campaign,
        channel="digital",
        actor_id=None,
        assignment=Assignment(mode="unassigned") if mode == "unassigned" else None,
    )
    incoming = Incoming(
        answers=answers,
        source="facebook",
        sub_source="instagram" if data.get("platform") == "ig" else "facebook",
        external_lead_id=external_id,
        source_created_at=_created(data.get("created_time")),
        source_form_name=form_name,
        source_campaign_name=data.get("campaign_name"),
        source_adset_name=data.get("adset_name"),
        source_ad_name=data.get("ad_name"),
        ad_id=ad.id if ad else None,
    )
    async with session.begin_nested():
        return await ingestor.ingest(incoming)


async def _page_for(
    session: AsyncSession, page_id: str
) -> tuple[IntegrationPage, Integration] | None:
    row = (
        await session.execute(
            select(IntegrationPage, Integration)
            .join(Integration, Integration.id == IntegrationPage.integration_id)
            .where(
                IntegrationPage.external_page_id == page_id,
                IntegrationPage.is_selected.is_(True),
                Integration.provider == PROVIDER,
                Integration.status == "connected",
            )
            .limit(1)
        )
    ).first()
    return (row[0], row[1]) if row else None


async def _process(session: AsyncSession, event: WebhookEvent, value: dict[str, Any]) -> None:
    found = await _page_for(session, str(value.get("page_id")))
    now = now_utc()
    event.attempts = (event.attempts or 0) + 1
    event.processed_at = now
    if found is None:
        event.status, event.error = "ignored", "Page not connected to TEG CRM."
        return
    page, integration = found
    event.organization_id = integration.organization_id
    integration.last_webhook_at = now
    try:
        data = await get_meta().lead(
            str(value["leadgen_id"]), decrypt(page.page_token_encrypted or "")
        )
        outcome = await ingest_lead(session, integration, data)
    except MetaError as exc:
        event.status, event.error = "failed", str(exc)[:500]
        if exc.expired:
            integration.status, integration.last_error = (
                "needs_reconnect",
                "Meta token expired. Reconnect.",
            )
        return
    except SecretBoxError as exc:
        event.status, event.error = "failed", str(exc)
        return
    if outcome.status == "created" and outcome.lead:
        event.status, event.lead_id, event.error = "processed", outcome.lead.id, None
    else:
        event.status, event.error = (
            ("ignored" if outcome.status == "skipped" else "failed"),
            outcome.reason,
        )


async def receive_webhook(session: AsyncSession, body: bytes, signature: str | None) -> int:
    """Handle a Meta webhook delivery: verify, store each lead event, then process it."""
    if not valid_signature(body, signature):
        raise forbidden("Invalid webhook signature.")
    payload = json.loads(body or b"{}")
    if payload.get("object") != "page":
        return 0
    handled = 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "leadgen":
                continue
            value = change.get("value") or {}
            leadgen_id = str(value.get("leadgen_id") or "")
            if not leadgen_id:
                continue
            event = await session.scalar(
                select(WebhookEvent).where(
                    WebhookEvent.provider == PROVIDER, WebhookEvent.external_event_id == leadgen_id
                )
            )
            if event is not None and event.status in ("processed", "ignored"):
                continue  # Meta retries deliveries; each lead is handled once
            if event is None:
                event = WebhookEvent(
                    provider=PROVIDER,
                    external_event_id=leadgen_id,
                    payload=value,
                    headers={},
                    status="received",
                    attempts=0,
                )
                session.add(event)
                await session.flush()
            await _process(session, event, value)
            await session.commit()
            handled += 1
    return handled


async def sync_leads(session: AsyncSession, integration: Integration) -> dict[str, int]:
    """Catch leads a webhook missed: retry failed events, then read each form's recent leads."""
    counts = {"created": 0, "skipped": 0, "failed": 0}
    failed = await session.scalars(
        select(WebhookEvent).where(
            WebhookEvent.provider == PROVIDER,
            WebhookEvent.organization_id == integration.organization_id,
            WebhookEvent.status == "failed",
            WebhookEvent.attempts < 5,
        )
    )
    for event in list(failed):
        await _process(session, event, event.payload)
        await session.commit()
    now = now_utc()
    since = (integration.last_lead_sync_at or now - timedelta(days=1)) - timedelta(hours=2)
    since = max(since, now - timedelta(days=1))  # never a backfill (decision 25 Nov 2026)
    for page in integration.pages:
        if not page.is_selected or not page.page_token_encrypted:
            continue
        token = decrypt(page.page_token_encrypted)
        try:
            forms = await get_meta().forms(page.external_page_id, token)
            for form in forms:
                for lead in await get_meta().form_leads(
                    str(form["id"]), token, int(since.timestamp())
                ):
                    outcome = await ingest_lead(session, integration, lead, form.get("name"))
                    counts[outcome.status] += 1
                await session.commit()
        except MetaError as exc:
            integration.last_error = f"{page.name}: {exc}"
            if exc.expired:
                integration.status = "needs_reconnect"
            counts["failed"] += 1
    integration.last_lead_sync_at = now
    await session.commit()
    return counts


def _platform_leads(row: dict[str, Any]) -> int:
    for action in row.get("actions") or []:
        if action.get("action_type") in LEAD_ACTIONS:
            return int(float(action.get("value") or 0))
    return 0


async def sync_spend(session: AsyncSession, integration: Integration, days: int = 3) -> int:
    """Pull spend per ad per day for the last few days (late attribution changes them)."""
    if not integration.access_token_encrypted:
        return 0
    token = decrypt(integration.access_token_encrypted)
    saved = 0
    for account in integration.ad_accounts:
        if not account.is_selected:
            continue
        today = now_utc().astimezone(ZoneInfo(account.timezone or "Asia/Dubai")).date()
        try:
            rows = await get_meta().insights(
                account.external_account_id, token, today - timedelta(days=days - 1), today
            )
        except MetaError as exc:
            integration.last_error = f"{account.name}: {exc}"
            if exc.expired:
                integration.status = "needs_reconnect"
            continue
        for row in rows:
            ad_campaign = await _ad_campaign(
                session,
                integration.organization_id,
                str(row["campaign_id"]),
                row.get("campaign_name") or "",
            )
            ad_campaign.ad_account_id = account.id
            ad = await _ad(session, ad_campaign, row)
            if ad is None:
                continue
            day = date.fromisoformat(row["date_start"])
            insight = await session.scalar(
                select(AdInsightDaily).where(
                    AdInsightDaily.ad_id == ad.id, AdInsightDaily.day == day
                )
            )
            if insight is None:
                insight = AdInsightDaily(
                    organization_id=integration.organization_id,
                    ad_id=ad.id,
                    day=day,
                    currency=account.currency,
                )
                session.add(insight)
            insight.spend = Decimal(str(row.get("spend") or 0))
            insight.currency = account.currency
            insight.impressions = int(row.get("impressions") or 0)
            insight.clicks = int(row.get("clicks") or 0)
            insight.platform_leads = _platform_leads(row)
            insight.updated_at = datetime.now(UTC)
            saved += 1
    integration.last_spend_sync_at = now_utc()
    await session.commit()
    return saved


async def connected_integrations(session: AsyncSession) -> list[Integration]:
    """Every connected Meta integration (jobs run across organisations)."""
    return list(
        (
            await session.scalars(
                select(Integration).where(
                    Integration.provider == PROVIDER, Integration.status == "connected"
                )
            )
        ).unique()
    )
