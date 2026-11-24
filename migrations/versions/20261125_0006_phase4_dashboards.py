"""Phase 4: Productivity dashboard on, indexes for dashboard figures.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-25

SQL lives in ``migrations/sql/0006_phase4_dashboards.sql`` (also used by the
throwaway end-to-end test database).
"""

from collections.abc import Sequence

from alembic import op

from migrations.sql_runner import run_sql_file

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Switch on Productivity and add the dashboard indexes."""
    run_sql_file("0006_phase4_dashboards.sql")


def downgrade() -> None:
    """Hide Productivity again and drop the indexes."""
    op.execute(
        "UPDATE app_menus SET is_available = false, label = 'My Productivity', "
        "route = '/dashboard/productivity' WHERE key = 'dashboard.productivity'"
    )
    op.execute("DROP INDEX IF EXISTS ix_activities_organization_id_type_occurred_at")
    op.execute("DROP INDEX IF EXISTS ix_leads_organization_id_first_contacted_at")
