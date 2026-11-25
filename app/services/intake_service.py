"""New leads from campaigns: duplicate rules, contact matching, round-robin and routing.

Decisions (19 Nov 2026), enforced here:
- A repeat enquiry goes to the **owner of the person's earlier lead**; if that user
  isn't active, it goes to the campaign's round-robin.
- Round-robin is per campaign, **equal turns**, skipping agents switched off or not active.
- Nobody available, or a manual campaign: the lead is left **unassigned**.
- Paused campaigns still accept and assign leads.
- Jaldi's blocking rules stay: an import skips a person already in the same campaign.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.phone import normalise_phone
from app.models.configuration import LeadStatus
from app.models.identity import User
from app.models.leads import Activity, Campaign, CampaignAgent, Contact, Lead, LeadAssignment
from app.models.organization import Organization
from app.schemas.settings import DuplicateRules
from app.services.form_answers import AliasMap

Channel = Literal["digital", "import", "manual"]
EMAIL_SHAPE = "@"


def now_utc() -> datetime:
    """Current time in UTC."""
    return datetime.now(UTC)


# --- Duplicate rules (organizations.settings.duplicates) -----------------------------------


def read_duplicate_rules(org: Organization) -> DuplicateRules:
    """Rules stored in Jaldi's shape (import/manual/digital), with defaults."""
    stored: dict[str, Any] = (org.settings or {}).get("duplicates") or {}
    imp, man, dig = (stored.get(k) or {} for k in ("import", "manual", "digital"))
    routing = dig.get("routing", "previous_owner")
    return DuplicateRules(
        import_block_same_campaign=imp.get("block_same_campaign", True),
        import_prevent_global=imp.get("prevent_global", False),
        manual_block_same_agent=man.get("block_same_agent", False),
        manual_prevent_global=man.get("prevent_global", False),
        digital_block_same_campaign=dig.get("block_same_campaign", False),
        digital_prevent_global=dig.get("prevent_global", False),
        routing="previous_owner" if routing == "previous_owner" else "round_robin",
    )


def write_duplicate_rules(org: Organization, rules: DuplicateRules) -> None:
    """Store rules back into ``organizations.settings`` (other keys untouched)."""
    settings = dict(org.settings or {})
    stored = dict(settings.get("duplicates") or {})
    stored["import"] = {
        **(stored.get("import") or {}),
        "block_same_campaign": rules.import_block_same_campaign,
        "prevent_global": rules.import_prevent_global,
    }
    stored["manual"] = {
        **(stored.get("manual") or {}),
        "block_same_agent": rules.manual_block_same_agent,
        "prevent_global": rules.manual_prevent_global,
    }
    stored["digital"] = {
        **(stored.get("digital") or {}),
        "block_same_campaign": rules.digital_block_same_campaign,
        "prevent_global": rules.digital_prevent_global,
        "routing": rules.routing,
    }
    settings["duplicates"] = stored
    org.settings = settings  # reassign so SQLAlchemy sees the JSON change


# --- Contacts -----------------------------------------------------------------------------


async def find_contact(
    session: AsyncSession, organization_id: uuid.UUID, phone_e164: str | None, email: str | None
) -> Contact | None:
    """The person with this phone (checked first) or email, if already known."""
    for column, value in ((Contact.phone_e164, phone_e164), (Contact.email, email)):
        if value:
            contact = await session.scalar(
                select(Contact)
                .where(Contact.organization_id == organization_id, column == value)
                .order_by(Contact.created_at)
                .limit(1)
            )
            if contact is not None:
                return contact
    return None


async def earlier_leads(session: AsyncSession, contact_id: uuid.UUID) -> list[Lead]:
    """The person's existing enquiries, oldest first."""
    return list(
        (
            await session.scalars(
                select(Lead)
                .where(Lead.contact_id == contact_id, Lead.deleted_at.is_(None))
                .order_by(Lead.created_at)
            )
        ).unique()
    )


async def link_contact(
    session: AsyncSession, lead: Lead, contact: Contact | None, earlier: list[Lead]
) -> Lead | None:
    """Attach the lead to its person and flag it as a duplicate; returns the first enquiry."""
    if contact is None:
        contact = Contact(
            organization_id=lead.organization_id,
            full_name=f"{lead.first_name} {lead.last_name or ''}".strip(),
            phone_e164=lead.phone_e164,
            email=lead.email,
            city=lead.city,
        )
        session.add(contact)
        await session.flush()
    lead.contact_id = contact.id
    if earlier:
        lead.is_duplicate = True
        lead.duplicate_of_lead_id = earlier[0].id
        return earlier[0]
    return None


def duplicate_activity(lead: Lead, original: Lead, when: datetime) -> Activity:
    """Timeline entry saying clearly that this is a duplicate lead, and of which enquiry."""
    return Activity(
        organization_id=lead.organization_id,
        lead_id=lead.id,
        user_id=None,
        type="duplicate_detected",
        occurred_at=when,
        notes="Duplicate lead: this person enquired before.",
        meta={
            "duplicate_of": str(original.id),
            "original_campaign": original.campaign.name if original.campaign else None,
            "original_owner": original.owner.full_name if original.owner else None,
            "original_created_at": original.created_at.isoformat(),
        },
    )


# --- Round-robin --------------------------------------------------------------------------


async def next_agent(session: AsyncSession, campaign_id: uuid.UUID) -> uuid.UUID | None:
    """Whose turn it is in the campaign, moving them to the back of the queue.

    The campaign row is locked first so two leads arriving together never land on
    the same turn. Agents switched off, or whose account isn't active, are skipped.
    """
    await session.execute(select(Campaign.id).where(Campaign.id == campaign_id).with_for_update())
    turn = await session.scalar(
        select(CampaignAgent)
        .join(User, User.id == CampaignAgent.user_id)
        .where(
            CampaignAgent.campaign_id == campaign_id,
            CampaignAgent.is_active.is_(True),
            User.status == "active",
            User.is_assignable.is_(True),
        )
        .order_by(CampaignAgent.rr_position, CampaignAgent.created_at, CampaignAgent.user_id)
        .limit(1)
    )
    if turn is None:
        return None
    last = await session.scalar(
        select(func.max(CampaignAgent.rr_position)).where(CampaignAgent.campaign_id == campaign_id)
    )
    await session.execute(
        update(CampaignAgent)
        .where(CampaignAgent.campaign_id == campaign_id, CampaignAgent.user_id == turn.user_id)
        .values(rr_position=(last or 0) + 1)
    )
    return turn.user_id


async def _active_user(session: AsyncSession, user_id: uuid.UUID | None) -> bool:
    if user_id is None:
        return False
    status = await session.scalar(select(User.status).where(User.id == user_id))
    return status == "active"


async def route(
    session: AsyncSession, campaign: Campaign, earlier: list[Lead], rules: DuplicateRules
) -> tuple[uuid.UUID | None, str]:
    """Owner for a new campaign lead and the reason, per the routing decisions."""
    if earlier and rules.routing == "previous_owner":
        previous = next((e.owner_user_id for e in reversed(earlier) if e.owner_user_id), None)
        if await _active_user(session, previous):
            return previous, "duplicate_routing"
    if campaign.assignment_strategy == "round_robin":
        agent = await next_agent(session, campaign.id)
        if agent:
            return agent, "round_robin"
    return None, "unassigned"


# --- Ingest one lead ----------------------------------------------------------------------


@dataclass
class Incoming:
    """A lead as it arrives from a website form, an import row or an integration."""

    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    email: str | None = None
    city: str | None = None
    country_of_interest: str | None = None
    budget_text: str | None = None
    education_level: str | None = None
    english_test_status: str | None = None
    age_text: str | None = None
    has_family: bool | None = None
    notes: str | None = None
    answers: dict[str, Any] = field(default_factory=dict)
    source: str = "webhook"
    sub_source: str | None = None
    external_lead_id: str | None = None
    source_created_at: datetime | None = None
    source_form_name: str | None = None
    source_campaign_name: str | None = None
    source_adset_name: str | None = None
    source_ad_name: str | None = None
    ad_id: uuid.UUID | None = None


@dataclass
class Assignment:
    """How an import assigns its leads: by campaign rules, to one person, or not at all."""

    mode: Literal["campaign", "user", "unassigned"] = "campaign"
    user_id: uuid.UUID | None = None


@dataclass
class Outcome:
    """What happened to one incoming lead."""

    status: Literal["created", "skipped", "failed"]
    lead: Lead | None = None
    reason: str | None = None
    duplicate: bool = False


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _split_name(full_name: str) -> tuple[str, str | None]:
    parts = full_name.split(None, 1)
    return parts[0], parts[1] if len(parts) > 1 else None


def _fill_from_answers(incoming: Incoming, aliases: AliasMap) -> dict[str, Any]:
    """Copy form answers into empty fields; return the answers to keep on the lead."""
    extracted = aliases.extract(incoming.answers)
    mapping = {
        "full_name": "full_name",
        "email": "email",
        "phone_raw": "phone",
        "whatsapp_e164": "whatsapp",
        "city": "city",
        "country_of_interest": "country_of_interest",
        "budget_text": "budget_text",
        "education_level": "education_level",
        "english_test_status": "english_test_status",
        "age_text": "age_text",
        "has_family": "has_family",
    }
    for column, value in extracted.columns.items():
        attr = mapping[column]
        if getattr(incoming, attr) in (None, ""):
            setattr(incoming, attr, value)
    return extracted.answers


class Ingestor:
    """Creates campaign leads one by one with shared, preloaded lookups."""

    def __init__(
        self,
        session: AsyncSession,
        org: Organization,
        campaign: Campaign,
        *,
        channel: Channel,
        actor_id: uuid.UUID | None,
        aliases: AliasMap,
        initial_status: LeadStatus,
        assignment: Assignment | None = None,
    ) -> None:
        """Prefer :meth:`create`, which loads the lookups."""
        self.session = session
        self.org = org
        self.campaign = campaign
        self.channel = channel
        self.actor_id = actor_id
        self.aliases = aliases
        self.initial_status = initial_status
        self.assignment = assignment or Assignment()
        self.rules = read_duplicate_rules(org)

    @classmethod
    async def create(
        cls,
        session: AsyncSession,
        org: Organization,
        campaign: Campaign,
        *,
        channel: Channel,
        actor_id: uuid.UUID | None,
        assignment: Assignment | None = None,
    ) -> "Ingestor":
        """Load the alias map and initial status once for a batch."""
        status = await session.scalar(
            select(LeadStatus).where(
                LeadStatus.organization_id == org.id, LeadStatus.reference == "not_contacted"
            )
        )
        if status is None:
            raise RuntimeError("The 'Not Contacted' status is missing (migration 0002).")
        return cls(
            session,
            org,
            campaign,
            channel=channel,
            actor_id=actor_id,
            aliases=await AliasMap.load(session, org.id),
            initial_status=status,
            assignment=assignment,
        )

    def _blocked(self, earlier: list[Lead]) -> str | None:
        if not earlier:
            return None
        prevent = (
            self.rules.import_prevent_global
            if self.channel == "import"
            else self.rules.digital_prevent_global
        )
        if prevent:
            return "Duplicate: this person is already in the CRM."
        same_campaign = (
            self.rules.import_block_same_campaign
            if self.channel == "import"
            else self.rules.digital_block_same_campaign
        )
        if same_campaign and any(e.campaign_id == self.campaign.id for e in earlier):
            return "Duplicate: this person is already in this campaign."
        return None

    async def ingest(self, incoming: Incoming) -> Outcome:
        """Validate, de-duplicate, create and assign one lead (no commit)."""
        answers = _fill_from_answers(incoming, self.aliases)
        first = _clean(incoming.first_name)
        last = _clean(incoming.last_name)
        if not first and _clean(incoming.full_name):
            first, split_last = _split_name(_clean(incoming.full_name) or "")
            last = last or split_last
        email = _clean(incoming.email)
        email = email.lower() if email and EMAIL_SHAPE in email else None
        phone = normalise_phone(_clean(incoming.phone))
        if phone is None and email is None:
            return Outcome("failed", reason="No phone number or email.")

        contact = await find_contact(
            self.session, self.org.id, phone.e164 if phone else None, email
        )
        earlier = await earlier_leads(self.session, contact.id) if contact else []
        blocked = self._blocked(earlier)
        if blocked:
            return Outcome("skipped", reason=blocked, duplicate=True)

        now = now_utc()
        whatsapp = normalise_phone(_clean(incoming.whatsapp))
        lead = Lead(
            organization_id=self.org.id,
            campaign_id=self.campaign.id,
            status_id=self.initial_status.id,
            first_name=(first or "Unknown")[:100],
            last_name=last[:100] if last else None,
            phone_raw=phone.raw if phone else None,
            phone_e164=phone.e164 if phone else None,
            phone_country_code=phone.country_code if phone else None,
            whatsapp_e164=(whatsapp.e164 or whatsapp.raw) if whatsapp else None,
            email=email,
            city=_clean(incoming.city),
            country_of_interest=_clean(incoming.country_of_interest),
            budget_text=_clean(incoming.budget_text),
            education_level=_clean(incoming.education_level),
            english_test_status=_clean(incoming.english_test_status),
            age_text=_clean(incoming.age_text),
            has_family=incoming.has_family,
            notes=_clean(incoming.notes),
            form_answers=answers,
            source=incoming.source,
            sub_source=_clean(incoming.sub_source),
            external_lead_id=_clean(incoming.external_lead_id),
            source_created_at=incoming.source_created_at,
            source_form_name=_clean(incoming.source_form_name),
            source_campaign_name=_clean(incoming.source_campaign_name),
            source_adset_name=_clean(incoming.source_adset_name),
            source_ad_name=_clean(incoming.source_ad_name),
            ad_id=incoming.ad_id,
            times_assigned=0,
        )
        original = await link_contact(self.session, lead, contact, earlier)

        if self.assignment.mode == "user":
            owner_id, reason = self.assignment.user_id, "import"
        elif self.assignment.mode == "unassigned":
            owner_id, reason = None, "unassigned"
        else:
            owner_id, reason = await route(self.session, self.campaign, earlier, self.rules)
        lead.owner_user_id = owner_id
        if owner_id:
            lead.times_assigned = 1
            lead.last_assigned_at = now
        self.session.add(lead)
        await self.session.flush()

        self.session.add(
            Activity(
                organization_id=self.org.id,
                lead_id=lead.id,
                user_id=self.actor_id,
                type="imported" if self.channel == "import" else "lead_added",
                occurred_at=now,
                meta={
                    "source": incoming.source,
                    "campaign": self.campaign.name,
                    "channel": self.channel,
                },
            )
        )
        if original is not None:
            self.session.add(duplicate_activity(lead, original, now))
        if owner_id:
            self.session.add(
                Activity(
                    organization_id=self.org.id,
                    lead_id=lead.id,
                    user_id=None,
                    type="assigned",
                    occurred_at=now,
                    meta={"from": None, "to": str(owner_id), "reason": reason},
                )
            )
            self.session.add(
                LeadAssignment(
                    lead_id=lead.id,
                    to_user_id=owner_id,
                    assigned_by_user_id=self.actor_id,
                    reason=reason,
                )
            )
        self.campaign.last_lead_at = now
        return Outcome("created", lead=lead, duplicate=original is not None)
