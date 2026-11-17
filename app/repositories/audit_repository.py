"""Writes to the append-only ``audit_logs`` and ``auth_events`` tables."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import client_ip, user_agent
from app.models.logs import AuditLog, AuthEvent


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID | datetime):
        return str(value)
    return value


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[Any]]:
    """Return ``{"field": [old, new]}`` for every field whose value changed."""
    return {
        key: [_jsonable(before.get(key)), _jsonable(value)]
        for key, value in after.items()
        if before.get(key) != value
    }


def add_audit_log(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    entity_type: str,
    entity_id: uuid.UUID | str,
    action: str,
    changes: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """Stage one audit entry in the current transaction (committed with the change)."""
    session.add(
        AuditLog(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action,
            changes=changes or None,
            ip_address=client_ip(request) if request else None,
            user_agent=user_agent(request) if request else None,
            request_id=request.headers.get("x-vercel-id") if request else None,
        )
    )


def add_auth_event(
    session: AsyncSession,
    *,
    event_type: str,
    request: Request,
    organization_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    username_attempted: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Stage one security event (login, logout, lockout, password change...)."""
    session.add(
        AuthEvent(
            organization_id=organization_id,
            user_id=user_id,
            username_attempted=username_attempted,
            event_type=event_type,
            ip_address=client_ip(request),
            user_agent=user_agent(request),
            meta=meta or {},
        )
    )


async def list_audit_logs(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_type: str | None,
    entity_id: str | None,
    actor_user_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[AuditLog], int]:
    """Newest-first page of audit entries plus the total count."""
    query: Select[tuple[AuditLog]] = select(AuditLog).where(
        AuditLog.organization_id == organization_id
    )
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    if actor_user_id:
        query = query.where(AuditLog.actor_user_id == actor_user_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.scalars(
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
    )
    return list(rows), total
