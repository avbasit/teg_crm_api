"""`/tasks`: My Tasks (rights: menu `tasks`) and task updates."""

import uuid
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.core.database import DbSession
from app.dependencies import CurrentUserDep, RequirePermission
from app.models.leads import Lead, Task
from app.models.organization import Organization
from app.schemas.common import ErrorResponse, Page
from app.schemas.leads import TaskOut, TaskUpdate
from app.services import lead_service
from app.services.access import visible_user_ids
from app.services.dashboard_service import day_bounds

router = APIRouter(prefix="/tasks", tags=["tasks"], responses={403: {"model": ErrorResponse}})


@router.get("", response_model=Page[TaskOut], dependencies=[RequirePermission("tasks", "view")])
async def list_tasks(
    current: CurrentUserDep,
    session: DbSession,
    view: Literal["overdue", "today", "upcoming", "completed"] = "today",
    mine: bool = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TaskOut]:
    """Tasks by when they're due (in the organisation's timezone). `mine=false` shows your scope."""
    org = await session.get(Organization, current.organization_id)
    now = lead_service.now_utc()
    _, end_of_today = day_bounds(org.timezone if org else "Asia/Dubai", now)

    query = (
        select(Task)
        .join(Lead, Lead.id == Task.lead_id)
        .where(Task.organization_id == current.organization_id, Lead.deleted_at.is_(None))
    )
    if mine:
        query = query.where(Task.assigned_to_user_id == current.user.id)
    else:
        owners = await visible_user_ids(session, current, "tasks")
        if owners is not None:
            query = query.where(Task.assigned_to_user_id.in_(owners))
    if view == "completed":
        query = query.where(
            Task.completed_at.is_not(None), Task.completed_at >= now - timedelta(days=30)
        )
        order = Task.completed_at.desc()
    else:
        query = query.where(Task.completed_at.is_(None))
        if view == "overdue":
            query = query.where(Task.due_at < now)
        elif view == "today":
            query = query.where(Task.due_at >= now, Task.due_at < end_of_today)
        else:
            query = query.where(Task.due_at >= end_of_today)
        order = Task.due_at.asc()

    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (await session.scalars(query.order_by(order).limit(limit).offset(offset))).unique()
    return Page(
        items=[lead_service.to_task(t, now) for t in rows], total=total, limit=limit, offset=offset
    )


@router.patch(
    "/{task_id}",
    response_model=TaskOut,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    dependencies=[RequirePermission("leads.all", "edit")],
)
async def update_task(
    task_id: uuid.UUID, body: TaskUpdate, current: CurrentUserDep, session: DbSession
) -> TaskOut:
    """Complete, reopen, reschedule or edit a task."""
    task = await lead_service.update_task(session, current, task_id, body)
    return lead_service.to_task(task, lead_service.now_utc())
