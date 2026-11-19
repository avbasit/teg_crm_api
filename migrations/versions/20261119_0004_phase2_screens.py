"""Phase 2: switch on the Dashboard, All Leads and My Tasks screens.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19

- `dashboard.pipeline` becomes the after-login Overview at /dashboard (Dashboard v1:
  live cards + sample charts; full dashboards in Phase 4).
- `leads.all` and `tasks` become available.
- Decision 19 Nov 2026: history is never hidden on reassignment, so the
  hide-history rule is forced off.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Make Phase 2 screens available and turn off hide-history."""
    op.execute(
        "UPDATE app_menus SET is_available = true, label = 'Overview', route = '/dashboard' "
        "WHERE key = 'dashboard.pipeline'"
    )
    op.execute("UPDATE app_menus SET is_available = true WHERE key IN ('leads.all', 'tasks')")
    op.execute(
        "UPDATE organizations SET settings = settings || '{\"hide_history_on_reassign\": false}'"
    )


def downgrade() -> None:
    """Hide the Phase 2 screens again."""
    op.execute(
        "UPDATE app_menus SET is_available = false, label = 'My Pipeline', "
        "route = '/dashboard/pipeline' WHERE key = 'dashboard.pipeline'"
    )
    op.execute("UPDATE app_menus SET is_available = false WHERE key IN ('leads.all', 'tasks')")
