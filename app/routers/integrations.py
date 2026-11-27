"""`/integrations`: Settings › Integrations and the public Meta webhook.

Connect Meta, pick the Page and ad account, choose how leads are assigned.
Rights (menu `settings.integrations`): view to see status; create to connect; edit to
change selection, assignment and run a sync; delete to disconnect. The webhook is
public but every delivery must carry Meta's signature.
"""

import uuid
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import DbSession
from app.core.errors import AppError, forbidden
from app.dependencies import CurrentUserDep, RequirePermission
from app.models.ads import Integration
from app.models.identity import User
from app.models.leads import Lead
from app.schemas.common import ErrorResponse
from app.schemas.integrations import (
    AdAccountOut,
    AdNode,
    AssignModeIn,
    ConnectUrl,
    IntegrationOut,
    PageOut,
    SelectionIn,
    SyncIn,
    SyncResult,
)
from app.schemas.users import UserRef
from app.services import meta_links, meta_service
from app.services.intake_service import now_utc

router = APIRouter(
    prefix="/integrations", tags=["integrations"], responses={403: {"model": ErrorResponse}}
)
MENU = "settings.integrations"
ERRORS = {422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}
LABELS = {
    "meta": "Meta (Facebook & Instagram)",
    "tiktok": "TikTok",
    "linkedin": "LinkedIn",
    "snapchat": "Snapchat",
}
SOURCES = {"meta": "facebook", "tiktok": "tiktok", "linkedin": "linkedin", "snapchat": "snapchat"}


async def _card(
    session: DbSession, provider: str, integration: Integration | None, org_id
) -> IntegrationOut:  # noqa: ANN001
    since = now_utc() - timedelta(days=30)
    leads = await session.scalar(
        select(func.count()).where(
            Lead.organization_id == org_id,
            Lead.source == SOURCES[provider],
            Lead.created_at >= since,
            Lead.deleted_at.is_(None),
        )
    )
    meta = provider == "meta"
    card = IntegrationOut(
        provider=provider,  # type: ignore[arg-type]
        label=LABELS[provider],
        available=meta,
        configured=get_settings().meta_configured if meta else False,
        status="not_connected",
        leads_last_30_days=leads or 0,
        webhook_url=meta_service.webhook_url() if meta else None,
        redirect_uri=meta_service.redirect_uri() if meta else None,
    )
    if integration is None:
        return card
    by = (
        await session.get(User, integration.connected_by_user_id)
        if integration.connected_by_user_id
        else None
    )
    return card.model_copy(
        update={
            "status": integration.status,
            "connected_as": integration.external_user_name,
            "connected_by": UserRef.model_validate(by) if by else None,
            "connected_at": integration.connected_at,
            "token_expires_at": integration.token_expires_at,
            "assign_mode": (integration.settings or {}).get("assign_mode", "unassigned"),
            "pages": [
                PageOut(
                    id=p.id,
                    external_id=p.external_page_id,
                    name=p.name,
                    is_selected=p.is_selected,
                    subscribed=p.subscribed_at is not None,
                )
                for p in integration.pages
            ],
            "ad_accounts": [
                AdAccountOut(
                    id=a.id,
                    external_id=a.external_account_id,
                    name=a.name,
                    currency=a.currency,
                    timezone=a.timezone,
                    is_selected=a.is_selected,
                )
                for a in integration.ad_accounts
            ],
            "last_webhook_at": integration.last_webhook_at,
            "last_lead_sync_at": integration.last_lead_sync_at,
            "last_spend_sync_at": integration.last_spend_sync_at,
            "last_error": integration.last_error,
        }
    )


@router.get("", response_model=list[IntegrationOut], dependencies=[RequirePermission(MENU, "view")])
async def list_integrations(current: CurrentUserDep, session: DbSession) -> list[IntegrationOut]:
    """Status of every ad platform."""
    rows = {
        i.provider: i
        for i in await session.scalars(
            select(Integration).where(Integration.organization_id == current.organization_id)
        )
    }
    return [await _card(session, p, rows.get(p), current.organization_id) for p in LABELS]


async def _meta_card(session: DbSession, current: CurrentUserDep) -> IntegrationOut:
    integration = await meta_service.get_integration(session, current.organization_id)
    return await _card(session, "meta", integration, current.organization_id)


@router.get(
    "/meta/connect",
    response_model=ConnectUrl,
    responses=ERRORS,
    dependencies=[RequirePermission(MENU, "create")],
)
async def meta_connect(current: CurrentUserDep) -> ConnectUrl:
    """The Facebook sign-in link to connect TEG's Page and ad account."""
    return ConnectUrl(url=meta_service.connect_url(current))


@router.get("/meta/callback", include_in_schema=False)
async def meta_callback(
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
    code: str | None = None,
    state: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    """Facebook sends the browser back here; we finish and return to Settings."""
    target = f"{get_settings().public_web_url.rstrip('/')}/settings/integrations"
    if not current.can(MENU, "create"):
        return RedirectResponse(f"{target}?error=forbidden", status_code=303)
    if not code or not state:
        reason = "cancelled" if error_description else "missing_code"
        return RedirectResponse(f"{target}?error={reason}", status_code=303)
    try:
        await meta_service.finish_connect(session, request, current, code, state)
    except AppError as exc:
        return RedirectResponse(f"{target}?error={exc.code}", status_code=303)
    return RedirectResponse(f"{target}?connected=meta", status_code=303)


@router.put(
    "/meta/selection",
    response_model=IntegrationOut,
    responses=ERRORS,
    dependencies=[RequirePermission(MENU, "edit")],
)
async def meta_selection(
    body: SelectionIn, request: Request, current: CurrentUserDep, session: DbSession
) -> IntegrationOut:
    """Choose the Pages that send leads and the ad accounts whose spend is synced."""
    await meta_service.set_selection(session, request, current, body.page_ids, body.ad_account_ids)
    return await _meta_card(session, current)


@router.patch(
    "/meta/settings",
    response_model=IntegrationOut,
    responses=ERRORS,
    dependencies=[RequirePermission(MENU, "edit")],
)
async def meta_settings(
    body: AssignModeIn, request: Request, current: CurrentUserDep, session: DbSession
) -> IntegrationOut:
    """Unassigned until go-live, or the campaign's round-robin."""
    await meta_service.set_assign_mode(session, request, current, body.assign_mode)
    return await _meta_card(session, current)


@router.post(
    "/meta/sync",
    response_model=SyncResult,
    responses=ERRORS,
    dependencies=[RequirePermission(MENU, "edit")],
)
async def meta_sync(body: SyncIn, current: CurrentUserDep, session: DbSession) -> SyncResult:
    """Run the missed-lead check or the spend sync now."""
    integration = await meta_service.get_integration(session, current.organization_id)
    if integration is None or integration.status != "connected":
        raise AppError(422, "not_connected", "Connect Meta first.")
    if body.what == "leads":
        return SyncResult(**await meta_service.sync_leads(session, integration))
    return SyncResult(spend_rows=await meta_service.sync_spend(session, integration))


@router.delete(
    "/meta",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[RequirePermission(MENU, "delete")],
)
async def meta_disconnect(
    request: Request, current: CurrentUserDep, session: DbSession
) -> Response:
    """Disconnect Meta (stored tokens are deleted)."""
    await meta_service.disconnect(session, request, current)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/meta/webhook", response_class=PlainTextResponse, include_in_schema=False)
async def meta_webhook_verify(
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    """Meta's one-off check when the webhook is registered."""
    expected = get_settings().meta_webhook_verify_token
    if mode != "subscribe" or not expected or token != expected:
        raise forbidden("Webhook verification failed.")
    return PlainTextResponse(challenge or "")


@router.post("/meta/webhook", include_in_schema=False)
async def meta_webhook(request: Request, session: DbSession) -> dict[str, int]:
    """New leads from Meta (signed with the app secret). Replies quickly; Meta retries on errors."""
    body = await request.body()
    handled = await meta_service.receive_webhook(
        session, body, request.headers.get("x-hub-signature-256")
    )
    return {"handled": handled}


@router.get(
    "/meta/browse",
    response_model=list[AdNode],
    responses=ERRORS,
    dependencies=[RequirePermission("campaigns", "edit")],
)
async def meta_browse(
    current: CurrentUserDep,
    session: DbSession,
    ad_account_id: uuid.UUID,
    level: Literal["campaigns", "adsets", "ads"] = "campaigns",
    parent: str | None = None,
) -> list[AdNode]:
    """Live list of ad campaigns, ad sets or ads, with what each is linked to."""
    return await meta_links.browse(session, current, ad_account_id, level, parent)
