"""Lead table query: filters, search, sort and paging (all inside the caller's scope)."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leads import Lead, LeadLabel
from app.services.access import CurrentUser
from app.services.lead_service import now_utc, scoped

SortKey = Literal[
    "created_at", "updated_at", "last_activity_at", "next_task_due_at", "full_name", "calls_made"
]
UNASSIGNED = "unassigned"


@dataclass
class LeadFilters:
    """Filters of the lead table (each is optional; lists mean 'any of')."""

    search: str | None = None
    status_ids: list[uuid.UUID] = field(default_factory=list)
    label_ids: list[uuid.UUID] = field(default_factory=list)
    campaign_ids: list[uuid.UUID] = field(default_factory=list)
    owner_ids: list[str] = field(default_factory=list)  # user ids or "unassigned"
    sources: list[str] = field(default_factory=list)
    task_status: list[Literal["none", "due", "overdue"]] = field(default_factory=list)
    created_from: datetime | None = None
    created_to: datetime | None = None
    updated_before: datetime | None = None
    calls_min: int | None = None
    calls_max: int | None = None
    duplicates: bool | None = None
    contacted: bool | None = None


def _apply(query: Select[tuple[Lead]], f: LeadFilters) -> Select[tuple[Lead]]:
    if f.search:
        term = f.search.strip()
        digits = "".join(ch for ch in term if ch.isdigit())
        conditions = [Lead.full_name.ilike(f"%{term}%"), Lead.email.ilike(f"%{term}%")]
        if len(digits) >= 4:
            conditions += [Lead.phone_e164.contains(digits), Lead.phone_raw.contains(digits)]
        query = query.where(or_(*conditions))
    if f.status_ids:
        query = query.where(Lead.status_id.in_(f.status_ids))
    if f.label_ids:
        query = query.where(
            exists().where(LeadLabel.lead_id == Lead.id, LeadLabel.label_id.in_(f.label_ids))
        )
    if f.campaign_ids:
        query = query.where(Lead.campaign_id.in_(f.campaign_ids))
    if f.owner_ids:
        user_ids = [uuid.UUID(o) for o in f.owner_ids if o != UNASSIGNED]
        conditions = [Lead.owner_user_id.in_(user_ids)] if user_ids else []
        if UNASSIGNED in f.owner_ids:
            conditions.append(Lead.owner_user_id.is_(None))
        query = query.where(or_(*conditions))
    if f.sources:
        query = query.where(Lead.source.in_(f.sources))
    if f.task_status:
        now = now_utc()
        conditions = []
        if "none" in f.task_status:
            conditions.append(Lead.next_task_due_at.is_(None))
        if "due" in f.task_status:
            conditions.append(Lead.next_task_due_at >= now)
        if "overdue" in f.task_status:
            conditions.append(Lead.next_task_due_at < now)
        query = query.where(or_(*conditions))
    if f.created_from:
        query = query.where(Lead.created_at >= f.created_from)
    if f.created_to:
        query = query.where(Lead.created_at < f.created_to)
    if f.updated_before:
        query = query.where(Lead.updated_at < f.updated_before)
    if f.calls_min is not None:
        query = query.where(Lead.calls_made >= f.calls_min)
    if f.calls_max is not None:
        query = query.where(Lead.calls_made <= f.calls_max)
    if f.duplicates is not None:
        query = query.where(Lead.is_duplicate.is_(f.duplicates))
    if f.contacted is not None:
        query = query.where(
            Lead.first_contacted_at.is_not(None)
            if f.contacted
            else Lead.first_contacted_at.is_(None)
        )
    return query


async def list_leads(
    session: AsyncSession,
    current: CurrentUser,
    owners: set[uuid.UUID] | None,
    filters: LeadFilters,
    *,
    sort: SortKey,
    descending: bool,
    limit: int,
    offset: int,
) -> tuple[list[Lead], int]:
    """A page of leads and the total that match."""
    base = _apply(scoped(select(Lead), current, owners), filters)
    total = (
        await session.scalar(
            select(func.count()).select_from(base.with_only_columns(Lead.id).subquery())
        )
        or 0
    )
    column = getattr(Lead, sort)
    order = column.desc().nulls_last() if descending else column.asc().nulls_last()
    rows = await session.scalars(base.order_by(order, Lead.id.desc()).limit(limit).offset(offset))
    return list(rows.unique()), total
