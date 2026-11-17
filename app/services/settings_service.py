"""Organisation profile and customizations (statuses, labels, programmes)."""

import uuid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import conflict, forbidden, invalid, not_found
from app.models.configuration import Label, LeadStatus, Program
from app.models.organization import Organization
from app.repositories.audit_repository import add_audit_log, diff
from app.schemas.settings import (
    LabelIn,
    LeadStatusCreate,
    LeadStatusUpdate,
    OrganizationRules,
    OrganizationRulesUpdate,
    OrganizationUpdate,
    ProgramCreate,
    ProgramUpdate,
)
from app.services.access import CurrentUser


async def _flush(session: AsyncSession, duplicate_message: str) -> None:
    """Flush to get server-generated ids, turning a unique-constraint hit into 409."""
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise conflict("duplicate", duplicate_message) from exc


def _fields(entity: object, names: list[str]) -> dict[str, Any]:
    return {name: getattr(entity, name) for name in names}


async def _save(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    changes: dict[str, Any],
    duplicate_message: str,
) -> None:
    if not changes:
        return
    add_audit_log(
        session,
        organization_id=current.organization_id,
        actor_user_id=current.user.id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        changes=changes,
        request=request,
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise conflict("duplicate", duplicate_message) from exc


# --- Organisation -------------------------------------------------------------------


async def get_organization(session: AsyncSession, current: CurrentUser) -> Organization:
    """The caller's organisation."""
    org = await session.get(Organization, current.organization_id)
    if org is None:
        raise not_found("Organisation")
    return org


async def update_organization(
    session: AsyncSession, request: Request, current: CurrentUser, data: OrganizationUpdate
) -> Organization:
    """Edit the organisation profile."""
    org = await get_organization(session, current)
    names = ["name", "logo_url", "phone", "timezone", "default_currency"]
    before = _fields(org, names)
    updates = data.model_dump(exclude_unset=True)
    if updates.get("timezone"):
        try:
            ZoneInfo(updates["timezone"])
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise invalid(
                "invalid_timezone", "Unknown timezone. Use a name like Asia/Dubai."
            ) from exc
    for field, value in updates.items():
        if field in ("name", "timezone", "default_currency") and value is None:
            raise invalid("required", f"{field.replace('_', ' ').capitalize()} can't be empty.")
        setattr(org, field, value)
    await _save(
        session,
        request,
        current,
        entity_type="organization",
        entity_id=org.id,
        action="update",
        changes=diff(before, _fields(org, names)),
        duplicate_message="Conflicting organisation details.",
    )
    await session.refresh(org)
    return org


# --- Organisation rules (settings JSON) -------------------------------------------------

_RULE_RIGHTS = {
    "hide_history_on_reassign": "settings.roles",
    "deal_value": "settings.customizations",
    "mql_status_references": "settings.customizations",
}


def read_rules(org: Organization) -> OrganizationRules:
    """Rules from ``organizations.settings``, with defaults for anything unset."""
    stored = org.settings or {}
    return OrganizationRules.model_validate(
        {key: stored[key] for key in OrganizationRules.model_fields if key in stored}
    )


async def update_rules(
    session: AsyncSession, request: Request, current: CurrentUser, data: OrganizationRulesUpdate
) -> OrganizationRules:
    """Change organisation-wide switches; each needs edit rights on its own screen."""
    org = await get_organization(session, current)
    updates = data.model_dump(exclude_unset=True, exclude_none=True)
    for key in updates:
        if not current.can(_RULE_RIGHTS[key], "edit"):
            raise forbidden()
    if "mql_status_references" in updates:
        known = {s.reference for s in await list_statuses(session, current)}
        unknown = set(updates["mql_status_references"]) - known
        if unknown:
            raise invalid("unknown_status", f"Unknown status: {', '.join(sorted(unknown))}.")

    before = read_rules(org).model_dump()
    settings = dict(org.settings or {})
    settings.update(updates)
    org.settings = settings  # reassign so SQLAlchemy sees the JSON change
    after = read_rules(org).model_dump()
    await _save(
        session,
        request,
        current,
        entity_type="organization",
        entity_id=org.id,
        action="rules_update",
        changes=diff(before, after),
        duplicate_message="Couldn't save the settings.",
    )
    return read_rules(org)


# --- Lead statuses ------------------------------------------------------------------

_STATUS_FIELDS = ["name", "color", "is_contacted", "is_mql", "is_won", "is_terminal", "is_active"]


async def list_statuses(session: AsyncSession, current: CurrentUser) -> list[LeadStatus]:
    """Statuses in pipeline order (inactive ones included, flagged)."""
    return list(
        await session.scalars(
            select(LeadStatus)
            .where(LeadStatus.organization_id == current.organization_id)
            .order_by(LeadStatus.sort_order)
        )
    )


async def _get_status(
    session: AsyncSession, current: CurrentUser, status_id: uuid.UUID
) -> LeadStatus:
    status = await session.get(LeadStatus, status_id)
    if status is None or status.organization_id != current.organization_id:
        raise not_found("Status")
    return status


async def create_status(
    session: AsyncSession, request: Request, current: CurrentUser, data: LeadStatusCreate
) -> LeadStatus:
    """Add a custom status at the end of the pipeline."""
    last = await session.scalar(
        select(func.max(LeadStatus.sort_order)).where(
            LeadStatus.organization_id == current.organization_id
        )
    )
    status = LeadStatus(
        organization_id=current.organization_id,
        sort_order=(last or 0) + 1,
        is_system=False,
        **data.model_dump(),
    )
    session.add(status)
    await _flush(session, "A status with that reference or name already exists.")
    await _save(
        session,
        request,
        current,
        entity_type="lead_status",
        entity_id=status.id,
        action="create",
        changes=diff({}, data.model_dump()),
        duplicate_message="A status with that reference already exists.",
    )
    return status


async def update_status(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    status_id: uuid.UUID,
    data: LeadStatusUpdate,
) -> LeadStatus:
    """Rename, recolour or change the flags of a status."""
    status = await _get_status(session, current, status_id)
    before = _fields(status, _STATUS_FIELDS)
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise invalid("required", f"{field} can't be empty.")
        if status.is_system and field == "is_active" and value is False:
            raise conflict("system_status", "Built-in statuses can't be deactivated.")
        setattr(status, field, value)
    await _save(
        session,
        request,
        current,
        entity_type="lead_status",
        entity_id=status.id,
        action="update",
        changes=diff(before, _fields(status, _STATUS_FIELDS)),
        duplicate_message="A status with that name already exists.",
    )
    return status


async def reorder_statuses(
    session: AsyncSession, request: Request, current: CurrentUser, ids: list[uuid.UUID]
) -> list[LeadStatus]:
    """Set the pipeline order (drag-to-reorder). Every status id must be included once."""
    statuses = await list_statuses(session, current)
    if sorted(ids) != sorted(s.id for s in statuses):
        raise invalid("incomplete_order", "Send every status exactly once.")
    before = [s.reference for s in statuses]
    by_id = {s.id: s for s in statuses}
    for position, status_id in enumerate(ids, start=1):
        by_id[status_id].sort_order = position
    after = [by_id[i].reference for i in ids]
    await _save(
        session,
        request,
        current,
        entity_type="lead_status",
        entity_id="order",
        action="reorder",
        changes={"order": [before, after]} if before != after else {},
        duplicate_message="Couldn't save the new order.",
    )
    return await list_statuses(session, current)


# --- Labels -------------------------------------------------------------------------


async def list_labels(session: AsyncSession, current: CurrentUser) -> list[Label]:
    """Labels, alphabetical."""
    return list(
        await session.scalars(
            select(Label)
            .where(Label.organization_id == current.organization_id)
            .order_by(Label.name)
        )
    )


async def _get_label(session: AsyncSession, current: CurrentUser, label_id: uuid.UUID) -> Label:
    label = await session.get(Label, label_id)
    if label is None or label.organization_id != current.organization_id:
        raise not_found("Label")
    return label


async def save_label(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    data: LabelIn,
    label_id: uuid.UUID | None = None,
) -> Label:
    """Create a label, or update one when ``label_id`` is given."""
    if label_id is None:
        label = Label(
            organization_id=current.organization_id, name=data.name.strip(), color=data.color
        )
        session.add(label)
        await _flush(session, "A label with that name already exists.")
        before: dict[str, Any] = {}
        action = "create"
    else:
        label = await _get_label(session, current, label_id)
        before = _fields(label, ["name", "color"])
        label.name, label.color, action = data.name.strip(), data.color, "update"
    await _save(
        session,
        request,
        current,
        entity_type="label",
        entity_id=label.id,
        action=action,
        changes=diff(before, _fields(label, ["name", "color"])),
        duplicate_message="A label with that name already exists.",
    )
    return label


async def delete_label(
    session: AsyncSession, request: Request, current: CurrentUser, label_id: uuid.UUID
) -> None:
    """Delete a label (it is removed from every lead)."""
    label = await _get_label(session, current, label_id)
    changes = diff(_fields(label, ["name", "color"]), {"name": None, "color": None})
    await session.delete(label)
    await _save(
        session,
        request,
        current,
        entity_type="label",
        entity_id=label.id,
        action="delete",
        changes=changes,
        duplicate_message="Couldn't delete the label.",
    )


# --- Programmes ---------------------------------------------------------------------

_PROGRAM_FIELDS = ["name", "country", "is_active"]


async def list_programs(session: AsyncSession, current: CurrentUser) -> list[Program]:
    """Programmes, alphabetical by name."""
    return list(
        await session.scalars(
            select(Program)
            .where(Program.organization_id == current.organization_id)
            .order_by(Program.name)
        )
    )


async def create_program(
    session: AsyncSession, request: Request, current: CurrentUser, data: ProgramCreate
) -> Program:
    """Add a programme."""
    program = Program(organization_id=current.organization_id, **data.model_dump())
    session.add(program)
    await _flush(session, "A programme with that code already exists.")
    await _save(
        session,
        request,
        current,
        entity_type="program",
        entity_id=program.id,
        action="create",
        changes=diff({}, data.model_dump()),
        duplicate_message="A programme with that code already exists.",
    )
    return program


async def update_program(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    program_id: uuid.UUID,
    data: ProgramUpdate,
) -> Program:
    """Rename, re-country or (de)activate a programme."""
    program = await session.get(Program, program_id)
    if program is None or program.organization_id != current.organization_id:
        raise not_found("Programme")
    before = _fields(program, _PROGRAM_FIELDS)
    for field, value in data.model_dump(exclude_unset=True).items():
        if field in ("name", "is_active") and value is None:
            raise invalid("required", f"{field} can't be empty.")
        setattr(program, field, value)
    await _save(
        session,
        request,
        current,
        entity_type="program",
        entity_id=program.id,
        action="update",
        changes=diff(before, _fields(program, _PROGRAM_FIELDS)),
        duplicate_message="Conflicting programme details.",
    )
    return program
