"""Dashboard v1 (Overview): live cards, follow-ups and recent activity, in the caller's scope.

Sample charts are drawn by the web app and clearly badged; real charts come in Phase 4.
"""

import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.configuration import LeadStatus
from app.models.leads import Activity, Lead, Task
from app.models.organization import Organization
from app.schemas.leads import DashboardCards, DashboardSummary
from app.services.access import CurrentUser, visible_user_ids
from app.services.lead_service import now_utc, scoped, status_names, to_activity, to_task

MENU = "dashboard.pipeline"


def day_bounds(timezone: str, now: datetime) -> tuple[datetime, datetime]:
    """Start and end of 'today' in the organisation's timezone, as UTC instants."""
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    start = datetime.combine(local.date(), time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


async def summary(session: AsyncSession, current: CurrentUser) -> DashboardSummary:
    """Cards, the next follow-ups and the latest activity for the Overview."""
    org = await session.get(Organization, current.organization_id)
    timezone = org.timezone if org else "Asia/Dubai"
    now = now_utc()
    start, end = day_bounds(timezone, now)
    owners = await visible_user_ids(session, current, MENU)
    scope = current.scope(MENU)

    def leads_count(*conditions: object):  # noqa: ANN202 - SQL expression helper
        return select(func.count()).select_from(
            scoped(select(Lead.id), current, owners).where(*conditions).subquery()
        )

    open_leads = await session.scalar(
        leads_count(
            Lead.status_id.in_(select(LeadStatus.id).where(LeadStatus.is_terminal.is_(False)))
        )
    )
    new_today = await session.scalar(leads_count(Lead.created_at >= start, Lead.created_at < end))
    not_contacted = await session.scalar(leads_count(Lead.first_contacted_at.is_(None)))

    calls = select(func.count()).where(
        Activity.organization_id == current.organization_id,
        Activity.type == "call",
        Activity.occurred_at >= start,
        Activity.occurred_at < end,
    )
    if owners is not None:
        calls = calls.where(Activity.user_id.in_(owners))
    calls_today = await session.scalar(calls)

    open_tasks = select(Task).where(
        Task.organization_id == current.organization_id, Task.completed_at.is_(None)
    )
    if owners is not None:
        open_tasks = open_tasks.where(Task.assigned_to_user_id.in_(owners))
    due_today = await session.scalar(
        select(func.count()).select_from(
            open_tasks.where(Task.due_at >= now, Task.due_at < end).subquery()
        )
    )
    overdue = await session.scalar(
        select(func.count()).select_from(open_tasks.where(Task.due_at < now).subquery())
    )
    follow_ups = list(
        (
            await session.scalars(
                open_tasks.where(Task.due_at < end).order_by(Task.due_at).limit(8)
            )
        ).unique()
    )

    recent_query = (
        select(Activity)
        .join(Lead, Lead.id == Activity.lead_id)
        .where(Lead.organization_id == current.organization_id, Lead.deleted_at.is_(None))
        .order_by(Activity.occurred_at.desc(), Activity.id.desc())
        .limit(10)
    )
    if owners is not None:
        recent_query = recent_query.where(Lead.owner_user_id.in_(owners))
    recent = list((await session.scalars(recent_query)).unique())
    lead_names: dict[uuid.UUID, str] = {}
    if recent:
        rows = await session.execute(
            select(Lead.id, Lead.full_name).where(Lead.id.in_({a.lead_id for a in recent}))
        )
        lead_names = dict(rows.all())
    names = await status_names(session, current)

    return DashboardSummary(
        scope=scope,  # type: ignore[arg-type]
        timezone=timezone,
        cards=DashboardCards(
            open_leads=open_leads or 0,
            new_today=new_today or 0,
            not_contacted=not_contacted or 0,
            calls_today=calls_today or 0,
            tasks_due_today=due_today or 0,
            tasks_overdue=overdue or 0,
        ),
        follow_ups=[to_task(task, now) for task in follow_ups],
        recent_activity=[to_activity(activity, names) for activity in recent],
        recent_activity_leads={str(k): v for k, v in lead_names.items()},
    )
