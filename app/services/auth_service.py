"""Sign-in, sessions, sign-out and password changes."""

from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, invalid
from app.core.request_context import client_ip, user_agent
from app.core.security import (
    hash_password,
    hash_token,
    needs_rehash,
    new_token,
    password_problems,
    verify_password,
)
from app.models.identity import User, UserSession
from app.repositories import identity_repository
from app.repositories.audit_repository import add_audit_log, add_auth_event, diff
from app.services.access import CurrentUser, load_rights

INVALID_CREDENTIALS = AppError(
    401, "invalid_credentials", "That username and password don't match. Please try again."
)
LAST_ACTIVE_THROTTLE = timedelta(minutes=5)

# "invited" = created by an admin with a temporary password, not yet signed in and
# changed it. Such users may sign in (they're held on the change-password screen);
# setting their own password makes them "active".
SIGN_IN_STATUSES = frozenset({"active", "invited"})


def _now() -> datetime:
    return datetime.now(UTC)


async def login(
    session: AsyncSession, request: Request, username: str, password: str
) -> tuple[CurrentUser, str]:
    """Check credentials and open a session.

    Returns:
        The signed-in user (with rights) and the raw session token, to be set as
        a cookie. Only the token's hash is stored.

    Raises:
        AppError: 401 bad credentials, 423 locked, 403 account not active.
    """
    settings = get_settings()
    now = _now()
    user = await identity_repository.get_user_by_username(session, username)

    if user is None:
        verify_password(password, None)  # same work as a real check: no username enumeration
        add_auth_event(
            session, event_type="login_failed", request=request, username_attempted=username
        )
        await session.commit()
        raise INVALID_CREDENTIALS

    if user.locked_until and user.locked_until > now:
        minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        raise AppError(
            423, "account_locked", f"Too many failed attempts. Try again in {minutes} minute(s)."
        )

    if not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        meta = {"failed_count": user.failed_login_count}
        add_auth_event(
            session,
            event_type="login_failed",
            request=request,
            organization_id=user.organization_id,
            user_id=user.id,
            username_attempted=username,
            meta=meta,
        )
        if user.failed_login_count >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_login_count = 0
            add_auth_event(
                session,
                event_type="account_locked",
                request=request,
                organization_id=user.organization_id,
                user_id=user.id,
                meta={"minutes": settings.login_lockout_minutes},
            )
        await session.commit()
        raise INVALID_CREDENTIALS

    if user.status not in SIGN_IN_STATUSES:
        # Only revealed after a correct password, so it can't be used to probe accounts.
        raise AppError(
            403,
            "account_inactive",
            "This account is not active. Please contact a TEG CRM administrator.",
        )

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    user.last_active_at = now
    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    token = new_token()
    user_session = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        user_agent=user_agent(request),
        ip_address=client_ip(request),
        expires_at=now + timedelta(hours=settings.session_idle_hours),
        created_at=now,
    )
    session.add(user_session)
    add_auth_event(
        session,
        event_type="login_succeeded",
        request=request,
        organization_id=user.organization_id,
        user_id=user.id,
    )
    await session.commit()
    current = CurrentUser(
        user=user, session=user_session, rights=await load_rights(session, user.role)
    )
    return current, token


async def authenticate(session: AsyncSession, token: str) -> CurrentUser | None:
    """Resolve a session cookie to the signed-in user, sliding the expiry on activity.

    Returns ``None`` when the token is unknown, expired, revoked, or the user is
    no longer active (paused/archived users are signed out on their next request).
    """
    settings = get_settings()
    now = _now()
    user_session = await identity_repository.get_active_session(session, hash_token(token), now)
    if user_session is None:
        return None

    user = await session.get(User, user_session.user_id)
    if user is None or user.status not in SIGN_IN_STATUSES:
        return None

    changed = False
    hard_limit = user_session.created_at + timedelta(days=settings.session_max_days)
    idle_window = timedelta(hours=settings.session_idle_hours)
    if user_session.expires_at - now < idle_window / 2 and user_session.expires_at < hard_limit:
        user_session.expires_at = min(now + idle_window, hard_limit)
        changed = True
    if user.last_active_at is None or now - user.last_active_at > LAST_ACTIVE_THROTTLE:
        user.last_active_at = now
        changed = True
    if changed:
        await session.commit()

    return CurrentUser(
        user=user, session=user_session, rights=await load_rights(session, user.role)
    )


async def update_profile(
    session: AsyncSession, request: Request, current: CurrentUser, changes: dict[str, str | None]
) -> None:
    """Update the signed-in user's own name, email or phone (My Account)."""
    user = current.user
    fields = ("full_name", "email", "phone")
    before = {f: getattr(user, f) for f in fields}
    for field, value in changes.items():
        if field == "full_name":
            if not value or not value.strip():
                raise invalid("full_name_required", "Full name can't be empty.")
            value = value.strip()
        elif value is not None:
            value = value.strip() or None
        setattr(user, field, value)
    after = {f: getattr(user, f) for f in fields}
    if after == before:
        return
    add_audit_log(
        session,
        organization_id=user.organization_id,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="profile_update",
        changes=diff(before, after),
        request=request,
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(409, "email_taken", "That email address is already in use.") from exc


async def logout(session: AsyncSession, request: Request, current: CurrentUser) -> None:
    """Revoke the current session."""
    current.session.revoked_at = _now()
    add_auth_event(
        session,
        event_type="logout",
        request=request,
        organization_id=current.organization_id,
        user_id=current.user.id,
    )
    await session.commit()


async def change_password(
    session: AsyncSession,
    request: Request,
    current: CurrentUser,
    current_password: str,
    new_password: str,
) -> None:
    """Change the signed-in user's password and sign out their other sessions.

    Raises:
        AppError: 422 wrong current password, weak or reused password.
    """
    user = current.user
    if not verify_password(current_password, user.password_hash):
        raise invalid("wrong_current_password", "Your current password is not correct.")
    problems = password_problems(new_password, username=user.username)
    if problems:
        raise invalid("weak_password", " ".join(problems))
    if verify_password(new_password, user.password_hash):
        raise invalid("password_reused", "Choose a password different from your current one.")

    now = _now()
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.password_changed_at = now
    was_invited = user.status == "invited"
    if was_invited:
        user.status = "active"  # invitation accepted: first own password set
    await identity_repository.revoke_user_sessions(
        session, user.id, now, keep_session_id=current.session.id
    )
    add_auth_event(
        session,
        event_type="invite_accepted" if was_invited else "password_changed",
        request=request,
        organization_id=user.organization_id,
        user_id=user.id,
    )
    add_audit_log(
        session,
        organization_id=user.organization_id,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="invite_accepted" if was_invited else "password_change",
        changes={"status": ["invited", "active"]} if was_invited else None,
        request=request,
    )
    await session.commit()
