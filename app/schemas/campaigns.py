"""Campaigns, their round-robin agents, website keys, imports and website intake."""

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ApiModel
from app.schemas.users import UserRef

CampaignType = Literal["paid_social", "event", "website", "portal", "import", "manual", "referral"]
CampaignStatus = Literal["draft", "active", "paused", "archived"]
LeadSource = Literal[
    "facebook", "tiktok", "csv", "excel", "webhook", "bayut", "dubizzle",
    "zapier", "manual", "event", "whatsapp",
]  # fmt: skip
Strategy = Literal["round_robin", "manual"]
ImportField = Literal[
    "first_name", "last_name", "full_name", "phone", "whatsapp", "email", "city",
    "country_of_interest", "budget_text", "education_level", "english_test_status",
    "age_text", "notes", "external_lead_id", "source_created_at", "sub_source",
]  # fmt: skip

# Default lead source for each campaign type.
DEFAULT_SOURCE: dict[str, str] = {
    "paid_social": "facebook",
    "event": "event",
    "website": "webhook",
    "portal": "bayut",
    "import": "csv",
    "manual": "manual",
    "referral": "manual",
}
_ORIGIN = re.compile(r"^https?://[a-z0-9.-]+(:\d{1,5})?$")


class NamedRef(ApiModel):
    """A programme or market on a campaign."""

    id: uuid.UUID
    name: str


class CampaignListItem(BaseModel):
    """One row of the campaign list."""

    id: uuid.UUID
    name: str
    campaign_type: CampaignType
    source: str
    status: CampaignStatus
    program: NamedRef | None
    market: NamedRef | None
    assignment_strategy: Strategy
    agents_total: int
    agents_active: int
    leads_total: int
    last_lead_at: datetime | None
    created_at: datetime


class CampaignAgentOut(BaseModel):
    """An agent in the round-robin."""

    user: UserRef
    user_status: str
    is_active: bool = Field(description="Switched on: receives leads from this campaign.")
    is_next: bool = Field(description="Next in line for a lead.")
    receiving: bool = Field(description="Switched on and the account is active.")
    leads_assigned: int


class StatusCount(BaseModel):
    """Leads in one status."""

    id: uuid.UUID
    name: str
    color: str
    count: int


class CampaignStats(BaseModel):
    """Lead counts for the campaign page."""

    total: int
    not_contacted: int
    unassigned: int
    duplicates: int
    last_7_days: int
    by_status: list[StatusCount]


class CampaignDetail(CampaignListItem):
    """Campaign page: details, round-robin and lead counts."""

    agents: list[CampaignAgentOut]
    stats: CampaignStats
    created_by: UserRef | None
    intake_url: str


class CampaignCounts(BaseModel):
    """Campaigns per tab."""

    active: int
    paused: int
    archived: int
    draft: int


class AgentIn(BaseModel):
    """An agent to put in the round-robin."""

    user_id: uuid.UUID
    is_active: bool = True


class CampaignCreate(BaseModel):
    """`POST /campaigns`."""

    name: str = Field(min_length=1, max_length=120)
    campaign_type: CampaignType
    source: LeadSource | None = Field(default=None, description="Defaults from the type.")
    program_id: uuid.UUID | None = None
    market_id: uuid.UUID | None = None
    assignment_strategy: Strategy = "round_robin"
    agents: list[AgentIn] = Field(default_factory=list, max_length=200)


class CampaignUpdate(BaseModel):
    """`PATCH /campaigns/{id}`: send only what changes (null clears programme/market)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    source: LeadSource | None = None
    program_id: uuid.UUID | None = None
    market_id: uuid.UUID | None = None
    assignment_strategy: Strategy | None = None


class CampaignStatusChange(BaseModel):
    """`PATCH /campaigns/{id}/status`. Paused campaigns still accept and assign leads."""

    status: Literal["active", "paused", "archived"]


class AgentsSet(BaseModel):
    """`PUT /campaigns/{id}/agents`: the full round-robin list."""

    agents: list[AgentIn] = Field(max_length=200)


class AgentToggle(BaseModel):
    """`PATCH /campaigns/{id}/agents/{user_id}`: switch one agent on or off."""

    is_active: bool


class AgentOption(BaseModel):
    """Someone who can be put in a round-robin."""

    id: uuid.UUID
    full_name: str
    username: str
    role_name: str
    status: str


class ApiKeyOut(BaseModel):
    """A website key (the secret itself is shown only once)."""

    id: uuid.UUID
    name: str
    key_prefix: str
    allowed_origins: list[str]
    created_by: UserRef | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    """A new key, with the secret. Store it now: it can't be shown again."""

    key: str
    intake_url: str


class ApiKeyCreate(BaseModel):
    """`POST /campaigns/{id}/api-keys`."""

    name: str = Field(min_length=1, max_length=80)
    allowed_origins: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Sites allowed to use the key from a browser, e.g. https://tegconsultancy.com",
    )

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            origin = value.strip().lower().rstrip("/")
            if not _ORIGIN.match(origin):
                raise ValueError(f"'{value}' isn't a site address like https://example.com")
            cleaned.append(origin)
        return sorted(set(cleaned))


# --- Imports ------------------------------------------------------------------------------


class ImportAssignment(BaseModel):
    """How imported leads are assigned."""

    mode: Literal["campaign", "user", "unassigned"] = "campaign"
    user_id: uuid.UUID | None = None


class ImportCreate(BaseModel):
    """`POST /imports`: start an import; rows follow in batches."""

    campaign_id: uuid.UUID
    file_name: str = Field(min_length=1, max_length=255)
    total_rows: int = Field(ge=1, le=100_000)
    mapping: dict[str, ImportField] = Field(description="CSV column → lead field.")
    parse_notes: bool = Field(
        default=True, description="Read `question: answer` lines in Notes as form answers."
    )
    assignment: ImportAssignment = ImportAssignment()


class ImportRowIn(BaseModel):
    """One CSV row, already mapped in the browser."""

    row_number: int = Field(ge=1)
    values: dict[ImportField, str | None]
    raw: dict[str, Any] = Field(default_factory=dict, description="The original row.")


class ImportBatch(BaseModel):
    """`POST /imports/{id}/rows`: up to 250 rows per call."""

    rows: list[ImportRowIn] = Field(min_length=1, max_length=250)


class ImportRowResult(BaseModel):
    """What happened to one row."""

    row_number: int
    status: Literal["imported", "skipped", "failed"]
    message: str | None
    lead_id: uuid.UUID | None
    duplicate: bool = False


class ImportBatchResult(BaseModel):
    """Counts for one batch."""

    imported: int
    skipped: int
    failed: int
    rows: list[ImportRowResult]


class ImportOut(BaseModel):
    """An import in the history."""

    id: uuid.UUID
    file_name: str
    campaign: NamedRef | None
    status: str
    total_rows: int | None
    imported_rows: int
    skipped_duplicate_rows: int
    failed_rows: int
    created_by: UserRef | None
    created_at: datetime
    completed_at: datetime | None


class ImportRowOut(BaseModel):
    """A row from the import log."""

    row_number: int
    raw: dict[str, Any]
    lead_id: uuid.UUID | None
    error: str | None


# --- Website intake -----------------------------------------------------------------------


class IntakeAccepted(BaseModel):
    """Reply to a website form: the lead was received."""

    status: Literal["received", "duplicate_event"]
    reference: uuid.UUID
