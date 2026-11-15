"""Initial schema: organisation, roles and rights, users, leads, campaigns, logs.

Revision ID: 0001
Revises:
Create Date: 2026-11-15

DDL lives in ``migrations/sql/0001_initial_schema.sql`` so it reads as plain SQL.
"""

from collections.abc import Sequence

from alembic import op

from migrations.sql_runner import run_sql_file

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Reverse dependency order.
TABLES = [
    "job_runs",
    "email_logs",
    "auth_events",
    "audit_logs",
    "webhook_events",
    "import_rows",
    "imports",
    "lead_assignments",
    "activities",
    "tasks",
    "lead_labels",
    "leads",
    "contacts",
    "api_keys",
    "campaign_agents",
    "campaigns",
    "form_field_aliases",
    "programs",
    "labels",
    "lead_statuses",
    "password_reset_tokens",
    "user_invitations",
    "user_sessions",
    "team_members",
    "teams",
    "users",
    "role_menu_permissions",
    "roles",
    "app_menus",
    "organizations",
]
TYPES = [
    "job_run_status",
    "webhook_event_status",
    "auth_event_type",
    "import_status",
    "task_type",
    "activity_type",
    "campaign_status",
    "campaign_type",
    "lead_source",
    "team_member_role",
    "data_scope",
    "user_status",
]
FUNCTIONS = ["check_users_reporting_cycle()", "prevent_log_modification()", "set_updated_at()"]


def upgrade() -> None:
    """Create all Phase 1-4 tables, enums, triggers and indexes."""
    run_sql_file("0001_initial_schema.sql")


def downgrade() -> None:
    """Drop everything created by this revision (extensions are left installed)."""
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for type_name in TYPES:
        op.execute(f"DROP TYPE IF EXISTS {type_name}")
    for function in FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
