"""`/dashboard`: Overview cards, follow-ups and recent activity (menu `dashboard.pipeline`)."""

from fastapi import APIRouter

from app.core.database import DbSession
from app.dependencies import CurrentUserDep, RequirePermission
from app.schemas.common import ErrorResponse
from app.schemas.leads import DashboardSummary
from app.services import dashboard_service

router = APIRouter(
    prefix="/dashboard", tags=["dashboard"], responses={403: {"model": ErrorResponse}}
)


@router.get(
    "/summary",
    response_model=DashboardSummary,
    dependencies=[RequirePermission("dashboard.pipeline", "view")],
)
async def get_summary(current: CurrentUserDep, session: DbSession) -> DashboardSummary:
    """Live numbers for today (organisation timezone) in your data scope."""
    return await dashboard_service.summary(session, current)
