"""Leads posted by website forms with a campaign's website key.

Every submission is stored in ``webhook_events`` before it is processed, so nothing
is lost, then goes through the same pipeline as other campaign leads.
"""

import re
import uuid
from typing import Any
from urllib.parse import parse_qsl

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
MAX_RAW_KEYS = 400  # Elementor's "advanced" webhook sends ~6 keys per field
MAX_VALUE = 4000

# Field names / labels website forms use (normalised by _key_of), mapped to lead fields.
# TEG's Elementor forms are matched by LABEL: their internal ids are misleading (in the
# RBI/CBI forms the field with id "name" is Current Citizenship, "message" is the mobile).
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
    "mobile_number": "phone",
    "mobile_no": "phone",
    "phone_no": "phone",
    "contact_number": "phone",
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
    "question": "notes",
    "your_question": "notes",
    "program_of_interest": "country_of_interest",
    "programme_of_interest": "country_of_interest",
    "i_am_interested_in": "country_of_interest",
    "interested_in": "country_of_interest",
    "programme": "country_of_interest",
    "program": "country_of_interest",
    "country_of_interest": "country_of_interest",
    "can_you_invest_minimum_of": "budget_text",
    "budget": "budget_text",
    "investment_budget": "budget_text",
    "how_did_you_hear_about_us": "sub_source",
    "utm_source": "sub_source",
    "form_name": "source_form_name",
}
COUNTRY_CODE_KEYS = {"country_code", "dial_code", "phone_code", "code"}
PLACEHOLDERS = {
    "choose one",
    "select",
    "select one",
    "please select",
    "-- select --",
    "none selected",
}
# Elementor "simple" webhook metadata that isn't an answer.
ELEMENTOR_META = {"date", "time", "user_agent", "remote_ip", "powered_by", "form_id", "form_name"}


def _key_of(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _is_placeholder(label: str, value: str) -> bool:
    text = value.strip().lower()
    return text in PLACEHOLDERS or _key_of(value) == _key_of(label)


def _with_country_code(code: str | None, phone: str | None) -> str | None:
    """'Pakistan  +92' + '0300 1234567' → '+923001234567' (a phone with its own + wins)."""
    if not phone or not code or phone.strip().startswith("+"):
        return phone
    dial = re.search(r"\+\s*([\d\s-]+)", code)
    if not dial:
        return phone
    digits = re.sub(r"\D", "", dial.group(1))
    local = re.sub(r"\D", "", phone).lstrip("0")
    return f"+{digits}{local}" if digits and local else phone


def flatten_form(body: bytes) -> dict[str, Any]:
    """Read an Elementor (or any HTML form) webhook body into ``{label: value}``.

    Elementor sends either *simple* data (``Full Name=…&Page URL=…``) or, with
    "Advanced data" on, ``fields[<id>][title]=…&fields[<id>][value]=…&form[name]=…``.
    """
    pairs = parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
    if len(pairs) > MAX_RAW_KEYS:
        raise invalid("too_many_fields", "This form sends too many fields.")
    raw: dict[str, str] = {}
    for key, value in pairs:
        raw[key] = f"{raw[key]}, {value}" if key in raw and value else value or raw.get(key, "")
    advanced = [k for k in raw if re.fullmatch(r"fields\[[^\]]+\]\[value\]", k)]
    if not advanced:
        flat = {k: v for k, v in raw.items() if _key_of(k) not in ELEMENTOR_META}
        if raw.get("form_name"):
            flat["form_name"] = raw["form_name"]
        return flat
    flat: dict[str, Any] = {}
    for key in advanced:
        field_id = key[len("fields[") : -len("][value]")]
        label = raw.get(f"fields[{field_id}][title]") or field_id
        if raw.get(f"fields[{field_id}][type]") in ("recaptcha", "recaptcha_v3", "step", "html"):
            continue
        flat[label] = raw[key]
    if raw.get("form[name]"):
        flat["form_name"] = raw["form[name]"]
    for meta in ("page_url", "referer"):
        if raw.get(f"meta[{meta}][value]"):
            flat[meta] = raw[f"meta[{meta}][value]"]
    return flat


def build_incoming(payload: dict[str, Any]) -> Incoming:
    """Read a website submission: known fields become lead fields, the rest form answers."""
    incoming = Incoming(source="webhook")
    answers: dict[str, Any] = {}
    country_code: str | None = None
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
        if not text or _is_placeholder(str(name), text):
            continue
        key = _key_of(str(name))
        if key in COUNTRY_CODE_KEYS:
            country_code = text
            continue
        field = _FIELD_NAMES.get(key)
        if field and getattr(incoming, field) in (None, ""):
            setattr(incoming, field, text)
            if field in ("sub_source", "country_of_interest", "budget_text"):
                answers[str(name)] = text  # also kept as the original answer
        else:
            answers[str(name)] = text
    incoming.phone = _with_country_code(country_code, incoming.phone)
    if country_code:
        answers["Country Code"] = country_code
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
