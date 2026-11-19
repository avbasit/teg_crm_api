"""CSV imports: the browser reads the file and sends mapped rows in batches.

Each row goes through the same intake pipeline as website leads (duplicate rules,
contact matching, round-robin), and is logged in ``import_rows`` with its lead or
the reason it was skipped or failed.
"""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import conflict, forbidden, invalid, not_found
from app.models.identity import User
from app.models.intake import Import, ImportRow
from app.models.organization import Organization
from app.repositories.audit_repository import add_audit_log
from app.schemas.campaigns import (
    ImportBatchResult,
    ImportCreate,
    ImportOut,
    ImportRowIn,
    ImportRowOut,
    ImportRowResult,
    NamedRef,
)
from app.schemas.users import UserRef
from app.services import campaign_service
from app.services.access import CurrentUser
from app.services.form_answers import parse_notes
from app.services.intake_service import Assignment, Incoming, Ingestor, now_utc

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S %Z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
)


def parse_date(value: str | None, tz: ZoneInfo) -> datetime | None:
    """A date from a CSV cell; times without a zone are read as organisation time."""
    if not value or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for fmt in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                if text.endswith(" UTC"):
                    parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


def to_out(item: Import) -> ImportOut:
    """An import for the API."""
    return ImportOut(
        id=item.id,
        file_name=item.file_name,
        campaign=NamedRef(id=item.campaign.id, name=item.campaign.name) if item.campaign else None,
        status=item.status,
        total_rows=item.total_rows,
        imported_rows=item.imported_rows or 0,
        skipped_duplicate_rows=item.skipped_duplicate_rows or 0,
        failed_rows=item.failed_rows or 0,
        created_by=UserRef.model_validate(item.created_by) if item.created_by else None,
        created_at=item.created_at,
        completed_at=item.completed_at,
    )


async def get_import(session: AsyncSession, current: CurrentUser, import_id: uuid.UUID) -> Import:
    """An import in the caller's organisation (404 otherwise)."""
    item = await session.scalar(
        select(Import)
        .where(Import.id == import_id, Import.organization_id == current.organization_id)
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise not_found("Import")
    return item


async def start(
    session: AsyncSession, request: Request, current: CurrentUser, data: ImportCreate
) -> Import:
    """Open an import for a campaign; rows follow in batches."""
    campaign = await campaign_service.get_campaign(session, current, data.campaign_id)
    if campaign.status == "archived":
        raise invalid("campaign_archived", "Reactivate the campaign before importing into it.")
    if not data.mapping:
        raise invalid("no_mapping", "Map at least one column.")
    fields = set(data.mapping.values())
    if not fields & {"phone", "email"}:
        raise invalid("contact_column_required", "Map a phone or email column.")
    if data.assignment.mode == "user":
        owner = data.assignment.user_id
        if owner is None:
            raise invalid("owner_required", "Choose who receives the imported leads.")
        if owner != current.user.id and not current.can("leads.all", "assign"):
            raise forbidden("You can only import leads for yourself.")
        user = await session.get(User, owner)
        if (
            user is None
            or user.organization_id != current.organization_id
            or user.status not in ("active", "invited")
        ):
            raise invalid("invalid_owner", "That person can't receive leads.")
    item = Import(
        organization_id=current.organization_id,
        campaign_id=campaign.id,
        file_name=data.file_name,
        column_mapping={
            "mapping": data.mapping,
            "parse_notes": data.parse_notes,
            "assignment": data.assignment.model_dump(mode="json"),
        },
        status="running",
        total_rows=data.total_rows,
        imported_rows=0,
        skipped_duplicate_rows=0,
        failed_rows=0,
        created_by_user_id=current.user.id,
    )
    session.add(item)
    await session.flush()
    add_audit_log(
        session,
        organization_id=current.organization_id,
        actor_user_id=current.user.id,
        entity_type="import",
        entity_id=item.id,
        action="start",
        changes={"campaign": [None, campaign.name], "rows": [None, data.total_rows]},
        request=request,
    )
    await session.commit()
    return await get_import(session, current, item.id)


def _incoming(row: ImportRowIn, parse: bool, tz: ZoneInfo) -> Incoming:
    values = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.values.items()}
    answers: dict[str, str] = {}
    notes = values.get("notes") or None
    if parse and notes:
        answers, notes = parse_notes(notes)
    return Incoming(
        first_name=values.get("first_name"),
        last_name=values.get("last_name"),
        full_name=values.get("full_name"),
        phone=values.get("phone"),
        whatsapp=values.get("whatsapp"),
        email=values.get("email"),
        city=values.get("city"),
        country_of_interest=values.get("country_of_interest"),
        budget_text=values.get("budget_text"),
        education_level=values.get("education_level"),
        english_test_status=values.get("english_test_status"),
        age_text=values.get("age_text"),
        notes=notes,
        answers=answers,
        source="csv",
        sub_source=values.get("sub_source"),
        external_lead_id=values.get("external_lead_id"),
        source_created_at=parse_date(values.get("source_created_at"), tz),
    )


async def add_rows(
    session: AsyncSession, current: CurrentUser, import_id: uuid.UUID, rows: list[ImportRowIn]
) -> ImportBatchResult:
    """Import one batch of rows; a row sent twice (retry) isn't imported twice."""
    item = await get_import(session, current, import_id)
    if item.created_by_user_id != current.user.id:
        raise forbidden("Only the person who started this import can add rows.")
    if item.status != "running":
        raise conflict("import_finished", "This import is already finished.")
    org = await session.get(Organization, current.organization_id)
    assert org is not None
    campaign = await campaign_service.get_campaign(session, current, item.campaign_id)  # type: ignore[arg-type]
    options = item.column_mapping or {}
    assignment = Assignment(**(options.get("assignment") or {}))
    if assignment.user_id:
        assignment.user_id = uuid.UUID(str(assignment.user_id))
    ingestor = await Ingestor.create(
        session, org, campaign, channel="import", actor_id=current.user.id, assignment=assignment
    )
    tz = ZoneInfo(org.timezone or "Asia/Dubai")
    seen = set(
        await session.scalars(
            select(ImportRow.row_number).where(
                ImportRow.import_id == item.id,
                ImportRow.row_number.in_([r.row_number for r in rows]),
            )
        )
    )

    results: list[ImportRowResult] = []
    for row in rows:
        if row.row_number in seen:
            results.append(
                ImportRowResult(
                    row_number=row.row_number,
                    status="skipped",
                    message="Already sent.",
                    lead_id=None,
                )
            )
            continue
        seen.add(row.row_number)
        try:
            async with session.begin_nested():
                outcome = await ingestor.ingest(
                    _incoming(row, options.get("parse_notes", True), tz)
                )
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the batch
            outcome = None
            message = f"Couldn't save this row ({type(exc).__name__})."
        else:
            message = outcome.reason
        status = (
            "failed"
            if outcome is None or outcome.status == "failed"
            else "skipped"
            if outcome.status == "skipped"
            else "imported"
        )
        lead_id = outcome.lead.id if outcome and outcome.lead else None
        session.add(
            ImportRow(
                import_id=item.id,
                row_number=row.row_number,
                raw=row.raw or dict(row.values),
                lead_id=lead_id,
                error=None if status == "imported" else message,
            )
        )
        results.append(
            ImportRowResult(
                row_number=row.row_number,
                status=status,  # type: ignore[arg-type]
                message=message if status != "imported" else None,
                lead_id=lead_id,
                duplicate=bool(outcome and outcome.duplicate),
            )
        )

    imported = sum(r.status == "imported" for r in results)
    skipped = sum(r.status == "skipped" and r.message != "Already sent." for r in results)
    failed = sum(r.status == "failed" for r in results)
    item.imported_rows = (item.imported_rows or 0) + imported
    item.skipped_duplicate_rows = (item.skipped_duplicate_rows or 0) + skipped
    item.failed_rows = (item.failed_rows or 0) + failed
    await session.commit()
    return ImportBatchResult(imported=imported, skipped=skipped, failed=failed, rows=results)


async def finish(
    session: AsyncSession, request: Request, current: CurrentUser, import_id: uuid.UUID
) -> Import:
    """Mark the import complete (also used when the user stops it early)."""
    item = await get_import(session, current, import_id)
    if item.created_by_user_id != current.user.id:
        raise forbidden("Only the person who started this import can finish it.")
    if item.status == "running":
        item.status = "completed"
        item.completed_at = now_utc()
        add_audit_log(
            session,
            organization_id=current.organization_id,
            actor_user_id=current.user.id,
            entity_type="import",
            entity_id=item.id,
            action="complete",
            changes={
                "imported": [None, item.imported_rows],
                "skipped": [None, item.skipped_duplicate_rows],
                "failed": [None, item.failed_rows],
            },
            request=request,
        )
        await session.commit()
    return await get_import(session, current, item.id)


async def list_imports(
    session: AsyncSession,
    current: CurrentUser,
    campaign_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[Import], int]:
    """Import history, newest first."""
    query = select(Import).where(Import.organization_id == current.organization_id)
    if campaign_id:
        query = query.where(Import.campaign_id == campaign_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.scalars(
        query.order_by(Import.created_at.desc()).limit(limit).offset(offset)
    )
    return list(rows.unique()), total


async def list_rows(
    session: AsyncSession,
    current: CurrentUser,
    import_id: uuid.UUID,
    problems_only: bool,
    limit: int,
    offset: int,
) -> tuple[list[ImportRowOut], int]:
    """Rows of an import (by default only the skipped and failed ones)."""
    item = await get_import(session, current, import_id)
    query = select(ImportRow).where(ImportRow.import_id == item.id)
    if problems_only:
        query = query.where(ImportRow.error.is_not(None))
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.scalars(query.order_by(ImportRow.row_number).limit(limit).offset(offset))
    return [
        ImportRowOut(row_number=r.row_number, raw=r.raw, lead_id=r.lead_id, error=r.error)
        for r in rows
    ], total
