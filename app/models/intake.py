"""Lead intake: website/API keys, inbound lead log, CSV imports and their rows.

Columns mirror migrations 0001 §4, §7 and 0005.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin
from app.models.identity import User
from app.models.leads import Campaign

ImportStatusType = ENUM(
    "uploaded", "mapped", "running", "completed", "failed", name="import_status", create_type=False
)
WebhookStatusType = ENUM(
    "received", "processed", "failed", "ignored", name="webhook_event_status", create_type=False
)


class ApiKey(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Key a website form uses to send leads into one campaign. Only its hash is stored."""

    __tablename__ = "api_keys"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    name: Mapped[str] = mapped_column(Text)
    key_prefix: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=lambda: ["leads:create"])
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id")
    )
    allowed_origins: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    campaign: Mapped[Campaign | None] = relationship(lazy="joined")


class WebhookEvent(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Every inbound lead, stored before processing so nothing is lost."""

    __tablename__ = "webhook_events"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    provider: Mapped[str] = mapped_column(Text)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id")
    )
    external_event_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(WebhookStatusType, default="received")
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Import(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """One CSV import (rows are sent in batches by the browser)."""

    __tablename__ = "imports"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id")
    )
    file_name: Mapped[str] = mapped_column(Text)
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(ImportStatusType, default="running")
    total_rows: Mapped[int | None] = mapped_column(Integer)
    imported_rows: Mapped[int | None] = mapped_column(Integer, default=0)
    skipped_duplicate_rows: Mapped[int | None] = mapped_column(Integer, default=0)
    failed_rows: Mapped[int | None] = mapped_column(Integer, default=0)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    campaign: Mapped[Campaign | None] = relationship(lazy="joined")
    created_by: Mapped[User | None] = relationship(lazy="joined")


class ImportRow(Base):
    """Row-level import log: the raw row and its lead, or why it was skipped or failed."""

    __tablename__ = "import_rows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("imports.id", ondelete="CASCADE")
    )
    row_number: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
