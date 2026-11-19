"""Campaigns and their agents, contacts, leads, labels on leads, activities, tasks, assignments.

Columns mirror migration 0001 §4-6. Only columns the API uses are mapped; the
rest keep their database defaults.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin
from app.models.configuration import Label, LeadStatus, Market, Program
from app.models.identity import User

LEAD_SOURCES = (
    "facebook", "tiktok", "csv", "excel", "webhook", "bayut", "dubizzle",
    "zapier", "manual", "event", "whatsapp",
)  # fmt: skip
ACTIVITY_TYPES = (
    "lead_added", "assigned", "call", "whatsapp", "text", "email", "meeting", "comment",
    "status_change", "label_change", "edit", "task_created", "task_completed",
    "duplicate_detected", "imported",
)  # fmt: skip
TASK_TYPES = ("call_back", "send_email", "send_whatsapp", "send_text", "meeting")

LeadSourceType = ENUM(*LEAD_SOURCES, name="lead_source", create_type=False)
ActivityTypeType = ENUM(*ACTIVITY_TYPES, name="activity_type", create_type=False)
TaskTypeType = ENUM(*TASK_TYPES, name="task_type", create_type=False)
CampaignTypeType = ENUM(
    "paid_social", "event", "website", "portal", "import", "manual", "referral",
    name="campaign_type", create_type=False,
)  # fmt: skip
CampaignStatusType = ENUM(
    "draft", "active", "paused", "archived", name="campaign_status", create_type=False
)


class Campaign(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Lead container per source/channel; leads are shared out by its round-robin."""

    __tablename__ = "campaigns"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    name: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(LeadSourceType)
    campaign_type: Mapped[str] = mapped_column(CampaignTypeType)
    program_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id")
    )
    market_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("markets.id")
    )
    status: Mapped[str] = mapped_column(CampaignStatusType, default="active")
    assignment_strategy: Mapped[str] = mapped_column(Text, default="round_robin")
    last_lead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    legacy_jaldi_name: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    program: Mapped[Program | None] = relationship(lazy="joined")
    market: Mapped[Market | None] = relationship(lazy="joined")


class CampaignAgent(Base):
    """An agent in a campaign's round-robin; ``is_active`` = receiving leads now."""

    __tablename__ = "campaign_agents"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    rr_position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(lazy="joined")


class Contact(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """One real person; their enquiries (leads) are linked to it."""

    __tablename__ = "contacts"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    full_name: Mapped[str] = mapped_column(Text)
    phone_e164: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(CITEXT)
    city: Mapped[str | None] = mapped_column(Text)


class LeadLabel(Base):
    """A label on a lead."""

    __tablename__ = "lead_labels"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    label: Mapped[Label] = relationship(lazy="joined")


class Lead(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """One enquiry: a person on a campaign, owned by an agent."""

    __tablename__ = "leads"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id")
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id")
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    status_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lead_statuses.id"))

    first_name: Mapped[str] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    # GENERATED ALWAYS column: computed by Postgres, never written by the app.
    full_name: Mapped[str] = mapped_column(
        Text, Computed("trim(first_name || ' ' || coalesce(last_name, ''))", persisted=True)
    )
    phone_raw: Mapped[str | None] = mapped_column(Text)
    phone_e164: Mapped[str | None] = mapped_column(Text)
    phone_country_code: Mapped[str | None] = mapped_column(Text)
    whatsapp_e164: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(CITEXT)

    city: Mapped[str | None] = mapped_column(Text)
    country_of_interest: Mapped[str | None] = mapped_column(Text)
    budget_text: Mapped[str | None] = mapped_column(Text)
    education_level: Mapped[str | None] = mapped_column(Text)
    english_test_status: Mapped[str | None] = mapped_column(Text)
    age_text: Mapped[str | None] = mapped_column(Text)
    has_family: Mapped[bool | None] = mapped_column(Boolean)
    form_answers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)

    source: Mapped[str] = mapped_column(LeadSourceType)
    sub_source: Mapped[str | None] = mapped_column(Text)
    external_lead_id: Mapped[str | None] = mapped_column(Text)
    source_campaign_name: Mapped[str | None] = mapped_column(Text)
    source_adset_name: Mapped[str | None] = mapped_column(Text)
    source_ad_name: Mapped[str | None] = mapped_column(Text)
    source_form_name: Mapped[str | None] = mapped_column(Text)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    deal_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    deal_currency: Mapped[str | None] = mapped_column(Text)

    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of_lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id")
    )

    calls_made: Mapped[int] = mapped_column(Integer, default=0)
    times_assigned: Mapped[int] = mapped_column(Integer, default=0)
    first_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_time_seconds: Mapped[int | None] = mapped_column(Integer)
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_note: Mapped[str | None] = mapped_column(Text)
    next_task_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    legacy_jaldi_id: Mapped[int | None] = mapped_column(BigInteger)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[LeadStatus] = relationship(lazy="joined")
    owner: Mapped[User | None] = relationship(lazy="joined")
    campaign: Mapped[Campaign | None] = relationship(lazy="joined")
    lead_labels: Mapped[list[LeadLabel]] = relationship(
        lazy="selectin", cascade="all, delete-orphan"
    )


class Activity(Base):
    """Timeline entry. Single source for timelines and productivity metrics."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(ActivityTypeType)
    outcome: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    from_status_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lead_statuses.id")
    )
    to_status_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lead_statuses.id")
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User | None] = relationship(lazy="joined")


class Task(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """A follow-up on a lead."""

    __tablename__ = "tasks"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE")
    )
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    type: Mapped[str] = mapped_column(TaskTypeType)
    notes: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )

    lead: Mapped[Lead] = relationship(lazy="joined")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assigned_to_user_id], lazy="joined")


class LeadAssignment(Base):
    """Owner change log (feeds Times Assigned / Last Assigned)."""

    __tablename__ = "lead_assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE")
    )
    from_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    to_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
