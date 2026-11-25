"""Phase 4 dashboards: pipeline and productivity reports, filter options."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.campaigns import NamedRef
from app.schemas.users import UserRef


class Period(BaseModel):
    """The dates a report covers (inclusive, organisation timezone) and the one before."""

    date_from: date
    date_to: date
    previous_from: date
    previous_to: date
    timezone: str


class Metric(BaseModel):
    """A figure for the period and the same-length period before it."""

    value: float
    previous: float


class PipelineKpis(BaseModel):
    """Headline figures for leads created in the period."""

    new_leads: Metric
    contacted: Metric = Field(description="New leads that have had a call or meeting.")
    contact_rate: Metric = Field(description="Contacted ÷ new leads, 0–1.")
    qualified: Metric = Field(description="New leads now in a status marked MQL.")
    won: Metric = Field(description="New leads now in a status marked won.")
    avg_response_seconds: Metric = Field(description="Average time to first contact.")
    won_value: Metric = Field(description="Deal value of won leads.")
    unassigned: int = Field(description="New leads that still have no owner.")
    open_pipeline: int = Field(description="All open leads now (not limited to the period).")


class DayCount(BaseModel):
    """New leads on one day."""

    day: date
    count: int


class StatusSlice(BaseModel):
    """Leads in one status."""

    id: uuid.UUID
    name: str
    color: str
    count: int


class LabelCount(BaseModel):
    """Leads for one source or programme."""

    key: str
    label: str
    count: int
    won: int = 0


class CampaignRow(BaseModel):
    """A campaign's results in the period."""

    campaign: NamedRef | None
    leads: int
    contacted: int
    qualified: int
    won: int


class LeaderRow(BaseModel):
    """One owner's results for leads created in the period."""

    user: UserRef | None = Field(description="None = unassigned leads.")
    leads: int
    contacted: int
    contact_rate: float
    qualified: int
    won: int
    won_value: float
    avg_response_seconds: float | None
    calls: int = Field(description="Calls the person logged in the period (any lead).")


class PipelineReport(BaseModel):
    """`GET /reports/pipeline`."""

    period: Period
    scope: str
    currency: str
    kpis: PipelineKpis
    daily: list[DayCount]
    by_status: list[StatusSlice]
    by_source: list[LabelCount]
    by_program: list[LabelCount]
    campaigns: list[CampaignRow]
    leaderboard: list[LeaderRow]


class ProductivityKpis(BaseModel):
    """Activity in the period."""

    calls: Metric
    answered: Metric
    answer_rate: Metric = Field(description="Answered ÷ calls, 0–1.")
    meetings: Metric
    messages: Metric = Field(description="WhatsApp, text and email logged.")
    first_contacts: Metric = Field(description="Leads contacted for the first time.")
    avg_response_seconds: Metric
    tasks_completed: Metric
    tasks_overdue: int = Field(description="Open tasks past due right now.")


class CallDay(BaseModel):
    """Calls on one day."""

    day: date
    answered: int
    not_answered: int


class OutcomeCount(BaseModel):
    """Calls with one outcome."""

    outcome: str
    count: int


class HourCount(BaseModel):
    """Calls in one hour of the day (organisation time)."""

    hour: int
    count: int


class AgentRow(BaseModel):
    """One person's activity in the period."""

    user: UserRef
    calls: int
    answered: int
    answer_rate: float
    meetings: int
    messages: int
    first_contacts: int
    avg_response_seconds: float | None
    tasks_completed: int
    tasks_overdue: int


class ProductivityReport(BaseModel):
    """`GET /reports/productivity`."""

    period: Period
    scope: str
    kpis: ProductivityKpis
    calls_daily: list[CallDay]
    call_outcomes: list[OutcomeCount]
    calls_by_hour: list[HourCount]
    agents: list[AgentRow]


class MarketingKpis(BaseModel):
    """Ad spend against the leads and deals it produced (spend in the deal currency)."""

    spend: Metric
    leads: Metric = Field(description="CRM leads from lead ads created in the period.")
    cost_per_lead: Metric
    qualified: Metric
    cost_per_qualified: Metric
    won: Metric
    won_value: Metric
    roas: Metric = Field(description="Won deal value ÷ spend.")
    impressions: int
    clicks: int
    platform_leads: int = Field(description="Leads as Meta counts them (for comparison).")


class MarketingDay(BaseModel):
    """Spend and new leads on one day."""

    day: date
    spend: float
    leads: int


class AdRow(BaseModel):
    """One line of the Campaign › Ad set › Ad drill-down."""

    level: str = Field(description="campaign, adset or ad")
    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    crm_campaign_id: uuid.UUID | None = None
    spend: float
    impressions: int
    clicks: int
    platform_leads: int
    leads: int
    qualified: int
    won: int
    won_value: float
    cost_per_lead: float | None
    roas: float | None


class MarketingReport(BaseModel):
    """`GET /reports/marketing`."""

    period: Period
    currency: str = Field(description="Deal currency; spend is converted into it.")
    spend_currencies: list[str]
    missing_rates: list[str] = Field(description="Spend currencies with no conversion rate set.")
    connected: bool
    last_spend_sync_at: datetime | None
    kpis: MarketingKpis
    daily: list[MarketingDay]
    rows: list[AdRow]


class FilterOption(BaseModel):
    """One choice in a dashboard filter."""

    id: uuid.UUID
    name: str


class ReportFilterOptions(BaseModel):
    """`GET /reports/filters`: choices limited to what the caller may see."""

    campaigns: list[FilterOption]
    programs: list[FilterOption]
    markets: list[FilterOption]
    teams: list[FilterOption]
    owners: list[FilterOption]
