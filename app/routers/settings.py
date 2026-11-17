"""`/settings`: organisation profile and customizations.

Rights: `settings.organization` for the profile, `settings.customizations` for
changing statuses, labels and programmes. Reading the lookup lists only needs a
signed-in user, because every screen uses them (lead table, filters, forms).
"""

import uuid

from fastapi import APIRouter, Request, Response, status

from app.core.database import DbSession
from app.dependencies import CurrentUserDep, RequirePermission
from app.schemas.common import ErrorResponse
from app.schemas.settings import (
    LabelIn,
    LabelOut,
    LeadStatusCreate,
    LeadStatusOut,
    LeadStatusUpdate,
    OrganizationOut,
    OrganizationRules,
    OrganizationRulesUpdate,
    OrganizationUpdate,
    ProgramCreate,
    ProgramOut,
    ProgramUpdate,
    ReorderRequest,
)
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"], responses={403: {"model": ErrorResponse}})
ERRORS = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
CUSTOMIZE = "settings.customizations"


# Organisation ---------------------------------------------------------------------------


@router.get(
    "/organization",
    response_model=OrganizationOut,
    dependencies=[RequirePermission("settings.organization", "view")],
)
async def get_organization(current: CurrentUserDep, session: DbSession) -> OrganizationOut:
    """Organisation profile and rules."""
    return OrganizationOut.model_validate(await settings_service.get_organization(session, current))


@router.patch(
    "/organization",
    response_model=OrganizationOut,
    responses=ERRORS,
    dependencies=[RequirePermission("settings.organization", "edit")],
)
async def update_organization(
    body: OrganizationUpdate, request: Request, current: CurrentUserDep, session: DbSession
) -> OrganizationOut:
    """Edit name, logo, phone, timezone or currency."""
    org = await settings_service.update_organization(session, request, current, body)
    return OrganizationOut.model_validate(org)


# Organisation rules ----------------------------------------------------------------------


@router.get("/rules", response_model=OrganizationRules)
async def get_rules(current: CurrentUserDep, session: DbSession) -> OrganizationRules:
    """Hide-history switch, deal value field and MQL statuses (read by every screen)."""
    return settings_service.read_rules(await settings_service.get_organization(session, current))


@router.patch("/rules", response_model=OrganizationRules, responses=ERRORS)
async def update_rules(
    body: OrganizationRulesUpdate, request: Request, current: CurrentUserDep, session: DbSession
) -> OrganizationRules:
    """Change switches (hide-history: Roles & Permissions edit; others: Customizations edit)."""
    return await settings_service.update_rules(session, request, current, body)


# Lead statuses ---------------------------------------------------------------------------


@router.get("/lead-statuses", response_model=list[LeadStatusOut])
async def list_statuses(current: CurrentUserDep, session: DbSession) -> list[LeadStatusOut]:
    """Pipeline statuses in order."""
    return [
        LeadStatusOut.model_validate(s)
        for s in await settings_service.list_statuses(session, current)
    ]


@router.post(
    "/lead-statuses",
    response_model=LeadStatusOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    dependencies=[RequirePermission(CUSTOMIZE, "create")],
)
async def create_status(
    body: LeadStatusCreate, request: Request, current: CurrentUserDep, session: DbSession
) -> LeadStatusOut:
    """Add a custom status at the end of the pipeline."""
    return LeadStatusOut.model_validate(
        await settings_service.create_status(session, request, current, body)
    )


@router.put(
    "/lead-statuses/order",
    response_model=list[LeadStatusOut],
    responses=ERRORS,
    dependencies=[RequirePermission(CUSTOMIZE, "edit")],
)
async def reorder_statuses(
    body: ReorderRequest, request: Request, current: CurrentUserDep, session: DbSession
) -> list[LeadStatusOut]:
    """Save a new drag-and-drop order (send every status id once)."""
    statuses = await settings_service.reorder_statuses(session, request, current, body.ids)
    return [LeadStatusOut.model_validate(s) for s in statuses]


@router.patch(
    "/lead-statuses/{status_id}",
    response_model=LeadStatusOut,
    responses=ERRORS,
    dependencies=[RequirePermission(CUSTOMIZE, "edit")],
)
async def update_status(
    status_id: uuid.UUID,
    body: LeadStatusUpdate,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> LeadStatusOut:
    """Rename, recolour, change flags, or (custom statuses only) deactivate."""
    status_row = await settings_service.update_status(session, request, current, status_id, body)
    return LeadStatusOut.model_validate(status_row)


# Labels ---------------------------------------------------------------------------------


@router.get("/labels", response_model=list[LabelOut])
async def list_labels(current: CurrentUserDep, session: DbSession) -> list[LabelOut]:
    """Lead labels."""
    return [
        LabelOut.model_validate(label)
        for label in await settings_service.list_labels(session, current)
    ]


@router.post(
    "/labels",
    response_model=LabelOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    dependencies=[RequirePermission(CUSTOMIZE, "create")],
)
async def create_label(
    body: LabelIn, request: Request, current: CurrentUserDep, session: DbSession
) -> LabelOut:
    """Add a label."""
    return LabelOut.model_validate(
        await settings_service.save_label(session, request, current, body)
    )


@router.patch(
    "/labels/{label_id}",
    response_model=LabelOut,
    responses=ERRORS,
    dependencies=[RequirePermission(CUSTOMIZE, "edit")],
)
async def update_label(
    label_id: uuid.UUID,
    body: LabelIn,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> LabelOut:
    """Rename or recolour a label."""
    label = await settings_service.save_label(session, request, current, body, label_id)
    return LabelOut.model_validate(label)


@router.delete(
    "/labels/{label_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERRORS,
    dependencies=[RequirePermission(CUSTOMIZE, "delete")],
)
async def delete_label(
    label_id: uuid.UUID, request: Request, current: CurrentUserDep, session: DbSession
) -> Response:
    """Delete a label; it is removed from every lead."""
    await settings_service.delete_label(session, request, current, label_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Programmes -----------------------------------------------------------------------------


@router.get("/programs", response_model=list[ProgramOut])
async def list_programs(current: CurrentUserDep, session: DbSession) -> list[ProgramOut]:
    """Immigration programmes."""
    return [
        ProgramOut.model_validate(p) for p in await settings_service.list_programs(session, current)
    ]


@router.post(
    "/programs",
    response_model=ProgramOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    dependencies=[RequirePermission(CUSTOMIZE, "create")],
)
async def create_program(
    body: ProgramCreate, request: Request, current: CurrentUserDep, session: DbSession
) -> ProgramOut:
    """Add a programme."""
    return ProgramOut.model_validate(
        await settings_service.create_program(session, request, current, body)
    )


@router.patch(
    "/programs/{program_id}",
    response_model=ProgramOut,
    responses=ERRORS,
    dependencies=[RequirePermission(CUSTOMIZE, "edit")],
)
async def update_program(
    program_id: uuid.UUID,
    body: ProgramUpdate,
    request: Request,
    current: CurrentUserDep,
    session: DbSession,
) -> ProgramOut:
    """Rename, re-country or (de)activate a programme."""
    program = await settings_service.update_program(session, request, current, program_id, body)
    return ProgramOut.model_validate(program)
