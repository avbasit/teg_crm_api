"""`/leads`: lead table, lead drawer, quick actions, tasks on a lead, bulk actions.

Rights (menu `leads.all`): view to read; create to add; edit for details, status,
labels, activities and tasks; assign to reassign; delete to delete; export for CSV.
Every query is limited to the caller's data scope (own / team / reporting line / all).
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select

from app.core.database import DbSession
from app.core.errors import invalid
from app.dependencies import CurrentUserDep, RequirePermission
from app.models.leads import Campaign
from app.repositories import lead_repository
from app.repositories.lead_repository import LeadFilters, SortKey
from app.schemas.common import ErrorResponse, Page
from app.schemas.leads import (
    ActivityCreate,
    ActivityOut,
    BulkLabels,
    BulkReassign,
    BulkRequest,
    BulkResult,
    BulkStatus,
    CampaignOption,
    LabelsSet,
    LeadCreate,
    LeadDetail,
    LeadListItem,
    LeadUpdate,
    OwnerChange,
    OwnerOption,
    StatusChange,
    TaskCreate,
    TaskOut,
)
from app.services import lead_service

router = APIRouter(prefix="/leads", tags=["leads"], responses={403: {"model": ErrorResponse}})
ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
VIEW = RequirePermission("leads.all", "view")
EDIT = RequirePermission("leads.all", "edit")


def lead_filters(
    search: Annotated[str | None, Query(max_length=100)] = None,
    status_id: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006 - FastAPI query list
    label_id: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006
    campaign_id: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006
    owner_id: Annotated[list[str], Query(description="User id or 'unassigned'")] = [],  # noqa: B006
    source: Annotated[list[str], Query()] = [],  # noqa: B006
    task_status: Annotated[list[Literal["none", "due", "overdue"]], Query()] = [],  # noqa: B006
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    updated_before: datetime | None = None,
    calls_min: Annotated[int | None, Query(ge=0)] = None,
    calls_max: Annotated[int | None, Query(ge=0)] = None,
    duplicates: bool | None = None,
    contacted: bool | None = None,
) -> LeadFilters:
    """The lead table's filters (shared by the list and the CSV export)."""
    for value in owner_id:
        if value != lead_repository.UNASSIGNED:
            try:
                uuid.UUID(value)
            except ValueError as exc:
                raise invalid(
                    "invalid_owner", "Agent filter must be a user id or 'unassigned'."
                ) from exc
    return LeadFilters(
        search=search,
        status_ids=status_id,
        label_ids=label_id,
        campaign_ids=campaign_id,
        owner_ids=owner_id,
        sources=source,
        task_status=task_status,
        created_from=created_from,
        created_to=created_to,
        updated_before=updated_before,
        calls_min=calls_min,
        calls_max=calls_max,
        duplicates=duplicates,
        contacted=contacted,
    )


Filters = Annotated[LeadFilters, Depends(lead_filters)]
EXPORT_LIMIT = 50_000


@router.get("", response_model=Page[LeadListItem], dependencies=[VIEW])
async def list_leads(
    current: CurrentUserDep,
    session: DbSession,
    filters: Filters,
    sort: SortKey = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[LeadListItem]:
    """The lead table: filter, search, sort and page through leads in your scope."""
    owners = await lead_service.lead_scope(session, current)
    leads, total = await lead_repository.list_leads(
        session,
        current,
        owners,
        filters,
        sort=sort,
        descending=order == "desc",
        limit=limit,
        offset=offset,
    )
    now = lead_service.now_utc()
    return Page(
        items=[lead_service.to_list_item(lead, now) for lead in leads],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}, 422: {"model": ErrorResponse}},
    dependencies=[RequirePermission("leads.all", "export")],
)
async def export_leads(
    current: CurrentUserDep,
    session: DbSession,
    filters: Filters,
    sort: SortKey = "created_at",
    order: Literal["asc", "desc"] = "desc",
) -> Response:
    """Every lead matching the table's filters as CSV (up to 50,000; opens in Excel)."""
    owners = await lead_service.lead_scope(session, current)
    leads, total = await lead_repository.list_leads(
        session,
        current,
        owners,
        filters,
        sort=sort,
        descending=order == "desc",
        limit=EXPORT_LIMIT,
        offset=0,
    )
    if total > EXPORT_LIMIT:
        raise invalid(
            "export_too_large",
            f"{total:,} leads match. Narrow the filters to {EXPORT_LIMIT:,} or fewer to export.",
        )
    stamp = lead_service.now_utc().strftime("%Y-%m-%d")
    return Response(
        content=lead_service.leads_csv(leads),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="teg-leads-{stamp}.csv"'},
    )


@router.get("/owners", response_model=list[OwnerOption], dependencies=[VIEW])
async def owner_options(current: CurrentUserDep, session: DbSession) -> list[OwnerOption]:
    """People you can filter by or assign leads to (your scope, active and invited)."""
    return [
        OwnerOption(id=u.id, full_name=u.full_name, role=u.role.name, is_assignable=u.is_assignable)
        for u in await lead_service.assignable_owners(session, current)
    ]


@router.get("/campaigns", response_model=list[CampaignOption], dependencies=[VIEW])
async def campaign_options(current: CurrentUserDep, session: DbSession) -> list[CampaignOption]:
    """Campaigns for the filter and the Add-lead form (campaigns are managed in Phase 3)."""
    rows = await session.scalars(
        select(Campaign)
        .where(Campaign.organization_id == current.organization_id, Campaign.deleted_at.is_(None))
        .order_by(Campaign.name)
    )
    return [CampaignOption(id=c.id, name=c.name, status=c.status) for c in rows]


@router.post(
    "",
    response_model=LeadDetail,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    dependencies=[RequirePermission("leads.all", "create")],
)
async def create_lead(
    body: LeadCreate, request: Request, current: CurrentUserDep, session: DbSession
) -> LeadDetail:
    """Add a lead by hand. Starts Not Contacted; linked to the person's earlier enquiries."""
    lead = await lead_service.create_lead(session, request, current, body)
    return await lead_service.to_detail(session, current, lead)


@router.get("/{lead_id}", response_model=LeadDetail, responses=ERRORS, dependencies=[VIEW])
async def get_lead(lead_id: uuid.UUID, current: CurrentUserDep, session: DbSession) -> LeadDetail:
    """Everything shown in the lead drawer."""
    lead = await lead_service.get_lead(session, current, lead_id)
    return await lead_service.to_detail(session, current, lead)


@router.patch("/{lead_id}", response_model=LeadDetail, responses=ERRORS, dependencies=[EDIT])
async def update_lead(
    lead_id: uuid.UUID,
    body: LeadUpdate,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> LeadDetail:
    """Edit details (name, phone, email, notes, qualification fields, deal value)."""
    lead = await lead_service.update_lead(session, request, current, lead_id, body)
    return await lead_service.to_detail(session, current, lead)


@router.delete(
    "/{lead_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERRORS,
    dependencies=[RequirePermission("leads.all", "delete")],
)
async def delete_lead(
    lead_id: uuid.UUID, request: Request, current: CurrentUserDep, session: DbSession
) -> Response:
    """Delete a lead (kept in the database for history)."""
    await lead_service.get_lead(session, current, lead_id)
    await lead_service.delete_leads(session, request, current, [lead_id])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{lead_id}/status", response_model=LeadDetail, responses=ERRORS, dependencies=[EDIT])
async def change_status(
    lead_id: uuid.UUID, body: StatusChange, current: CurrentUserDep, session: DbSession
) -> LeadDetail:
    """Change status. 422 `not_contacted` until a call or meeting has been logged."""
    lead = await lead_service.change_status(session, current, lead_id, body.status_id)
    return await lead_service.to_detail(session, current, lead)


@router.patch(
    "/{lead_id}/owner",
    response_model=LeadDetail,
    responses=ERRORS,
    dependencies=[RequirePermission("leads.all", "assign")],
)
async def change_owner(
    lead_id: uuid.UUID,
    body: OwnerChange,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> LeadDetail:
    """Reassign. Open tasks move with the lead; the full history stays visible."""
    lead = await lead_service.change_owner(session, request, current, lead_id, body.owner_user_id)
    return await lead_service.to_detail(session, current, lead)


@router.put("/{lead_id}/labels", response_model=LeadDetail, responses=ERRORS, dependencies=[EDIT])
async def set_labels(
    lead_id: uuid.UUID, body: LabelsSet, current: CurrentUserDep, session: DbSession
) -> LeadDetail:
    """Replace the lead's labels."""
    lead = await lead_service.set_labels(session, current, lead_id, body.label_ids)
    return await lead_service.to_detail(session, current, lead)


@router.get(
    "/{lead_id}/activities", response_model=list[ActivityOut], responses=ERRORS, dependencies=[VIEW]
)
async def list_activities(
    lead_id: uuid.UUID, current: CurrentUserDep, session: DbSession
) -> list[ActivityOut]:
    """The lead's full timeline, newest first."""
    return await lead_service.timeline(session, current, lead_id)


@router.post(
    "/{lead_id}/activities",
    response_model=LeadDetail,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    dependencies=[EDIT],
)
async def log_activity(
    lead_id: uuid.UUID, body: ActivityCreate, current: CurrentUserDep, session: DbSession
) -> LeadDetail:
    """Log a call, WhatsApp, text, email, meeting or comment (optionally with a follow-up task)."""
    lead = await lead_service.log_activity(session, current, lead_id, body)
    return await lead_service.to_detail(session, current, lead)


@router.get("/{lead_id}/tasks", response_model=list[TaskOut], responses=ERRORS, dependencies=[VIEW])
async def list_tasks(
    lead_id: uuid.UUID, current: CurrentUserDep, session: DbSession
) -> list[TaskOut]:
    """The lead's tasks: open first, then completed."""
    now = lead_service.now_utc()
    return [
        lead_service.to_task(t, now)
        for t in await lead_service.lead_tasks(session, current, lead_id)
    ]


@router.post(
    "/{lead_id}/tasks",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    dependencies=[EDIT],
)
async def create_task(
    lead_id: uuid.UUID, body: TaskCreate, current: CurrentUserDep, session: DbSession
) -> TaskOut:
    """Add a follow-up (defaults to the lead owner)."""
    task = await lead_service.create_task(session, current, lead_id, body)
    return lead_service.to_task(task, lead_service.now_utc())


# --- Bulk ---------------------------------------------------------------------------------


@router.post(
    "/bulk/reassign",
    response_model=BulkResult,
    responses=ERRORS,
    dependencies=[RequirePermission("leads.all", "assign")],
)
async def bulk_reassign(
    body: BulkReassign, request: Request, current: CurrentUserDep, session: DbSession
) -> BulkResult:
    """Give the selected leads a new owner."""
    return await lead_service.bulk_reassign(
        session, request, current, body.lead_ids, body.owner_user_id
    )


@router.post("/bulk/status", response_model=BulkResult, responses=ERRORS, dependencies=[EDIT])
async def bulk_status(body: BulkStatus, current: CurrentUserDep, session: DbSession) -> BulkResult:
    """Change status on the selected leads; ones not contacted yet are skipped."""
    return await lead_service.bulk_status(session, current, body.lead_ids, body.status_id)


@router.post("/bulk/labels", response_model=BulkResult, responses=ERRORS, dependencies=[EDIT])
async def bulk_labels(body: BulkLabels, current: CurrentUserDep, session: DbSession) -> BulkResult:
    """Add or remove labels on the selected leads."""
    return await lead_service.bulk_labels(
        session, current, body.lead_ids, body.label_ids, body.mode
    )


@router.post(
    "/bulk/delete",
    response_model=BulkResult,
    responses=ERRORS,
    dependencies=[RequirePermission("leads.all", "delete")],
)
async def bulk_delete(
    body: BulkRequest, request: Request, current: CurrentUserDep, session: DbSession
) -> BulkResult:
    """Delete the selected leads (kept for history)."""
    return await lead_service.delete_leads(session, request, current, body.lead_ids)
