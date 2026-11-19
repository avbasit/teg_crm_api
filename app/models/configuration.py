"""Admin-editable lookups: lead statuses, labels, markets, programmes."""

import uuid

from sqlalchemy import Boolean, ForeignKey, SmallInteger, Text
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class LeadStatus(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Pipeline status (Settings › Customizations)."""

    __tablename__ = "lead_statuses"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    reference: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    color: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(SmallInteger)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_contacted: Mapped[bool] = mapped_column(Boolean, default=True)
    is_mql: Mapped[bool] = mapped_column(Boolean, default=False)
    is_won: Mapped[bool] = mapped_column(Boolean, default=False)
    is_terminal: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Label(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Coloured lead tag."""

    __tablename__ = "labels"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    name: Mapped[str] = mapped_column(CITEXT)
    color: Mapped[str] = mapped_column(Text)


class Market(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Target market for campaigns (Lahore, Islamabad, Doha...)."""

    __tablename__ = "markets"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    name: Mapped[str] = mapped_column(CITEXT)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0)


class FormFieldAlias(Base):
    """Maps a raw form question key (normalised) to a lead column."""

    __tablename__ = "form_field_aliases"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True
    )
    raw_key: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical_field: Mapped[str] = mapped_column(Text)


class Program(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Immigration programme (E2, D2, SUV...)."""

    __tablename__ = "programs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
