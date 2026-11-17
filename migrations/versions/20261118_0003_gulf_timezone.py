"""Use Gulf time (Asia/Dubai, UTC+04:00) as the organisation timezone.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18

Decision 18 Nov 2026 (project-phases.md, open decision 1). Timestamps stay
stored in UTC; this only changes how dates are shown and how "today" is cut.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Switch the TEG organisation and the column default to Asia/Dubai."""
    op.execute("ALTER TABLE organizations ALTER COLUMN timezone SET DEFAULT 'Asia/Dubai'")
    op.execute("UPDATE organizations SET timezone = 'Asia/Dubai' WHERE slug = 'teg'")


def downgrade() -> None:
    """Back to Asia/Karachi."""
    op.execute("ALTER TABLE organizations ALTER COLUMN timezone SET DEFAULT 'Asia/Karachi'")
    op.execute("UPDATE organizations SET timezone = 'Asia/Karachi' WHERE slug = 'teg'")
