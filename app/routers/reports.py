"""`/reports`: Phase 4 dashboards (pipeline, productivity), filter choices and CSV exports.

Rights: `dashboard.pipeline` / `dashboard.productivity` · view to read, · export to
download CSV. Every figure is limited to the caller's data scope on that dashboard.
"""

import uuid
from datetime import date, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core.database import DbSession
from app.core.errors import forbidden, invalid
from app.dependencies import CurrentUserDep, RequirePermission
from app.models.organization import Organization
from app.schemas.common import ErrorResponse
from app.schemas.reports import PipelineReport, ProductivityReport, ReportFilterOptions
from app.services import report_service
from app.services.lead_service import now_utc
from app.services.report_service import PIPELINE, PRODUCTIVITY, ReportFilters

router = APIRouter(prefix="/reports", tags=["reports"], responses={403: {"model": ErrorResponse}})
ERRORS = {422: {"model": ErrorResponse}}
DEFAULT_DAYS = 30


async def report_filters(
    current: CurrentUserDep,
    session: DbSession,
    date_from: Annotated[date | None, Query(description="First day, organisation time")] = None,
    date_to: Annotated[date | None, Query(description="Last day (inclusive)")] = None,
    campaign_id: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006 - FastAPI query list
    program_id: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006
    market_id: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006
    team_id: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006
    owner_id: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006
) -> ReportFilters:
    """Dashboard filters; without dates, the last 30 days up to today (organisation time)."""
    org = await session.get(Organization, current.organization_id)
    today = now_utc().astimezone(ZoneInfo(org.timezone if org else "Asia/Dubai")).date()
    end = date_to or today
    start = date_from or end - timedelta(days=DEFAULT_DAYS - 1)
    filters = ReportFilters(
        date_from=start,
        date_to=end,
        campaign_ids=campaign_id,
        program_ids=program_id,
        market_ids=market_id,
        team_ids=team_id,
        owner_ids=owner_id,
    )
    problem = report_service.check_range(filters)
    if problem:
        raise invalid("invalid_period", problem)
    return filters


Filters = Annotated[ReportFilters, Depends(report_filters)]


def _csv(text: str, name: str, filters: ReportFilters) -> Response:
    filename = f"teg-{name}-{filters.date_from}-to-{filters.date_to}.csv"
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/filters", response_model=ReportFilterOptions)
async def filter_options(
    current: CurrentUserDep,
    session: DbSession,
    dashboard: Literal["pipeline", "productivity"] = "pipeline",
) -> ReportFilterOptions:
    """Choices for the dashboard filters, limited to what you may see."""
    menu = PIPELINE if dashboard == "pipeline" else PRODUCTIVITY
    if not current.can(menu, "view"):
        raise forbidden()
    return await report_service.filter_options(session, current, menu)


@router.get(
    "/pipeline",
    response_model=PipelineReport,
    responses=ERRORS,
    dependencies=[RequirePermission(PIPELINE, "view")],
)
async def pipeline(current: CurrentUserDep, session: DbSession, filters: Filters) -> PipelineReport:
    """Overview dashboard: what happened to the leads created in the period."""
    return await report_service.pipeline(session, current, filters)


@router.get(
    "/pipeline/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}, **ERRORS},
    dependencies=[RequirePermission(PIPELINE, "export")],
)
async def export_pipeline(
    current: CurrentUserDep, session: DbSession, filters: Filters
) -> Response:
    """The agent leaderboard as CSV (opens in Excel)."""
    report = await report_service.pipeline(session, current, filters)
    return _csv(report_service.leaderboard_csv(report), "leaderboard", filters)


@router.get(
    "/productivity",
    response_model=ProductivityReport,
    responses=ERRORS,
    dependencies=[RequirePermission(PRODUCTIVITY, "view")],
)
async def productivity(
    current: CurrentUserDep, session: DbSession, filters: Filters
) -> ProductivityReport:
    """Productivity dashboard: calls, meetings, messages, first contacts and tasks."""
    return await report_service.productivity(session, current, filters)


@router.get(
    "/productivity/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}, **ERRORS},
    dependencies=[RequirePermission(PRODUCTIVITY, "export")],
)
async def export_productivity(
    current: CurrentUserDep, session: DbSession, filters: Filters
) -> Response:
    """The per-person productivity table as CSV (opens in Excel)."""
    report = await report_service.productivity(session, current, filters)
    return _csv(report_service.productivity_csv(report), "productivity", filters)
