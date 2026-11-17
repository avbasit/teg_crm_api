"""User management rules (docs/roles-and-permissions.md §2 hierarchy rules)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import conflict, forbidden, invalid, not_found
from app.core.security import hash_password, password_problems
from app.models.identity import Role, User
from app.repositories import identity_repository
from app.repositories.audit_repository import add_audit_log, diff
from app.schemas.users import UserCreate, UserUpdate
from app.services.access import CurrentUser, can_manage, visible_user_ids

MENU = "users"
_AUDITED = (
    "full_name",
    "email",
    "phone",
    "role_id",
    "reports_to_user_id",
    "is_assignable",
    "status",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _snapshot(user: User) -> dict[str, Any]:
    return {field: getattr(user, field) for field in _AUDITED}


async def get_visible_user(session: AsyncSession, current: CurrentUser, user_id: uuid.UUID) -> User:
    """User by id if it exists and is inside the caller's data scope (else 404)."""
    user = await identity_repository.get_user(session, current.organization_id, user_id)
    visible = await visible_user_ids(session, current, MENU)
    if user is None or (visible is not None and user.id not in visible):
        raise not_found("User")
    return user


async def _role_to_grant(session: AsyncSession, current: CurrentUser, role_id: uuid.UUID) -> Role:
    role = await identity_repository.get_role(session, current.organization_id, role_id)
    if role is None:
        raise invalid("unknown_role", "That role doesn't exist.")
    if not can_manage(current, role):
        raise forbidden("You can only give people a role below your own.")
    return role


async def _check_manager(
    session: AsyncSession,
    current: CurrentUser,
    manager_id: uuid.UUID | None,
    role: Role,
    user_id: uuid.UUID | None,
) -> None:
    if manager_id is None:
        return
    if manager_id == user_id:
        raise invalid("invalid_manager", "Someone can't report to themselves.")
    manager = await identity_repository.get_user(session, current.organization_id, manager_id)
    if manager is None or manager.status != "active":
        raise invalid("invalid_manager", "The manager must be an active user.")
    if manager.role.hierarchy_level <= role.hierarchy_level:
        raise invalid("invalid_manager", "The manager must have a more senior role than this user.")


async def _guard_last_super_admin(session: AsyncSession, user: User) -> None:
    if user.role.key == "super_admin" and user.status == "active":
        if await identity_repository.count_active_super_admins(session, user.organization_id) <= 1:
            raise conflict(
                "last_super_admin", "There must always be at least one active Super Admin."
            )


def _check_password(password: str, username: str) -> None:
    problems = password_problems(password, username=username)
    if problems:
        raise invalid("weak_password", " ".join(problems))


async def _commit(session: AsyncSession, *, flush_only: bool = False) -> None:
    """Commit (or just flush), turning database guard failures into readable errors."""
    try:
        if flush_only:
            await session.flush()
        else:
            await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        message = str(exc.orig)
        if "uq_users_username" in message:
            raise conflict("username_taken", "That username is already in use.") from exc
        if "uq_users_email" in message:
            raise conflict("email_taken", "That email address is already in use.") from exc
        raise
    except DBAPIError as exc:
        await session.rollback()
        if "reporting cycle" in str(exc.orig):
            raise invalid("reporting_cycle", "That would create a reporting loop.") from exc
        raise


async def create_user(
    session: AsyncSession, request: Request, current: CurrentUser, data: UserCreate
) -> User:
    """Create an *invited* account with a temporary password.

    The user signs in with it, must choose their own password, and then becomes
    active (see ``auth_service.change_password``). Until then they sit in the
    Invited tab.
    """
    role = await _role_to_grant(session, current, data.role_id)
    await _check_manager(session, current, data.reports_to_user_id, role, None)
    _check_password(data.temporary_password, data.username)

    user = User(
        organization_id=current.organization_id,
        role_id=role.id,
        reports_to_user_id=data.reports_to_user_id,
        username=data.username,
        full_name=data.full_name.strip(),
        email=data.email,
        phone=data.phone,
        password_hash=hash_password(data.temporary_password),
        must_change_password=True,
        status="invited",
        is_assignable=data.is_assignable,
    )
    session.add(user)
    await _commit(session, flush_only=True)  # get the id; duplicates surface here as 409
    add_audit_log(
        session,
        organization_id=current.organization_id,
        actor_user_id=current.user.id,
        entity_type="user",
        entity_id=user.id,
        action="create",
        changes=diff({}, {"username": user.username, **_snapshot(user)}),
        request=request,
    )
    await _commit(session)
    await session.refresh(user)
    return user


async def update_user(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    user_id: uuid.UUID,
    data: UserUpdate,
) -> User:
    """Edit a user's details, role or manager."""
    user = await get_visible_user(session, current, user_id)
    is_self = user.id == current.user.id
    if not is_self and not can_manage(current, user.role):
        raise forbidden("You can only edit people below your own role.")

    changes = data.model_dump(exclude_unset=True)
    before = _snapshot(user)
    role = user.role
    if "role_id" in changes and changes["role_id"] != user.role_id:
        if is_self:
            raise forbidden("You can't change your own role.")
        new_role = await _role_to_grant(session, current, changes["role_id"])
        if user.role.key == "super_admin" and new_role.key != "super_admin":
            await _guard_last_super_admin(session, user)
        role = new_role
        user.role_id = new_role.id
        user.role = new_role
    if "reports_to_user_id" in changes or "role_id" in changes:
        manager_id = changes.get("reports_to_user_id", user.reports_to_user_id)
        await _check_manager(session, current, manager_id, role, user.id)

    for field in ("full_name", "email", "phone", "reports_to_user_id", "is_assignable"):
        if field in changes:
            value = changes[field]
            if field == "full_name":
                if value is None:
                    raise invalid("full_name_required", "Full name can't be empty.")
                value = value.strip()
            setattr(user, field, value)

    after = _snapshot(user)
    if after != before:
        add_audit_log(
            session,
            organization_id=current.organization_id,
            actor_user_id=current.user.id,
            entity_type="user",
            entity_id=user.id,
            action="role_change" if before["role_id"] != after["role_id"] else "update",
            changes=diff(before, after),
            request=request,
        )
        await _commit(session)
        await session.refresh(user)
    return user


async def set_status(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    user_id: uuid.UUID,
    status: str,
) -> User:
    """Pause, archive or re-activate a user. Pausing/archiving signs them out everywhere."""
    user = await get_visible_user(session, current, user_id)
    if user.id == current.user.id:
        raise forbidden("You can't change the status of your own account.")
    if not can_manage(current, user.role):
        raise forbidden("You can only change people below your own role.")
    if user.status == status:
        return user
    if status in ("paused", "archived"):
        await _guard_last_super_admin(session, user)
    if status == "active" and not user.password_hash:
        raise invalid("no_password", "Reset this user's password before activating the account.")

    now = _now()
    before = _snapshot(user)
    user.status = status
    user.archived_at = now if status == "archived" else None
    if status != "active":
        await identity_repository.revoke_user_sessions(session, user.id, now)
    add_audit_log(
        session,
        organization_id=current.organization_id,
        actor_user_id=current.user.id,
        entity_type="user",
        entity_id=user.id,
        action={"paused": "pause", "archived": "archive", "active": "activate"}[status],
        changes=diff(before, _snapshot(user)),
        request=request,
    )
    await _commit(session)
    await session.refresh(user)
    return user


async def reset_password(
    session: AsyncSession, request: Request, current: CurrentUser, user_id: uuid.UUID, password: str
) -> None:
    """Give a user a temporary password; they must change it at next sign-in."""
    user = await get_visible_user(session, current, user_id)
    if user.id == current.user.id:
        raise forbidden("Use 'Change password' for your own account.")
    if not can_manage(current, user.role):
        raise forbidden("You can only reset passwords for people below your own role.")
    _check_password(password, user.username)

    now = _now()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    await identity_repository.revoke_user_sessions(session, user.id, now)
    add_audit_log(
        session,
        organization_id=current.organization_id,
        actor_user_id=current.user.id,
        entity_type="user",
        entity_id=user.id,
        action="password_reset",
        request=request,
    )
    await _commit(session)
