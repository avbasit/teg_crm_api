"""Phase 3: markets, website keys limited by origin, Unassigned menu, Phase 3 screens on.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19

SQL lives in ``migrations/sql/0005_phase3_campaigns.sql`` (also used by the
throwaway end-to-end test database).
"""

from collections.abc import Sequence

from alembic import op

from migrations.sql_runner import run_sql_file

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the Phase 3 schema and menu changes."""
    run_sql_file("0005_phase3_campaigns.sql")


def downgrade() -> None:
    """Hide the Phase 3 screens and drop the new columns and table."""
    op.execute(
        "UPDATE app_menus SET is_available = false "
        "WHERE key IN ('campaigns', 'leads.duplicates', 'leads.import', 'settings.duplicates')"
    )
    op.execute("DELETE FROM role_menu_permissions WHERE menu_key = 'leads.unassigned'")
    op.execute("DELETE FROM app_menus WHERE key = 'leads.unassigned'")
    op.execute("DROP INDEX IF EXISTS ix_leads_contact_id_created_at")
    op.execute("DROP INDEX IF EXISTS ix_campaign_agents_campaign_id_rr")
    op.execute("DROP INDEX IF EXISTS ix_api_keys_campaign_id")
    op.execute("ALTER TABLE api_keys DROP COLUMN allowed_origins")
    op.execute("ALTER TABLE campaigns DROP COLUMN market_id")
    op.execute("DROP TABLE markets")
