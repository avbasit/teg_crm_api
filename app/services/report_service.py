"""Phase 4 dashboards: pipeline and productivity figures, filter options, CSV exports.

Definitions (shown on the dashboards too):
- Pipeline figures follow the **leads created in the period** (a cohort), and their
  status *now*: qualified = status marked MQL, won = status marked won.
- Productivity figures are **activities logged in the period** by the people in view.
- Days and hours are in the organisation's timezone (Gulf time).
- Every figure is limited to the caller's data scope on that dashboard.

All figures are direct aggregate queries (no cached copies), so they always match
the lead table; at TEG's volume (~10k leads a year) each report takes well under a
second.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, case, cast, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Date

from app.core.csv_export import to_csv
from app.models.configuration import LeadStatus, Market, Program
from app.models.identity import Team, TeamMember, User
from app.models.leads import Activity, Campaign, Lead, Task
from app.models.organization import Organization
from app.schemas.campaigns import NamedRef
from app.schemas.reports import (
    AgentRow,
    CallDay,
    CampaignRow,
    DayCount,
    FilterOption,
    HourCount,
    LabelCount,
    LeaderRow,
    Metric,
    OutcomeCount,
    Period,
    PipelineKpis,
    PipelineReport,
    ProductivityKpis,
    ProductivityReport,
    ReportFilterOptions,
    StatusSlice,
)
from app.schemas.users import UserRef
from app.services.access import CurrentUser, visible_user_ids
from app.services.lead_service import now_utc

PIPELINE = "dashboard.pipeline"
PRODUCTIVITY = "dashboard.productivity"
MESSAGE_TYPES = ("whatsapp", "text", "email")
SOURCE_LABELS = {
    "facebook": "Facebook",
    "tiktok": "TikTok",
    "webhook": "Website",
    "csv": "CSV import",
    "excel": "Excel import",
    "manual": "Manual",
    "event": "Event",
    "whatsapp": "WhatsApp",
    "bayut": "Bayut",
    "dubizzle": "Dubizzle",
    "zapier": "Zapier",
}
MAX_RANGE_DAYS = 731


@dataclass
class ReportFilters:
    """Dashboard filters; dates are inclusive, in organisation time."""

    date_from: date
    date_to: date
    campaign_ids: list[uuid.UUID] = field(default_factory=list)
    program_ids: list[uuid.UUID] = field(default_factory=list)
    market_ids: list[uuid.UUID] = field(default_factory=list)
    team_ids: list[uuid.UUID] = field(default_factory=list)
    owner_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class Window:
    """Start/end instants of the period and the previous one."""

    start: datetime
    end: datetime
    prev_start: datetime
    prev_end: datetime
    period: Period
    tz: str


def _window(filters: ReportFilters, tz: str) -> Window:
    zone = ZoneInfo(tz)
    days = (filters.date_to - filters.date_from).days + 1
    start = datetime.combine(filters.date_from, time.min, tzinfo=zone)
    end = datetime.combine(filters.date_to + timedelta(days=1), time.min, tzinfo=zone)
    prev_from = filters.date_from - timedelta(days=days)
    prev_to = filters.date_from - timedelta(days=1)
    return Window(
        start=start,
        end=end,
        prev_start=datetime.combine(prev_from, time.min, tzinfo=zone),
        prev_end=start,
        period=Period(
            date_from=filters.date_from,
            date_to=filters.date_to,
            previous_from=prev_from,
            previous_to=prev_to,
            timezone=tz,
        ),
        tz=tz,
    )


async def _org(session: AsyncSession, current: CurrentUser) -> Organization:
    org = await session.get(Organization, current.organization_id)
    assert org is not None
    return org


def _campaign_filter(f: ReportFilters) -> list[Any]:
    """Conditions on ``Lead.campaign_id`` for campaign / programme / market filters."""
    conditions: list[Any] = []
    if f.campaign_ids:
        conditions.append(Lead.campaign_id.in_(f.campaign_ids))
    if f.program_ids:
        conditions.append(
            Lead.campaign_id.in_(select(Campaign.id).where(Campaign.program_id.in_(f.program_ids)))
        )
    if f.market_ids:
        conditions.append(
            Lead.campaign_id.in_(select(Campaign.id).where(Campaign.market_id.in_(f.market_ids)))
        )
    return conditions


def _people_filter(column: Any, owners: set[uuid.UUID] | None, f: ReportFilters) -> list[Any]:
    """Conditions on a user column: data scope, team filter and person filter."""
    conditions: list[Any] = []
    if owners is not None:
        conditions.append(column.in_(owners))
    if f.team_ids:
        conditions.append(
            column.in_(select(TeamMember.user_id).where(TeamMember.team_id.in_(f.team_ids)))
        )
    if f.owner_ids:
        conditions.append(column.in_(f.owner_ids))
    return conditions


def _lead_base(current: CurrentUser, owners: set[uuid.UUID] | None, f: ReportFilters) -> list[Any]:
    return [
        Lead.organization_id == current.organization_id,
        Lead.deleted_at.is_(None),
        *_people_filter(Lead.owner_user_id, owners, f),
        *_campaign_filter(f),
    ]


def _local_day(column: Any, tz: str) -> Any:
    return cast(func.timezone(tz, column), Date)


def _metric(now: float | None, before: float | None) -> Metric:
    return Metric(value=float(now or 0), previous=float(before or 0))


def _rate(part: float | None, whole: float | None) -> float:
    return float(part or 0) / float(whole) if whole else 0.0


def _fill_days(rows: dict[date, Any], f: ReportFilters, empty: Any) -> list[tuple[date, Any]]:
    days = (f.date_to - f.date_from).days + 1
    return [
        (f.date_from + timedelta(days=i), rows.get(f.date_from + timedelta(days=i), empty))
        for i in range(days)
    ]


def _ref(user: User | None) -> UserRef | None:
    return UserRef.model_validate(user) if user else None


async def _users(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, User]:
    if not ids:
        return {}
    return {u.id: u for u in (await session.scalars(select(User).where(User.id.in_(ids)))).unique()}


# --- Pipeline -----------------------------------------------------------------------------


async def pipeline(session: AsyncSession, current: CurrentUser, f: ReportFilters) -> PipelineReport:
    """Overview dashboard: what happened to the leads created in the period."""
    org = await _org(session, current)
    w = _window(f, org.timezone or "Asia/Dubai")
    owners = await visible_user_ids(session, current, PIPELINE)
    base = _lead_base(current, owners, f)
    in_period = [Lead.created_at >= w.start, Lead.created_at < w.end]
    joined = Lead.status_id == LeadStatus.id
    contacted = Lead.first_contacted_at.is_not(None)

    def cohort(start: datetime, end: datetime) -> Select[Any]:
        return (
            select(
                func.count(),
                func.count(case((contacted, 1))),
                func.count(case((LeadStatus.is_mql.is_(True), 1))),
                func.count(case((LeadStatus.is_won.is_(True), 1))),
                func.avg(Lead.response_time_seconds),
                func.coalesce(func.sum(case((LeadStatus.is_won.is_(True), Lead.deal_value))), 0),
                func.count(case((Lead.owner_user_id.is_(None), 1))),
            )
            .select_from(Lead)
            .join(LeadStatus, joined)
            .where(*base, Lead.created_at >= start, Lead.created_at < end)
        )

    now_row = (await session.execute(cohort(w.start, w.end))).one()
    before = (await session.execute(cohort(w.prev_start, w.prev_end))).one()
    open_pipeline = await session.scalar(
        select(func.count())
        .select_from(Lead)
        .join(LeadStatus, joined)
        .where(*base, LeadStatus.is_terminal.is_(False))
    )
    kpis = PipelineKpis(
        new_leads=_metric(now_row[0], before[0]),
        contacted=_metric(now_row[1], before[1]),
        contact_rate=Metric(
            value=_rate(now_row[1], now_row[0]), previous=_rate(before[1], before[0])
        ),
        qualified=_metric(now_row[2], before[2]),
        won=_metric(now_row[3], before[3]),
        avg_response_seconds=_metric(now_row[4], before[4]),
        won_value=_metric(now_row[5], before[5]),
        unassigned=now_row[6] or 0,
        open_pipeline=open_pipeline or 0,
    )

    day = _local_day(Lead.created_at, w.tz)
    daily_rows = dict(
        (
            await session.execute(select(day, func.count()).where(*base, *in_period).group_by(day))
        ).all()
    )
    daily = [DayCount(day=d, count=c) for d, c in _fill_days(daily_rows, f, 0)]

    by_status = [
        StatusSlice(id=r[0], name=r[1], color=r[2], count=r[3])
        for r in (
            await session.execute(
                select(LeadStatus.id, LeadStatus.name, LeadStatus.color, func.count())
                .join(Lead, and_(joined, *base, *in_period))
                .group_by(LeadStatus.id, LeadStatus.name, LeadStatus.color, LeadStatus.sort_order)
                .order_by(LeadStatus.sort_order)
            )
        ).all()
    ]

    won_flag = func.count(case((LeadStatus.is_won.is_(True), 1)))
    by_source = [
        LabelCount(key=r[0], label=SOURCE_LABELS.get(r[0], r[0]), count=r[1], won=r[2])
        for r in (
            await session.execute(
                select(Lead.source, func.count(), won_flag)
                .select_from(Lead)
                .join(LeadStatus, joined)
                .where(*base, *in_period)
                .group_by(Lead.source)
                .order_by(func.count().desc())
            )
        ).all()
    ]
    by_program = [
        LabelCount(
            key=str(r[0]) if r[0] else "none", label=r[1] or "No programme", count=r[2], won=r[3]
        )
        for r in (
            await session.execute(
                select(Program.id, Program.name, func.count(), won_flag)
                .select_from(Lead)
                .join(LeadStatus, joined)
                .outerjoin(Campaign, Campaign.id == Lead.campaign_id)
                .outerjoin(Program, Program.id == Campaign.program_id)
                .where(*base, *in_period)
                .group_by(Program.id, Program.name)
                .order_by(func.count().desc())
            )
        ).all()
    ]

    campaigns = [
        CampaignRow(
            campaign=NamedRef(id=r[0], name=r[1]) if r[0] else None,
            leads=r[2],
            contacted=r[3],
            qualified=r[4],
            won=r[5],
        )
        for r in (
            await session.execute(
                select(
                    Campaign.id,
                    Campaign.name,
                    func.count(),
                    func.count(case((contacted, 1))),
                    func.count(case((LeadStatus.is_mql.is_(True), 1))),
                    won_flag,
                )
                .select_from(Lead)
                .join(LeadStatus, joined)
                .outerjoin(Campaign, Campaign.id == Lead.campaign_id)
                .where(*base, *in_period)
                .group_by(Campaign.id, Campaign.name)
                .order_by(func.count().desc())
                .limit(10)
            )
        ).all()
    ]

    leader_rows = (
        await session.execute(
            select(
                Lead.owner_user_id,
                func.count(),
                func.count(case((contacted, 1))),
                func.count(case((LeadStatus.is_mql.is_(True), 1))),
                won_flag,
                func.coalesce(func.sum(case((LeadStatus.is_won.is_(True), Lead.deal_value))), 0),
                func.avg(Lead.response_time_seconds),
            )
            .select_from(Lead)
            .join(LeadStatus, joined)
            .where(*base, *in_period)
            .group_by(Lead.owner_user_id)
        )
    ).all()
    calls = dict(
        (
            await session.execute(
                select(Activity.user_id, func.count())
                .where(
                    Activity.organization_id == current.organization_id,
                    Activity.type == "call",
                    Activity.occurred_at >= w.start,
                    Activity.occurred_at < w.end,
                    Activity.user_id.in_([r[0] for r in leader_rows if r[0]]),
                )
                .group_by(Activity.user_id)
            )
        ).all()
    )
    people = await _users(session, {r[0] for r in leader_rows if r[0]})
    leaderboard = sorted(
        (
            LeaderRow(
                user=_ref(people.get(r[0])) if r[0] else None,
                leads=r[1],
                contacted=r[2],
                contact_rate=_rate(r[2], r[1]),
                qualified=r[3],
                won=r[4],
                won_value=float(r[5] or 0),
                avg_response_seconds=float(r[6]) if r[6] is not None else None,
                calls=calls.get(r[0], 0) if r[0] else 0,
            )
            for r in leader_rows
        ),
        key=lambda row: (-row.won, -row.qualified, -row.contacted, -row.leads),
    )

    return PipelineReport(
        period=w.period,
        scope=current.scope(PIPELINE),
        currency=((org.settings or {}).get("deal_value") or {}).get("currency")
        or org.default_currency,
        kpis=kpis,
        daily=daily,
        by_status=by_status,
        by_source=by_source,
        by_program=by_program,
        campaigns=campaigns,
        leaderboard=leaderboard,
    )


# --- Productivity -------------------------------------------------------------------------


async def productivity(
    session: AsyncSession, current: CurrentUser, f: ReportFilters
) -> ProductivityReport:
    """Productivity dashboard: calls, meetings, messages, first contacts and tasks."""
    org = await _org(session, current)
    w = _window(f, org.timezone or "Asia/Dubai")
    owners = await visible_user_ids(session, current, PRODUCTIVITY)
    people_scope = _people_filter(Activity.user_id, owners, f)
    lead_scope = _campaign_filter(f)
    activity_base = [
        Activity.organization_id == current.organization_id,
        Activity.user_id.is_not(None),
        *people_scope,
    ]
    if lead_scope:
        activity_base.append(
            Activity.lead_id.in_(select(Lead.id).where(*lead_scope))  # type: ignore[arg-type]
        )
    is_call = Activity.type == "call"
    answered = and_(is_call, Activity.outcome == "answered")

    def activity_totals(start: datetime, end: datetime) -> Select[Any]:
        return select(
            func.count(case((is_call, 1))),
            func.count(case((answered, 1))),
            func.count(case((Activity.type == "meeting", 1))),
            func.count(case((Activity.type.in_(MESSAGE_TYPES), 1))),
        ).where(*activity_base, Activity.occurred_at >= start, Activity.occurred_at < end)

    first_base = [
        Lead.organization_id == current.organization_id,
        Lead.deleted_at.is_(None),
        *_people_filter(Lead.owner_user_id, owners, f),
        *lead_scope,
    ]

    def first_contacts(start: datetime, end: datetime) -> Select[Any]:
        return select(func.count(), func.avg(Lead.response_time_seconds)).where(
            *first_base, Lead.first_contacted_at >= start, Lead.first_contacted_at < end
        )

    task_people = _people_filter(Task.completed_by_user_id, owners, f)
    task_leads = [Task.lead_id.in_(select(Lead.id).where(*lead_scope))] if lead_scope else []

    def tasks_done(start: datetime, end: datetime) -> Select[Any]:
        return select(func.count()).where(
            Task.organization_id == current.organization_id,
            Task.completed_at >= start,
            Task.completed_at < end,
            *task_people,
            *task_leads,
        )

    now_a = (await session.execute(activity_totals(w.start, w.end))).one()
    prev_a = (await session.execute(activity_totals(w.prev_start, w.prev_end))).one()
    now_f = (await session.execute(first_contacts(w.start, w.end))).one()
    prev_f = (await session.execute(first_contacts(w.prev_start, w.prev_end))).one()
    now_t = await session.scalar(tasks_done(w.start, w.end))
    prev_t = await session.scalar(tasks_done(w.prev_start, w.prev_end))
    overdue_people = _people_filter(Task.assigned_to_user_id, owners, f)
    overdue = await session.scalar(
        select(func.count()).where(
            Task.organization_id == current.organization_id,
            Task.completed_at.is_(None),
            Task.due_at < now_utc(),
            *overdue_people,
            *task_leads,
        )
    )
    kpis = ProductivityKpis(
        calls=_metric(now_a[0], prev_a[0]),
        answered=_metric(now_a[1], prev_a[1]),
        answer_rate=Metric(value=_rate(now_a[1], now_a[0]), previous=_rate(prev_a[1], prev_a[0])),
        meetings=_metric(now_a[2], prev_a[2]),
        messages=_metric(now_a[3], prev_a[3]),
        first_contacts=_metric(now_f[0], prev_f[0]),
        avg_response_seconds=_metric(now_f[1], prev_f[1]),
        tasks_completed=_metric(now_t, prev_t),
        tasks_overdue=overdue or 0,
    )

    in_period = [Activity.occurred_at >= w.start, Activity.occurred_at < w.end]
    day = _local_day(Activity.occurred_at, w.tz)
    call_days = {
        r[0]: (r[1], r[2])
        for r in (
            await session.execute(
                select(day, func.count(case((answered, 1))), func.count())
                .where(*activity_base, *in_period, is_call)
                .group_by(day)
            )
        ).all()
    }
    calls_daily = [
        CallDay(day=d, answered=v[0], not_answered=v[1] - v[0])
        for d, v in _fill_days(call_days, f, (0, 0))
    ]
    call_outcomes = [
        OutcomeCount(outcome=r[0] or "unknown", count=r[1])
        for r in (
            await session.execute(
                select(Activity.outcome, func.count())
                .where(*activity_base, *in_period, is_call)
                .group_by(Activity.outcome)
                .order_by(func.count().desc())
            )
        ).all()
    ]
    hour = extract("hour", func.timezone(w.tz, Activity.occurred_at))
    hours = dict(
        (
            await session.execute(
                select(hour, func.count()).where(*activity_base, *in_period, is_call).group_by(hour)
            )
        ).all()
    )
    calls_by_hour = [HourCount(hour=h, count=int(hours.get(h, 0) or 0)) for h in range(24)]

    per_user = {
        r[0]: r[1:]
        for r in (
            await session.execute(
                select(
                    Activity.user_id,
                    func.count(case((is_call, 1))),
                    func.count(case((answered, 1))),
                    func.count(case((Activity.type == "meeting", 1))),
                    func.count(case((Activity.type.in_(MESSAGE_TYPES), 1))),
                )
                .where(*activity_base, *in_period)
                .group_by(Activity.user_id)
            )
        ).all()
    }
    firsts = {
        r[0]: (r[1], r[2])
        for r in (
            await session.execute(
                select(Lead.owner_user_id, func.count(), func.avg(Lead.response_time_seconds))
                .where(
                    *first_base,
                    Lead.owner_user_id.is_not(None),
                    Lead.first_contacted_at >= w.start,
                    Lead.first_contacted_at < w.end,
                )
                .group_by(Lead.owner_user_id)
            )
        ).all()
    }
    done = dict(
        (
            await session.execute(
                select(Task.completed_by_user_id, func.count())
                .where(
                    Task.organization_id == current.organization_id,
                    Task.completed_at >= w.start,
                    Task.completed_at < w.end,
                    Task.completed_by_user_id.is_not(None),
                    *task_people,
                    *task_leads,
                )
                .group_by(Task.completed_by_user_id)
            )
        ).all()
    )
    late = dict(
        (
            await session.execute(
                select(Task.assigned_to_user_id, func.count())
                .where(
                    Task.organization_id == current.organization_id,
                    Task.completed_at.is_(None),
                    Task.due_at < now_utc(),
                    Task.assigned_to_user_id.is_not(None),
                    *overdue_people,
                    *task_leads,
                )
                .group_by(Task.assigned_to_user_id)
            )
        ).all()
    )
    ids = set(per_user) | set(firsts) | set(done) | set(late)
    people = await _users(session, ids)
    agents = []
    for user_id in ids:
        user = people.get(user_id)
        if user is None:
            continue
        c, a, m, msg = per_user.get(user_id, (0, 0, 0, 0))
        fc, avg = firsts.get(user_id, (0, None))
        agents.append(
            AgentRow(
                user=UserRef.model_validate(user),
                calls=c,
                answered=a,
                answer_rate=_rate(a, c),
                meetings=m,
                messages=msg,
                first_contacts=fc,
                avg_response_seconds=float(avg) if avg is not None else None,
                tasks_completed=done.get(user_id, 0),
                tasks_overdue=late.get(user_id, 0),
            )
        )
    agents.sort(key=lambda row: (-row.calls, -row.first_contacts, row.user.full_name))

    return ProductivityReport(
        period=w.period,
        scope=current.scope(PRODUCTIVITY),
        kpis=kpis,
        calls_daily=calls_daily,
        call_outcomes=call_outcomes,
        calls_by_hour=calls_by_hour,
        agents=agents,
    )


# --- Filter options -----------------------------------------------------------------------


async def filter_options(
    session: AsyncSession, current: CurrentUser, menu: str
) -> ReportFilterOptions:
    """Campaigns, programmes, markets, teams and people the caller may filter by."""
    org_id = current.organization_id
    owners = await visible_user_ids(session, current, menu)
    campaigns = await session.execute(
        select(Campaign.id, Campaign.name)
        .where(Campaign.organization_id == org_id, Campaign.deleted_at.is_(None))
        .order_by(Campaign.name)
    )
    programs = await session.execute(
        select(Program.id, Program.name)
        .where(Program.organization_id == org_id)
        .order_by(Program.name)
    )
    markets = await session.execute(
        select(Market.id, Market.name)
        .where(Market.organization_id == org_id)
        .order_by(Market.sort_order, Market.name)
    )
    teams_query = select(Team.id, Team.name).where(Team.organization_id == org_id)
    if owners is not None:
        teams_query = teams_query.where(
            Team.id.in_(
                select(TeamMember.team_id).where(
                    TeamMember.user_id == current.user.id, TeamMember.team_role == "manager"
                )
            )
        )
    teams = await session.execute(teams_query.order_by(Team.name))
    people_query = select(User.id, User.full_name).where(
        User.organization_id == org_id, User.status != "archived"
    )
    if owners is not None:
        people_query = people_query.where(User.id.in_(owners))
    people = await session.execute(people_query.order_by(User.full_name))

    def opts(rows: Any) -> list[FilterOption]:
        return [FilterOption(id=r[0], name=r[1]) for r in rows.all()]

    return ReportFilterOptions(
        campaigns=opts(campaigns),
        programs=opts(programs),
        markets=opts(markets),
        teams=opts(teams),
        owners=opts(people),
    )


# --- CSV ----------------------------------------------------------------------------------


def _minutes(seconds: float | None) -> str:
    return f"{seconds / 60:.1f}" if seconds is not None else ""


def leaderboard_csv(report: PipelineReport) -> str:
    """The agent leaderboard as CSV."""
    return to_csv(
        [
            "Agent",
            "New leads",
            "Contacted",
            "Contact rate %",
            "Qualified (MQL)",
            "Won",
            f"Won value ({report.currency})",
            "Avg response (min)",
            "Calls logged",
        ],
        [
            [
                row.user.full_name if row.user else "Unassigned",
                row.leads,
                row.contacted,
                round(row.contact_rate * 100, 1),
                row.qualified,
                row.won,
                round(row.won_value, 2),
                _minutes(row.avg_response_seconds),
                row.calls,
            ]
            for row in report.leaderboard
        ],
    )


def productivity_csv(report: ProductivityReport) -> str:
    """The per-person productivity table as CSV."""
    return to_csv(
        [
            "Person",
            "Calls",
            "Answered",
            "Answer rate %",
            "Meetings",
            "Messages",
            "First contacts",
            "Avg response (min)",
            "Tasks completed",
            "Tasks overdue now",
        ],
        [
            [
                row.user.full_name,
                row.calls,
                row.answered,
                round(row.answer_rate * 100, 1),
                row.meetings,
                row.messages,
                row.first_contacts,
                _minutes(row.avg_response_seconds),
                row.tasks_completed,
                row.tasks_overdue,
            ]
            for row in report.agents
        ],
    )


def check_range(f: ReportFilters) -> str | None:
    """Why a date range is unusable, if it is."""
    if f.date_to < f.date_from:
        return "The end date is before the start date."
    if (f.date_to - f.date_from).days + 1 > MAX_RANGE_DAYS:
        return "Choose a period of two years or less."
    return None
