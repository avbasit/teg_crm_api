"""Organisation profile, customizations (statuses, labels, programmes) and audit log."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel
from app.schemas.users import UserRef

HEX_COLOR = r"^#[0-9A-Fa-f]{6}$"
REFERENCE = r"^[a-z][a-z0-9_]{1,49}$"


class OrganizationOut(ApiModel):
    """Organisation profile and rules."""

    id: uuid.UUID
    name: str
    slug: str
    logo_url: str | None
    phone: str | None
    timezone: str
    default_currency: str
    settings: dict[str, Any]
    updated_at: datetime


class DealValueSetting(BaseModel):
    """The optional Deal value field on leads (Customizations › Fields)."""

    is_active: bool = True
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class OrganizationRules(BaseModel):
    """`GET /settings/rules`: organisation-wide switches used across screens."""

    hide_history_on_reassign: bool = Field(
        default=False,
        description="Hide past owners' activity when a not-yet-contacted lead is reassigned.",
    )
    deal_value: DealValueSetting = DealValueSetting()
    mql_status_references: list[str] = Field(
        default_factory=list, description="Statuses that count as a qualified (MQL) lead."
    )


class OrganizationRulesUpdate(BaseModel):
    """`PATCH /settings/rules`: send only what changes.

    `hide_history_on_reassign` needs edit rights on Roles & Permissions; the deal
    value field and MQL statuses need edit rights on Customizations.
    """

    hide_history_on_reassign: bool | None = None
    deal_value: DealValueSetting | None = None
    mql_status_references: list[str] | None = None


class OrganizationUpdate(BaseModel):
    """`PATCH /settings/organization`."""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    logo_url: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=40)
    timezone: str | None = Field(
        default=None, max_length=64, description="IANA name, e.g. Asia/Dubai"
    )
    default_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class LeadStatusOut(ApiModel):
    """A pipeline status."""

    id: uuid.UUID
    reference: str
    name: str
    color: str
    sort_order: int
    is_system: bool
    is_contacted: bool
    is_mql: bool
    is_won: bool
    is_terminal: bool
    is_active: bool


class LeadStatusCreate(BaseModel):
    """`POST /settings/lead-statuses` (custom status, added at the end)."""

    name: str = Field(min_length=1, max_length=60)
    reference: str = Field(pattern=REFERENCE, description="Stable code, e.g. visa_filed")
    color: str = Field(pattern=HEX_COLOR)
    is_contacted: bool = True
    is_mql: bool = False
    is_won: bool = False
    is_terminal: bool = False


class LeadStatusUpdate(BaseModel):
    """`PATCH /settings/lead-statuses/{id}`. The reference never changes."""

    name: str | None = Field(default=None, min_length=1, max_length=60)
    color: str | None = Field(default=None, pattern=HEX_COLOR)
    is_contacted: bool | None = None
    is_mql: bool | None = None
    is_won: bool | None = None
    is_terminal: bool | None = None
    is_active: bool | None = None


class ReorderRequest(BaseModel):
    """New order: every id, first to last."""

    ids: list[uuid.UUID]


class LabelOut(ApiModel):
    """A coloured lead tag."""

    id: uuid.UUID
    name: str
    color: str


class LabelIn(BaseModel):
    """`POST`/`PATCH /settings/labels`."""

    name: str = Field(min_length=1, max_length=40)
    color: str = Field(pattern=HEX_COLOR)


class ProgramOut(ApiModel):
    """An immigration programme."""

    id: uuid.UUID
    code: str
    name: str
    country: str | None
    is_active: bool


class ProgramCreate(BaseModel):
    """`POST /settings/programs`."""

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,29}$")
    name: str = Field(min_length=1, max_length=100)
    country: str | None = Field(default=None, max_length=60)


class ProgramUpdate(BaseModel):
    """`PATCH /settings/programs/{id}`."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    country: str | None = Field(default=None, max_length=60)
    is_active: bool | None = None


class MarketOut(ApiModel):
    """A target market for campaigns."""

    id: uuid.UUID
    name: str
    is_active: bool
    sort_order: int


class MarketCreate(BaseModel):
    """`POST /settings/markets`."""

    name: str = Field(min_length=1, max_length=60)


class MarketUpdate(BaseModel):
    """`PATCH /settings/markets/{id}`."""

    name: str | None = Field(default=None, min_length=1, max_length=60)
    is_active: bool | None = None


DuplicateRouting = Literal["previous_owner", "round_robin"]


class DuplicateRules(BaseModel):
    """Settings › Duplicate Rules.

    A person already in the CRM is matched on phone (E.164) or email. "Digital"
    means website forms and, from Phase 5, ad platforms.
    """

    import_block_same_campaign: bool = Field(
        default=True, description="CSV import skips a person already in the same campaign."
    )
    import_prevent_global: bool = Field(
        default=False, description="CSV import skips a person already anywhere in the CRM."
    )
    manual_block_same_agent: bool = Field(
        default=False, description="Adding a lead by hand is refused if you already own them."
    )
    manual_prevent_global: bool = Field(
        default=False, description="Adding a lead by hand is refused if the person exists."
    )
    digital_block_same_campaign: bool = Field(
        default=False, description="Website/ad leads are dropped if already in the campaign."
    )
    digital_prevent_global: bool = Field(
        default=False, description="Website/ad leads are dropped if the person exists."
    )
    routing: DuplicateRouting = Field(
        default="previous_owner",
        description=(
            "Who gets a repeat enquiry from a campaign: the owner of the person's earlier "
            "lead if that user is active (otherwise round-robin), or always round-robin."
        ),
    )


class DuplicateRulesUpdate(BaseModel):
    """`PATCH /settings/duplicates`: send only what changes."""

    import_block_same_campaign: bool | None = None
    import_prevent_global: bool | None = None
    manual_block_same_agent: bool | None = None
    manual_prevent_global: bool | None = None
    digital_block_same_campaign: bool | None = None
    digital_prevent_global: bool | None = None
    routing: DuplicateRouting | None = None


class AuditLogOut(BaseModel):
    """One audit entry."""

    id: int
    actor: UserRef | None
    entity_type: str
    entity_id: str
    action: str
    changes: dict[str, Any] | None
    ip_address: str | None
    created_at: datetime
