"""Ad platform integrations and the ad hierarchy with daily spend (migration 0007)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin

PROVIDERS = ("meta", "tiktok", "linkedin", "snapchat")
ProviderType = ENUM(*PROVIDERS, name="integration_provider", create_type=False)
IntegrationStatusType = ENUM(
    "connected", "needs_reconnect", "disconnected", name="integration_status", create_type=False
)


class Integration(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """A connected ad platform (one per provider per organisation)."""

    __tablename__ = "integrations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    provider: Mapped[str] = mapped_column(ProviderType)
    status: Mapped[str] = mapped_column(IntegrationStatusType, default="connected")
    external_user_id: Mapped[str | None] = mapped_column(Text)
    external_user_name: Mapped[str | None] = mapped_column(Text)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    connected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_webhook_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_lead_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_spend_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    pages: Mapped[list["IntegrationPage"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="IntegrationPage.name"
    )
    ad_accounts: Mapped[list["AdAccount"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="AdAccount.name"
    )


class IntegrationPage(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """A Facebook Page (or equivalent); selected pages send leads."""

    __tablename__ = "integration_pages"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE")
    )
    external_page_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    page_token_encrypted: Mapped[str | None] = mapped_column(Text)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    subscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdAccount(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """An ad account; selected accounts have their spend synced."""

    __tablename__ = "ad_accounts"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE")
    )
    external_account_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str | None] = mapped_column(Text)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)


class AdCampaign(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """A platform ad campaign, linked to the CRM campaign its leads go into."""

    __tablename__ = "ad_campaigns"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    provider: Mapped[str] = mapped_column(ProviderType)
    external_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    ad_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ad_accounts.id", ondelete="SET NULL")
    )
    crm_campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL")
    )


class AdSet(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """A platform ad set."""

    __tablename__ = "ad_sets"

    ad_campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ad_campaigns.id", ondelete="CASCADE")
    )
    external_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    crm_campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL")
    )


class Ad(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """A platform ad."""

    __tablename__ = "ads"

    ad_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ad_sets.id", ondelete="CASCADE")
    )
    external_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    crm_campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL")
    )


class AdInsightDaily(Base):
    """Spend and delivery for one ad on one day."""

    __tablename__ = "ad_insights_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    ad_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ads.id", ondelete="CASCADE")
    )
    day: Mapped[date] = mapped_column(Date)
    spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    currency: Mapped[str] = mapped_column(Text)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    platform_leads: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
