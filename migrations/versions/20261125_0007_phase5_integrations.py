"""Phase 5: ad platform integrations, ad hierarchy and daily spend, Marketing dashboard on.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-25

SQL lives in ``migrations/sql/0007_phase5_integrations.sql`` (also used by the
throwaway end-to-end test database).
"""

from collections.abc import Sequence

from alembic import op

from migrations.sql_runner import run_sql_file

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the integration tables and switch on Integrations and Marketing."""
    run_sql_file("0007_phase5_integrations.sql")


def downgrade() -> None:
    """Drop the integration tables (enum values added to lead_source stay)."""
    op.execute(
        "UPDATE app_menus SET is_available = false "
        "WHERE key IN ('settings.integrations', 'dashboard.marketing')"
    )
    op.execute("ALTER TABLE leads DROP COLUMN ad_id")
    for table in (
        "ad_insights_daily",
        "ads",
        "ad_sets",
        "ad_campaigns",
        "ad_accounts",
        "integration_pages",
        "integrations",
    ):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP TYPE integration_status")
    op.execute("DROP TYPE integration_provider")
