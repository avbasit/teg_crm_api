"""`/imports`: CSV imports into a campaign (menu `leads.import`: view, create).

The browser parses the file, maps columns and sends rows in batches of up to 250:
``POST /imports`` → ``POST /imports/{id}/rows`` (repeat) → ``POST /imports/{id}/complete``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from app.core.database import DbSession
from app.dependencies import CurrentUserDep, RequirePermission
from app.schemas.campaigns import (
    ImportBatch,
    ImportBatchResult,
    ImportCreate,
    ImportOut,
    ImportRowOut,
)
from app.schemas.common import ErrorResponse, Page
from app.services import import_service

router = APIRouter(prefix="/imports", tags=["imports"], responses={403: {"model": ErrorResponse}})
ERRORS = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
VIEW = RequirePermission("leads.import", "view")
CREATE = RequirePermission("leads.import", "create")


@router.get("", response_model=Page[ImportOut], dependencies=[VIEW])
async def list_imports(
    current: CurrentUserDep,
    session: DbSession,
    campaign_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ImportOut]:
    """Import history, newest first."""
    items, total = await import_service.list_imports(session, current, campaign_id, limit, offset)
    return Page(
        items=[import_service.to_out(i) for i in items], total=total, limit=limit, offset=offset
    )


@router.post(
    "",
    response_model=ImportOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    dependencies=[CREATE],
)
async def start_import(
    body: ImportCreate, request: Request, current: CurrentUserDep, session: DbSession
) -> ImportOut:
    """Start an import into a campaign."""
    return import_service.to_out(await import_service.start(session, request, current, body))


@router.get("/{import_id}", response_model=ImportOut, responses=ERRORS, dependencies=[VIEW])
async def get_import(
    import_id: uuid.UUID, current: CurrentUserDep, session: DbSession
) -> ImportOut:
    """One import with its counts."""
    return import_service.to_out(await import_service.get_import(session, current, import_id))


@router.post(
    "/{import_id}/rows", response_model=ImportBatchResult, responses=ERRORS, dependencies=[CREATE]
)
async def add_rows(
    import_id: uuid.UUID, body: ImportBatch, current: CurrentUserDep, session: DbSession
) -> ImportBatchResult:
    """Import a batch of mapped rows."""
    return await import_service.add_rows(session, current, import_id, body.rows)


@router.post(
    "/{import_id}/complete", response_model=ImportOut, responses=ERRORS, dependencies=[CREATE]
)
async def complete_import(
    import_id: uuid.UUID, request: Request, current: CurrentUserDep, session: DbSession
) -> ImportOut:
    """Finish the import (after the last batch, or to stop early)."""
    return import_service.to_out(await import_service.finish(session, request, current, import_id))


@router.get(
    "/{import_id}/rows", response_model=Page[ImportRowOut], responses=ERRORS, dependencies=[VIEW]
)
async def list_rows(
    import_id: uuid.UUID,
    current: CurrentUserDep,
    session: DbSession,
    problems_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ImportRowOut]:
    """Row log: by default only rows that were skipped or failed, with the reason."""
    rows, total = await import_service.list_rows(
        session, current, import_id, problems_only, limit, offset
    )
    return Page(items=rows, total=total, limit=limit, offset=offset)
