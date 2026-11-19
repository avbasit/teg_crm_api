"""Leads posted by website forms with a campaign's website key.

Every submission is stored in ``webhook_events`` before it is processed, so nothing
is lost, then goes through the same pipeline as other campaign leads.
"""

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, forbidden, invalid
from app.models.intake import ApiKey, WebhookEvent
from app.models.leads import Campaign
from app.models.organization import Organization
from app.schemas.campaigns import IntakeAccepted
from app.services.campaign_service import hash_key
from app.services.intake_service import Incoming, Ingestor, now_utc

INVALID_KEY = AppError(401, "invalid_key", "Missing, unknown or revoked website key.")
HONEYPOTS = ("_gotcha", "_hp")
MAX_FIELDS = 100
MAX_VALUE = 4000

# Common field names website forms use, mapped to lead fields.
_FIELD_NAMES: dict[str, str] = {
    "first_name": "first_name",
    "firstname": "first_name",
    "last_name": "last_name",
    "lastname": "last_name",
    "surname": "last_name",
    "name": "full_name",
    "full_name": "full_name",
    "fullname": "full_name",
    "your_name": "full_name",
    "phone": "phone",
    "phone_number": "phone",
    "mobile": "phone",
    "tel": "phone",
    "email": "email",
    "email_address": "email",
    "your_email": "email",
    "whatsapp": "whatsapp",
    "whatsapp_number": "whatsapp",
    "city": "city",
    "message": "notes",
    "notes": "notes",
    "comments": "notes",
    "your_message": "notes",
    "form_name": "source_form_name",
    "utm_source": "sub_source",
}


def _key_of(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def build_incoming(payload: dict[str, Any]) -> Incoming:
    """Read a website submission: known fields become lead fields, the rest form answers."""
    incoming = Incoming(source="webhook")
    answers: dict[str, Any] = {}
    extra = payload.get("fields")
    items = list(payload.items()) + (list(extra.items()) if isinstance(extra, dict) else [])
    for name, value in items:
        if name in ("fields", "external_id", *HONEYPOTS) or value is None:
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        if isinstance(value, dict):
            continue
        text = str(value).strip()[:MAX_VALUE]
        if not text:
            continue
        field = _FIELD_NAMES.get(_key_of(str(name)))
        if field and getattr(incoming, field) in (None, ""):
            setattr(incoming, field, text)
            if field == "sub_source":
                answers[str(name)] = text
        else:
            answers[str(name)] = text
    incoming.answers = answers
    external = payload.get("external_id")
    incoming.external_lead_id = str(external)[:200] if external else None
    return incoming


async def _key(session: AsyncSession, secret: str | None) -> ApiKey:
    if not secret or len(secret) > 200:
        raise INVALID_KEY
    key = await session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(secret.strip())))
    now = now_utc()
    if (
        key is None
        or key.revoked_at is not None
        or (key.expires_at is not None and key.expires_at < now)
        or "leads:create" not in (key.scopes or [])
        or key.campaign_id is None
    ):
        raise INVALID_KEY
    return key


async def receive(
    session: AsyncSession, request: Request, secret: str | None, payload: dict[str, Any]
) -> IntakeAccepted:
    """Store, check and process one website submission."""
    if len(payload) > MAX_FIELDS:
        raise invalid("too_many_fields", f"Send at most {MAX_FIELDS} fields.")
    key = await _key(session, secret)
    origin = (request.headers.get("origin") or "").lower().rstrip("/")
    if key.allowed_origins and origin and origin not in key.allowed_origins:
        raise forbidden("This website key can't be used from this site.")
    campaign = await session.get(Campaign, key.campaign_id)
    org = await session.get(Organization, key.organization_id)
    if campaign is None or campaign.deleted_at is not None or org is None:
        raise INVALID_KEY
    if campaign.status == "archived":
        raise AppError(410, "campaign_archived", "This campaign no longer takes leads.")

    external = payload.get("external_id")
    event_ref = f"{key.id}:{external}" if external else None
    if event_ref:
        existing = await session.scalar(
            select(WebhookEvent.id).where(
                WebhookEvent.provider == "website", WebhookEvent.external_event_id == event_ref
            )
        )
        if existing:
            return IntakeAccepted(status="duplicate_event", reference=existing)

    now = now_utc()
    key.last_used_at = now
    event = WebhookEvent(
        organization_id=org.id,
        provider="website",
        api_key_id=key.id,
        external_event_id=event_ref,
        payload=payload,
        headers={
            "origin": request.headers.get("origin"),
            "referer": request.headers.get("referer"),
            "user_agent": (request.headers.get("user-agent") or "")[:300],
        },
        status="received",
        attempts=1,
    )
    session.add(event)
    await session.flush()
    reference: uuid.UUID = event.id

    if any(str(payload.get(h) or "").strip() for h in HONEYPOTS):
        event.status, event.error, event.processed_at = "ignored", "Spam trap filled in.", now
        await session.commit()
        return IntakeAccepted(status="received", reference=reference)  # don't tell the bot

    ingestor = await Ingestor.create(session, org, campaign, channel="digital", actor_id=None)
    async with session.begin_nested():
        outcome = await ingestor.ingest(build_incoming(payload))
    event.processed_at = now
    if outcome.status == "created" and outcome.lead is not None:
        event.status, event.lead_id = "processed", outcome.lead.id
    elif outcome.status == "skipped":
        event.status, event.error = "ignored", outcome.reason
    else:
        event.status, event.error = "failed", outcome.reason
    await session.commit()
    if outcome.status == "failed":
        raise invalid("contact_required", outcome.reason or "Send a phone number or an email.")
    return IntakeAccepted(status="received", reference=reference)
