"""`/audit-logs`: who changed what (Settings › Audit Log). Rights: `settings.audit_log`."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.core.database import DbSession
from app.dependencies import CurrentUserDep, RequirePermission
from app.models.identity import User
from app.repositories.audit_repository import list_audit_logs
from app.schemas.common import ErrorResponse, Page
from app.schemas.settings import AuditLogOut
from app.schemas.users import UserRef

router = APIRouter(prefix="/audit-logs", tags=["audit"], responses={403: {"model": ErrorResponse}})


@router.get(
    "",
    response_model=Page[AuditLogOut],
    dependencies=[RequirePermission("settings.audit_log", "view")],
)
async def get_audit_logs(
    current: CurrentUserDep,
    session: DbSession,
    entity_type: Annotated[str | None, Query(max_length=40)] = None,
    entity_id: Annotated[str | None, Query(max_length=64)] = None,
    actor_user_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AuditLogOut]:
    """Newest first. Filter by record (`entity_type` + `entity_id`) or by who made the change."""
    logs, total = await list_audit_logs(
        session,
        organization_id=current.organization_id,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        limit=limit,
        offset=offset,
    )
    actor_ids = {log.actor_user_id for log in logs if log.actor_user_id}
    actors = (
        {
            user.id: UserRef.model_validate(user)
            for user in (await session.scalars(select(User).where(User.id.in_(actor_ids)))).unique()
        }
        if actor_ids
        else {}
    )
    items = [
        AuditLogOut(
            id=log.id,
            actor=actors.get(log.actor_user_id) if log.actor_user_id else None,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            action=log.action,
            changes=log.changes,
            ip_address=str(log.ip_address) if log.ip_address else None,
            created_at=log.created_at,
        )
        for log in logs
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)
