"""Postgres ENUM types created by migration 0001 (never created or altered from Python)."""

from sqlalchemy.dialects.postgresql import ENUM

USER_STATUSES = ("active", "paused", "archived", "invited")
DATA_SCOPES = ("own", "team", "subtree", "all")
TEAM_MEMBER_ROLES = ("manager", "member")
AUTH_EVENT_TYPES = (
    "login_succeeded",
    "login_failed",
    "logout",
    "token_refreshed",
    "password_changed",
    "password_reset_requested",
    "password_reset_completed",
    "account_locked",
    "invite_accepted",
)

UserStatusType = ENUM(*USER_STATUSES, name="user_status", create_type=False)
DataScopeType = ENUM(*DATA_SCOPES, name="data_scope", create_type=False)
TeamMemberRoleType = ENUM(*TEAM_MEMBER_ROLES, name="team_member_role", create_type=False)
AuthEventTypeType = ENUM(*AUTH_EVENT_TYPES, name="auth_event_type", create_type=False)
