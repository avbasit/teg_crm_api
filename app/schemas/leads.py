"""Leads, activities, tasks, bulk actions and the dashboard summary."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ApiModel
from app.schemas.users import UserRef

LogType = Literal["call", "whatsapp", "text", "email", "meeting", "comment"]
TaskType = Literal["call_back", "send_email", "send_whatsapp", "send_text", "meeting"]
TaskState = Literal["none", "due", "overdue"]
CALL_OUTCOMES = ("answered", "did_not_respond", "dead_number")
MEETING_OUTCOMES = ("done", "rescheduled", "no_show", "cancelled")
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class StatusRef(ApiModel):
    """Status shown on a lead."""

    id: uuid.UUID
    reference: str
    name: str
    color: str
    is_contacted: bool


class LabelRef(ApiModel):
    """Label shown on a lead."""

    id: uuid.UUID
    name: str
    color: str


class CampaignRef(ApiModel):
    """Campaign shown on a lead."""

    id: uuid.UUID
    name: str


class LeadListItem(BaseModel):
    """One row of the lead table (all 22 Jaldi columns are derivable from this)."""

    id: uuid.UUID
    first_name: str
    last_name: str | None
    full_name: str
    phone: str | None = Field(description="E.164 when valid, otherwise as entered.")
    email: str | None
    status: StatusRef
    labels: list[LabelRef]
    campaign: CampaignRef | None
    owner: UserRef | None
    source: str
    sub_source: str | None
    city: str | None
    country_of_interest: str | None
    budget_text: str | None
    deal_value: Decimal | None
    deal_currency: str | None
    calls_made: int
    times_assigned: int
    is_duplicate: bool
    is_contacted: bool = Field(
        description="A call or meeting has been logged (decision 19 Nov 2026)."
    )
    task_status: TaskState
    next_task_due_at: datetime | None
    last_assigned_at: datetime | None
    last_activity_at: datetime | None
    last_activity_note: str | None
    source_created_at: datetime | None
    created_at: datetime
    updated_at: datetime


class Enquiry(BaseModel):
    """Another enquiry by the same person (contact)."""

    id: uuid.UUID
    campaign: str | None
    status: str
    status_color: str
    owner: str | None
    created_at: datetime
    visible: bool = Field(description="False when outside your scope (shown without a link).")
    full_name: str | None = None


class LeadDetail(LeadListItem):
    """Lead drawer: every field plus the person's other enquiries."""

    whatsapp: str | None
    notes: str | None
    form_answers: dict[str, Any]
    education_level: str | None
    english_test_status: str | None
    age_text: str | None
    has_family: bool | None
    source_campaign_name: str | None
    source_adset_name: str | None
    source_ad_name: str | None
    source_form_name: str | None
    first_contacted_at: datetime | None
    response_time_seconds: int | None
    other_enquiries: list[Enquiry]
    duplicate_of: Enquiry | None = Field(
        default=None, description="The person's first enquiry, when this lead is a duplicate."
    )


class LeadCreate(BaseModel):
    """`POST /leads`: a manual lead. Starts as Not Contacted."""

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, pattern=EMAIL_PATTERN, max_length=254)
    notes: str | None = Field(default=None, max_length=5000)
    owner_user_id: uuid.UUID | None = Field(default=None, description="Defaults to you.")
    campaign_id: uuid.UUID | None = None

    _clean = field_validator("last_name", "phone", "email", "notes", mode="before")(_blank_to_none)


class LeadUpdate(BaseModel):
    """`PATCH /leads/{id}`: send only the fields that change."""

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    whatsapp: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, pattern=EMAIL_PATTERN, max_length=254)
    notes: str | None = Field(default=None, max_length=5000)
    city: str | None = Field(default=None, max_length=100)
    country_of_interest: str | None = Field(default=None, max_length=100)
    budget_text: str | None = Field(default=None, max_length=200)
    deal_value: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)

    _clean = field_validator(
        "last_name",
        "phone",
        "whatsapp",
        "email",
        "notes",
        "city",
        "country_of_interest",
        "budget_text",
        mode="before",
    )(_blank_to_none)


class StatusChange(BaseModel):
    """`PATCH /leads/{id}/status`."""

    status_id: uuid.UUID


class OwnerChange(BaseModel):
    """`PATCH /leads/{id}/owner`. `null` = unassign."""

    owner_user_id: uuid.UUID | None


class LabelsSet(BaseModel):
    """`PUT /leads/{id}/labels`: the complete new label list."""

    label_ids: list[uuid.UUID] = Field(max_length=50)


class FollowUp(BaseModel):
    """Optional task created together with an activity (e.g. after a meeting)."""

    type: TaskType = "call_back"
    due_at: datetime
    notes: str | None = Field(default=None, max_length=2000)


class ActivityCreate(BaseModel):
    """`POST /leads/{id}/activities`: one of the six quick actions.

    Calls need an outcome (answered / did_not_respond / dead_number); meetings
    need one too (done / rescheduled / no_show / cancelled). A comment needs notes.
    Logging a call or meeting marks the lead as contacted.
    """

    type: LogType
    outcome: str | None = None
    notes: str | None = Field(default=None, max_length=5000)
    location: str | None = Field(default=None, max_length=200)
    follow_up: FollowUp | None = None

    _clean = field_validator("outcome", "notes", "location", mode="before")(_blank_to_none)


class ActivityOut(BaseModel):
    """A timeline entry."""

    id: int
    lead_id: uuid.UUID
    type: str
    outcome: str | None
    notes: str | None
    location: str | None
    user: UserRef | None
    from_status: str | None
    to_status: str | None
    meta: dict[str, Any]
    occurred_at: datetime


class TaskCreate(BaseModel):
    """`POST /leads/{id}/tasks`."""

    type: TaskType
    due_at: datetime
    notes: str | None = Field(default=None, max_length=2000)
    assigned_to_user_id: uuid.UUID | None = Field(
        default=None, description="Defaults to the lead owner."
    )


class TaskUpdate(BaseModel):
    """`PATCH /tasks/{id}`: complete/reopen, reschedule or edit notes."""

    completed: bool | None = None
    due_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class TaskLead(BaseModel):
    """The lead a task belongs to."""

    id: uuid.UUID
    full_name: str
    phone: str | None
    status: StatusRef


class TaskOut(BaseModel):
    """A follow-up task."""

    id: uuid.UUID
    type: str
    notes: str | None
    due_at: datetime
    completed_at: datetime | None
    is_overdue: bool
    lead: TaskLead
    assignee: UserRef | None


class BulkRequest(BaseModel):
    """Common body of bulk actions (max 500 leads per call)."""

    lead_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class BulkReassign(BulkRequest):
    """`POST /leads/bulk/reassign`."""

    owner_user_id: uuid.UUID | None


class BulkStatus(BulkRequest):
    """`POST /leads/bulk/status`. Leads not yet contacted are skipped."""

    status_id: uuid.UUID


class BulkLabels(BulkRequest):
    """`POST /leads/bulk/labels`."""

    label_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    mode: Literal["add", "remove"]


class BulkResult(BaseModel):
    """How many leads changed, and how many were skipped (with why)."""

    updated: int
    skipped: int = 0
    skipped_reason: str | None = None


class OwnerOption(BaseModel):
    """Someone a lead can be assigned to."""

    id: uuid.UUID
    full_name: str
    role: str
    is_assignable: bool


class CampaignOption(BaseModel):
    """Campaign for filters and the Add-lead form (managed in Phase 3)."""

    id: uuid.UUID
    name: str
    status: str


class DashboardCards(BaseModel):
    """Live numbers for the Overview (in the caller's scope)."""

    open_leads: int
    new_today: int
    not_contacted: int
    calls_today: int
    tasks_due_today: int
    tasks_overdue: int


class DashboardSummary(BaseModel):
    """`GET /dashboard/summary`."""

    scope: Literal["own", "team", "subtree", "all"]
    timezone: str
    cards: DashboardCards
    follow_ups: list[TaskOut]
    recent_activity: list[ActivityOut]
    recent_activity_leads: dict[str, str] = Field(description="lead id → lead name for the feed")
