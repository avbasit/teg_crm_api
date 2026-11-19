"""Lead rules: scope, create/edit, status, owner, labels, activities, tasks, bulk actions.

Decisions (19 Nov 2026), enforced here:
- A lead is *contacted* once a **call or meeting** is logged (any outcome).
- Its **status can't change before first contact**.
- History is **never hidden** when a lead is reassigned.
- Leads are linked to a **contact** (one real person) by phone or email.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import forbidden, invalid, not_found
from app.core.phone import normalise_phone
from app.models.configuration import Label, LeadStatus
from app.models.identity import User
from app.models.leads import Activity, Campaign, Contact, Lead, LeadAssignment, LeadLabel, Task
from app.repositories.audit_repository import add_audit_log, diff
from app.schemas.leads import (
    CALL_OUTCOMES,
    MEETING_OUTCOMES,
    ActivityCreate,
    ActivityOut,
    BulkResult,
    CampaignRef,
    Enquiry,
    LabelRef,
    LeadCreate,
    LeadDetail,
    LeadListItem,
    LeadUpdate,
    StatusRef,
    TaskCreate,
    TaskLead,
    TaskOut,
    TaskUpdate,
)
from app.schemas.users import UserRef
from app.services.access import CurrentUser, visible_user_ids

MENU = "leads.all"
CONTACT_TYPES = {"call", "meeting"}  # decision: only these count as contact
LOGGED_TYPES = {"call", "whatsapp", "text", "email", "meeting", "comment"}
NOT_CONTACTED = invalid(
    "not_contacted", "Log a call or meeting with this lead before changing its status."
)


def now_utc() -> datetime:
    """Current time in UTC (all timestamps are stored in UTC)."""
    return datetime.now(UTC)


# --- Scope and loading --------------------------------------------------------------------


async def lead_scope(session: AsyncSession, current: CurrentUser) -> set[uuid.UUID] | None:
    """Owner ids whose leads the caller may see; ``None`` = all leads."""
    return await visible_user_ids(session, current, MENU)


def scoped(query: Select[Any], current: CurrentUser, owners: set[uuid.UUID] | None) -> Select[Any]:
    """Restrict a lead query to the organisation, non-deleted rows and the caller's scope."""
    query = query.where(Lead.organization_id == current.organization_id, Lead.deleted_at.is_(None))
    if owners is not None:
        query = query.where(Lead.owner_user_id.in_(owners))  # unassigned leads: 'all' scope only
    return query


async def get_lead(session: AsyncSession, current: CurrentUser, lead_id: uuid.UUID) -> Lead:
    """A lead inside the caller's scope, freshly loaded (404 otherwise)."""
    owners = await lead_scope(session, current)
    lead = await session.scalar(
        scoped(select(Lead).where(Lead.id == lead_id), current, owners).execution_options(
            populate_existing=True
        )
    )
    if lead is None:
        raise not_found("Lead")
    return lead


async def _status(session: AsyncSession, current: CurrentUser, status_id: uuid.UUID) -> LeadStatus:
    status = await session.get(LeadStatus, status_id)
    if status is None or status.organization_id != current.organization_id or not status.is_active:
        raise invalid("unknown_status", "That status doesn't exist or is inactive.")
    return status


async def _initial_status(session: AsyncSession, current: CurrentUser) -> LeadStatus:
    status = await session.scalar(
        select(LeadStatus).where(
            LeadStatus.organization_id == current.organization_id,
            LeadStatus.reference == "not_contacted",
        )
    )
    if status is None:  # seeded by migration 0002; guard anyway
        raise invalid("missing_status", "The 'Not Contacted' status is missing.")
    return status


async def assignable_owners(session: AsyncSession, current: CurrentUser) -> list[User]:
    """People the caller may give leads to: active/invited users in their scope."""
    owners = await lead_scope(session, current)
    query = select(User).where(
        User.organization_id == current.organization_id, User.status.in_(("active", "invited"))
    )
    if owners is not None:
        query = query.where(User.id.in_(owners))
    return list((await session.scalars(query.order_by(User.full_name))).unique())


async def _check_owner(
    session: AsyncSession, current: CurrentUser, owner_id: uuid.UUID | None
) -> None:
    if owner_id is None:
        if current.scope(MENU) != "all":
            raise forbidden("Only people who see all leads can leave a lead unassigned.")
        return
    if owner_id not in {u.id for u in await assignable_owners(session, current)}:
        raise invalid("invalid_owner", "You can't assign leads to that person.")


# --- Serialisation ------------------------------------------------------------------------


def task_state(lead: Lead, now: datetime) -> str:
    """Jaldi's Task Status column: none / due / overdue."""
    if lead.next_task_due_at is None:
        return "none"
    return "overdue" if lead.next_task_due_at < now else "due"


def to_list_item(lead: Lead, now: datetime) -> LeadListItem:
    """Table row for a lead."""
    return LeadListItem(
        id=lead.id,
        first_name=lead.first_name,
        last_name=lead.last_name,
        full_name=lead.full_name,
        phone=lead.phone_e164 or lead.phone_raw,
        email=lead.email,
        status=StatusRef.model_validate(lead.status),
        labels=sorted(
            (LabelRef.model_validate(ll.label) for ll in lead.lead_labels),
            key=lambda label: label.name,
        ),
        campaign=CampaignRef.model_validate(lead.campaign) if lead.campaign else None,
        owner=UserRef.model_validate(lead.owner) if lead.owner else None,
        source=lead.source,
        sub_source=lead.sub_source,
        city=lead.city,
        country_of_interest=lead.country_of_interest,
        budget_text=lead.budget_text,
        deal_value=lead.deal_value,
        deal_currency=lead.deal_currency,
        calls_made=lead.calls_made,
        times_assigned=lead.times_assigned,
        is_duplicate=lead.is_duplicate,
        is_contacted=lead.first_contacted_at is not None,
        task_status=task_state(lead, now),  # type: ignore[arg-type]
        next_task_due_at=lead.next_task_due_at,
        last_assigned_at=lead.last_assigned_at,
        last_activity_at=lead.last_activity_at,
        last_activity_note=lead.last_activity_note,
        source_created_at=lead.source_created_at,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


async def to_detail(session: AsyncSession, current: CurrentUser, lead: Lead) -> LeadDetail:
    """Drawer view, including the same person's other enquiries."""
    others: list[Enquiry] = []
    if lead.contact_id:
        owners = await lead_scope(session, current)
        rows = (
            await session.scalars(
                select(Lead)
                .where(
                    Lead.contact_id == lead.contact_id,
                    Lead.id != lead.id,
                    Lead.deleted_at.is_(None),
                )
                .order_by(Lead.created_at.desc())
                .limit(20)
            )
        ).unique()
        others = [
            Enquiry(
                id=other.id,
                campaign=other.campaign.name if other.campaign else None,
                status=other.status.name,
                status_color=other.status.color,
                owner=other.owner.full_name if other.owner else None,
                created_at=other.created_at,
                visible=owners is None or other.owner_user_id in owners,
            )
            for other in rows
        ]
    item = to_list_item(lead, now_utc())
    return LeadDetail(
        **item.model_dump(),
        whatsapp=lead.whatsapp_e164,
        notes=lead.notes,
        form_answers=lead.form_answers or {},
        education_level=lead.education_level,
        english_test_status=lead.english_test_status,
        age_text=lead.age_text,
        has_family=lead.has_family,
        source_campaign_name=lead.source_campaign_name,
        source_adset_name=lead.source_adset_name,
        source_ad_name=lead.source_ad_name,
        source_form_name=lead.source_form_name,
        first_contacted_at=lead.first_contacted_at,
        response_time_seconds=lead.response_time_seconds,
        other_enquiries=others,
    )


def to_activity(activity: Activity, statuses: dict[uuid.UUID, str]) -> ActivityOut:
    """Timeline entry for the API."""
    return ActivityOut(
        id=activity.id,
        lead_id=activity.lead_id,
        type=activity.type,
        outcome=activity.outcome,
        notes=activity.notes,
        location=activity.location,
        user=UserRef.model_validate(activity.user) if activity.user else None,
        from_status=statuses.get(activity.from_status_id) if activity.from_status_id else None,
        to_status=statuses.get(activity.to_status_id) if activity.to_status_id else None,
        meta=activity.meta or {},
        occurred_at=activity.occurred_at,
    )


def to_task(task: Task, now: datetime) -> TaskOut:
    """Task for the API."""
    return TaskOut(
        id=task.id,
        type=task.type,
        notes=task.notes,
        due_at=task.due_at,
        completed_at=task.completed_at,
        is_overdue=task.completed_at is None and task.due_at < now,
        lead=TaskLead(
            id=task.lead.id,
            full_name=task.lead.full_name,
            phone=task.lead.phone_e164 or task.lead.phone_raw,
            status=StatusRef.model_validate(task.lead.status),
        ),
        assignee=UserRef.model_validate(task.assignee) if task.assignee else None,
    )


async def status_names(session: AsyncSession, current: CurrentUser) -> dict[uuid.UUID, str]:
    """Status id → name, for timeline entries."""
    rows = await session.execute(
        select(LeadStatus.id, LeadStatus.name).where(
            LeadStatus.organization_id == current.organization_id
        )
    )
    return dict(rows.all())


# --- Helpers ------------------------------------------------------------------------------


def _add_activity(
    session: AsyncSession,
    current: CurrentUser | None,
    lead: Lead,
    type_: str,
    *,
    when: datetime,
    **fields: Any,
) -> None:
    session.add(
        Activity(
            organization_id=lead.organization_id,
            lead_id=lead.id,
            user_id=current.user.id if current else None,
            type=type_,
            occurred_at=when,
            **fields,
        )
    )


async def _refresh_next_task(session: AsyncSession, lead_id: uuid.UUID) -> None:
    """Keep ``leads.next_task_due_at`` = earliest open task (drives Task Status)."""
    await session.flush()
    earliest = await session.scalar(
        select(func.min(Task.due_at)).where(Task.lead_id == lead_id, Task.completed_at.is_(None))
    )
    await session.execute(update(Lead).where(Lead.id == lead_id).values(next_task_due_at=earliest))


async def _link_contact(session: AsyncSession, lead: Lead) -> None:
    """Attach the lead to its person (by phone, then email); flag repeat enquiries."""
    conditions = []
    if lead.phone_e164:
        conditions.append(Contact.phone_e164 == lead.phone_e164)
    if lead.email:
        conditions.append(Contact.email == lead.email)
    contact = None
    if conditions:
        contact = await session.scalar(
            select(Contact)
            .where(Contact.organization_id == lead.organization_id, or_(*conditions))
            .order_by(Contact.created_at)
            .limit(1)
        )
    if contact is None:
        contact = Contact(
            organization_id=lead.organization_id,
            full_name=f"{lead.first_name} {lead.last_name or ''}".strip(),
            phone_e164=lead.phone_e164,
            email=lead.email,
        )
        session.add(contact)
        await session.flush()
    else:
        first = await session.scalar(
            select(Lead.id)
            .where(Lead.contact_id == contact.id, Lead.deleted_at.is_(None))
            .order_by(Lead.created_at)
            .limit(1)
        )
        if first is not None:
            lead.is_duplicate = True
            lead.duplicate_of_lead_id = first
    lead.contact_id = contact.id


def _set_phone(lead: Lead, value: str | None) -> None:
    phone = normalise_phone(value)
    lead.phone_raw = phone.raw if phone else None
    lead.phone_e164 = phone.e164 if phone else None
    lead.phone_country_code = phone.country_code if phone else None


# --- Create / edit / delete ---------------------------------------------------------------


async def create_lead(
    session: AsyncSession, request: Request, current: CurrentUser, data: LeadCreate
) -> Lead:
    """Add a lead by hand. It starts Not Contacted, owned by you unless you can assign."""
    if not data.phone and not data.email:
        raise invalid("contact_required", "Enter a phone number or an email address.")
    owner_id = data.owner_user_id or current.user.id
    if owner_id != current.user.id and not current.can(MENU, "assign"):
        raise forbidden("You can only add leads for yourself.")
    await _check_owner(session, current, owner_id)
    if data.campaign_id:
        campaign = await session.get(Campaign, data.campaign_id)
        if campaign is None or campaign.organization_id != current.organization_id:
            raise invalid("unknown_campaign", "That campaign doesn't exist.")

    now = now_utc()
    lead = Lead(
        organization_id=current.organization_id,
        owner_user_id=owner_id,
        campaign_id=data.campaign_id,
        status_id=(await _initial_status(session, current)).id,
        first_name=data.first_name.strip(),
        last_name=data.last_name,
        email=data.email.lower() if data.email else None,
        notes=data.notes,
        source="manual",
        times_assigned=1,
        last_assigned_at=now,
        form_answers={},
    )
    _set_phone(lead, data.phone)
    await _link_contact(session, lead)
    session.add(lead)
    await session.flush()
    _add_activity(session, current, lead, "lead_added", when=now, meta={"source": "manual"})
    _add_activity(session, None, lead, "assigned", when=now, meta={"to": str(owner_id)})
    session.add(
        LeadAssignment(
            lead_id=lead.id,
            to_user_id=owner_id,
            assigned_by_user_id=current.user.id,
            reason="manual",
        )
    )
    await session.commit()
    return await get_lead(session, current, lead.id)


_EDITABLE = [
    "first_name",
    "last_name",
    "email",
    "notes",
    "city",
    "country_of_interest",
    "budget_text",
    "deal_value",
    "phone_raw",
    "whatsapp_e164",
]


async def update_lead(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    lead_id: uuid.UUID,
    data: LeadUpdate,
) -> Lead:
    """Edit lead details. Each change is logged on the timeline as an edit."""
    lead = await get_lead(session, current, lead_id)
    before = {field: getattr(lead, field) for field in _EDITABLE}
    changes = data.model_dump(exclude_unset=True)
    if "first_name" in changes and not (changes["first_name"] or "").strip():
        raise invalid("first_name_required", "First name can't be empty.")
    for field, value in changes.items():
        if field == "phone":
            _set_phone(lead, value)
        elif field == "whatsapp":
            phone = normalise_phone(value)
            lead.whatsapp_e164 = (phone.e164 or phone.raw) if phone else None
        elif field == "email":
            lead.email = value.lower() if value else None
        else:
            setattr(lead, field, value.strip() if isinstance(value, str) else value)
    if not (lead.phone_raw or lead.email):
        raise invalid("contact_required", "Keep a phone number or an email address.")
    after = {field: getattr(lead, field) for field in _EDITABLE}
    changed = diff(before, after)
    if changed:
        _add_activity(session, current, lead, "edit", when=now_utc(), meta={"changes": changed})
        await session.commit()
    return await get_lead(session, current, lead.id)


async def delete_leads(
    session: AsyncSession, request: Request, current: CurrentUser, lead_ids: list[uuid.UUID]
) -> BulkResult:
    """Soft-delete leads in scope (kept for history and audit)."""
    owners = await lead_scope(session, current)
    ids = list(
        await session.scalars(scoped(select(Lead.id).where(Lead.id.in_(lead_ids)), current, owners))
    )
    if ids:
        await session.execute(update(Lead).where(Lead.id.in_(ids)).values(deleted_at=now_utc()))
        add_audit_log(
            session,
            organization_id=current.organization_id,
            actor_user_id=current.user.id,
            entity_type="lead",
            entity_id=ids[0] if len(ids) == 1 else f"{len(ids)} leads",
            action="delete",
            changes={"lead_ids": [str(i) for i in ids]},
            request=request,
        )
        await session.commit()
    return _result(len(ids), len(lead_ids) - len(ids), "Not found or outside your scope.")


def _result(updated: int, skipped: int, reason: str) -> BulkResult:
    return BulkResult(updated=updated, skipped=skipped, skipped_reason=reason if skipped else None)


# --- Status / owner / labels --------------------------------------------------------------


async def change_status(
    session: AsyncSession, current: CurrentUser, lead_id: uuid.UUID, status_id: uuid.UUID
) -> Lead:
    """Move a contacted lead to another status (blocked before first contact)."""
    lead = await get_lead(session, current, lead_id)
    status = await _status(session, current, status_id)
    if lead.first_contacted_at is None:
        raise NOT_CONTACTED
    if status.id != lead.status_id:
        _add_activity(
            session,
            current,
            lead,
            "status_change",
            when=now_utc(),
            from_status_id=lead.status_id,
            to_status_id=status.id,
        )
        lead.status_id = status.id
        await session.commit()
    return await get_lead(session, current, lead.id)


async def _assign(
    session: AsyncSession, current: CurrentUser, lead: Lead, owner_id: uuid.UUID | None, reason: str
) -> bool:
    if owner_id == lead.owner_user_id:
        return False
    now = now_utc()
    session.add(
        LeadAssignment(
            lead_id=lead.id,
            from_user_id=lead.owner_user_id,
            to_user_id=owner_id,
            assigned_by_user_id=current.user.id,
            reason=reason,
        )
    )
    _add_activity(
        session,
        current,
        lead,
        "assigned",
        when=now,
        meta={
            "from": str(lead.owner_user_id) if lead.owner_user_id else None,
            "to": str(owner_id) if owner_id else None,
        },
    )
    lead.owner_user_id = owner_id
    lead.times_assigned += 1
    lead.last_assigned_at = now
    # Open tasks follow the lead to its new owner. History is never hidden (decision 19 Nov 2026).
    await session.execute(
        update(Task)
        .where(Task.lead_id == lead.id, Task.completed_at.is_(None))
        .values(assigned_to_user_id=owner_id)
    )
    return True


async def change_owner(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    lead_id: uuid.UUID,
    owner_id: uuid.UUID | None,
) -> Lead:
    """Reassign a lead (needs the Assign right)."""
    lead = await get_lead(session, current, lead_id)
    await _check_owner(session, current, owner_id)
    if await _assign(session, current, lead, owner_id, "manual"):
        await session.commit()
    return await get_lead(session, current, lead.id)


async def _labels(
    session: AsyncSession, current: CurrentUser, label_ids: list[uuid.UUID]
) -> list[Label]:
    labels = list(
        await session.scalars(
            select(Label).where(
                Label.id.in_(label_ids), Label.organization_id == current.organization_id
            )
        )
    )
    if len(labels) != len(set(label_ids)):
        raise invalid("unknown_label", "One or more labels don't exist.")
    return labels


async def set_labels(
    session: AsyncSession, current: CurrentUser, lead_id: uuid.UUID, label_ids: list[uuid.UUID]
) -> Lead:
    """Replace a lead's labels (logged on the timeline)."""
    lead = await get_lead(session, current, lead_id)
    labels = await _labels(session, current, label_ids)
    before = {ll.label.name for ll in lead.lead_labels}
    after = {label.name for label in labels}
    if before != after:
        await session.execute(delete(LeadLabel).where(LeadLabel.lead_id == lead.id))
        session.add_all(LeadLabel(lead_id=lead.id, label_id=label.id) for label in labels)
        _add_activity(
            session,
            current,
            lead,
            "label_change",
            when=now_utc(),
            meta={"added": sorted(after - before), "removed": sorted(before - after)},
        )
        await session.commit()
    return await get_lead(session, current, lead.id)


# --- Activities ---------------------------------------------------------------------------


async def log_activity(
    session: AsyncSession, current: CurrentUser, lead_id: uuid.UUID, data: ActivityCreate
) -> Lead:
    """Record a quick action. A call or meeting marks the lead contacted (sets response time)."""
    lead = await get_lead(session, current, lead_id)
    if data.type == "call" and data.outcome not in CALL_OUTCOMES:
        raise invalid("outcome_required", "Choose the call outcome.")
    if data.type == "meeting" and data.outcome not in MEETING_OUTCOMES:
        raise invalid("outcome_required", "Choose the meeting outcome.")
    if data.type not in ("call", "meeting") and data.outcome:
        raise invalid("unexpected_outcome", "Only calls and meetings have an outcome.")
    if data.type == "comment" and not data.notes:
        raise invalid("notes_required", "Write the comment.")

    now = now_utc()
    _add_activity(
        session,
        current,
        lead,
        data.type,
        when=now,
        outcome=data.outcome,
        notes=data.notes,
        location=data.location if data.type == "meeting" else None,
    )
    if data.type in CONTACT_TYPES and lead.first_contacted_at is None:
        lead.first_contacted_at = now
        lead.response_time_seconds = max(0, int((now - lead.created_at).total_seconds()))
    if data.type == "call":
        lead.calls_made += 1
    lead.last_activity_at = now
    lead.last_activity_note = _summary(data)
    if data.follow_up:
        await _new_task(
            session,
            current,
            lead,
            data.follow_up.type,
            data.follow_up.due_at,
            data.follow_up.notes,
            None,
        )
    await session.commit()
    return await get_lead(session, current, lead.id)


def _summary(data: ActivityCreate) -> str:
    label = {
        "call": "Call",
        "whatsapp": "WhatsApp",
        "text": "Text",
        "email": "Email",
        "meeting": "Meeting",
        "comment": "Comment",
    }[data.type]
    outcome = f" ({data.outcome.replace('_', ' ')})" if data.outcome else ""
    note = f": {data.notes}" if data.notes else ""
    return f"{label}{outcome}{note}"[:280]


async def timeline(
    session: AsyncSession, current: CurrentUser, lead_id: uuid.UUID
) -> list[ActivityOut]:
    """Full history of a lead, newest first (never filtered by owner)."""
    lead = await get_lead(session, current, lead_id)
    rows = (
        await session.scalars(
            select(Activity)
            .where(Activity.lead_id == lead.id)
            .order_by(Activity.occurred_at.desc(), Activity.id.desc())
            .limit(300)
        )
    ).unique()
    names = await status_names(session, current)
    return [to_activity(activity, names) for activity in rows]


# --- Tasks --------------------------------------------------------------------------------


async def _new_task(
    session: AsyncSession,
    current: CurrentUser,
    lead: Lead,
    type_: str,
    due_at: datetime,
    notes: str | None,
    assignee: uuid.UUID | None,
) -> Task:
    if due_at.tzinfo is None:
        raise invalid("timezone_required", "Send the due time with a timezone (ISO 8601).")
    task = Task(
        organization_id=lead.organization_id,
        lead_id=lead.id,
        assigned_to_user_id=assignee or lead.owner_user_id or current.user.id,
        type=type_,
        notes=notes,
        due_at=due_at,
        created_by_user_id=current.user.id,
    )
    session.add(task)
    _add_activity(
        session,
        current,
        lead,
        "task_created",
        when=now_utc(),
        meta={"task_type": type_, "due_at": due_at.isoformat()},
    )
    await _refresh_next_task(session, lead.id)
    return task


async def create_task(
    session: AsyncSession, current: CurrentUser, lead_id: uuid.UUID, data: TaskCreate
) -> Task:
    """Add a follow-up task to a lead."""
    lead = await get_lead(session, current, lead_id)
    if data.assigned_to_user_id and data.assigned_to_user_id != current.user.id:
        await _check_owner(session, current, data.assigned_to_user_id)
    task = await _new_task(
        session, current, lead, data.type, data.due_at, data.notes, data.assigned_to_user_id
    )
    await session.commit()
    return await _get_task(session, current, task.id)


async def lead_tasks(session: AsyncSession, current: CurrentUser, lead_id: uuid.UUID) -> list[Task]:
    """A lead's tasks: open first (soonest due), then completed."""
    lead = await get_lead(session, current, lead_id)
    return list(
        (
            await session.scalars(
                select(Task)
                .where(Task.lead_id == lead.id)
                .order_by(Task.completed_at.is_not(None), Task.due_at)
                .execution_options(populate_existing=True)
            )
        ).unique()
    )


async def _get_task(session: AsyncSession, current: CurrentUser, task_id: uuid.UUID) -> Task:
    task = await session.scalar(
        select(Task)
        .where(Task.id == task_id, Task.organization_id == current.organization_id)
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise not_found("Task")
    owners = await lead_scope(session, current)
    if (
        owners is not None
        and task.assigned_to_user_id not in owners
        and task.lead.owner_user_id not in owners
    ):
        raise not_found("Task")
    return task


async def update_task(
    session: AsyncSession, current: CurrentUser, task_id: uuid.UUID, data: TaskUpdate
) -> Task:
    """Complete, reopen, reschedule or edit a task."""
    task = await _get_task(session, current, task_id)
    changes = data.model_dump(exclude_unset=True)
    now = now_utc()
    if "completed" in changes:
        if changes["completed"] and task.completed_at is None:
            task.completed_at = now
            task.completed_by_user_id = current.user.id
            _add_activity(
                session,
                current,
                task.lead,
                "task_completed",
                when=now,
                meta={"task_type": task.type, "notes": task.notes},
            )
        elif not changes["completed"]:
            task.completed_at = None
            task.completed_by_user_id = None
    if changes.get("due_at"):
        if changes["due_at"].tzinfo is None:
            raise invalid("timezone_required", "Send the due time with a timezone (ISO 8601).")
        task.due_at = changes["due_at"]
    if "notes" in changes:
        task.notes = changes["notes"]
    await _refresh_next_task(session, task.lead_id)
    await session.commit()
    return await _get_task(session, current, task.id)


# --- Bulk actions -------------------------------------------------------------------------


async def _bulk_leads(
    session: AsyncSession, current: CurrentUser, lead_ids: list[uuid.UUID]
) -> list[Lead]:
    owners = await lead_scope(session, current)
    return list(
        (
            await session.scalars(
                scoped(select(Lead).where(Lead.id.in_(lead_ids)), current, owners)
            )
        ).unique()
    )


async def bulk_reassign(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    lead_ids: list[uuid.UUID],
    owner_id: uuid.UUID | None,
) -> BulkResult:
    """Give many leads a new owner."""
    await _check_owner(session, current, owner_id)
    leads = await _bulk_leads(session, current, lead_ids)
    changed = 0
    for lead in leads:
        changed += await _assign(session, current, lead, owner_id, "bulk")
    if changed:
        add_audit_log(
            session,
            organization_id=current.organization_id,
            actor_user_id=current.user.id,
            entity_type="lead",
            entity_id=f"{changed} leads",
            action="bulk_reassign",
            changes={"owner_user_id": [None, str(owner_id) if owner_id else None]},
            request=request,
        )
        await session.commit()
    return _result(changed, len(lead_ids) - len(leads), "Not found or outside your scope.")


async def bulk_status(
    session: AsyncSession, current: CurrentUser, lead_ids: list[uuid.UUID], status_id: uuid.UUID
) -> BulkResult:
    """Change status on many leads; not-yet-contacted leads are skipped."""
    status = await _status(session, current, status_id)
    leads = await _bulk_leads(session, current, lead_ids)
    eligible = [lead for lead in leads if lead.first_contacted_at is not None]
    now = now_utc()
    changed = 0
    for lead in eligible:
        if lead.status_id != status.id:
            _add_activity(
                session,
                current,
                lead,
                "status_change",
                when=now,
                from_status_id=lead.status_id,
                to_status_id=status.id,
                meta={"bulk": True},
            )
            lead.status_id = status.id
            changed += 1
    if changed:
        await session.commit()
    skipped = len(lead_ids) - len(eligible)
    return _result(
        changed, skipped, "Not contacted yet (log a call or meeting first), or outside your scope."
    )


async def bulk_labels(
    session: AsyncSession,
    current: CurrentUser,
    lead_ids: list[uuid.UUID],
    label_ids: list[uuid.UUID],
    mode: str,
) -> BulkResult:
    """Add or remove labels on many leads."""
    labels = await _labels(session, current, label_ids)
    leads = await _bulk_leads(session, current, lead_ids)
    ids = [lead.id for lead in leads]
    if ids:
        if mode == "remove":
            await session.execute(
                delete(LeadLabel).where(
                    LeadLabel.lead_id.in_(ids), LeadLabel.label_id.in_(label_ids)
                )
            )
        else:
            existing = set(
                (
                    await session.execute(
                        select(LeadLabel.lead_id, LeadLabel.label_id).where(
                            LeadLabel.lead_id.in_(ids)
                        )
                    )
                ).all()
            )
            session.add_all(
                LeadLabel(lead_id=lead_id, label_id=label.id)
                for lead_id in ids
                for label in labels
                if (lead_id, label.id) not in existing
            )
        now = now_utc()
        names = sorted(label.name for label in labels)
        for lead in leads:
            _add_activity(
                session,
                current,
                lead,
                "label_change",
                when=now,
                meta={"added" if mode == "add" else "removed": names, "bulk": True},
            )
        await session.commit()
    return _result(len(ids), len(lead_ids) - len(ids), "Not found or outside your scope.")


def open_lead_condition() -> Any:
    """Leads whose status isn't final (used by dashboard cards)."""
    return and_(LeadStatus.id == Lead.status_id, LeadStatus.is_terminal.is_(False))
