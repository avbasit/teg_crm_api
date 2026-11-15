"""Seed reference data and the first Super Admin user.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15

Reference data comes from ``migrations/sql/0002_seed_reference_data.sql``.
The admin password is read from the ``SEED_ADMIN_PASSWORD`` environment variable
at run time and stored only as an argon2id hash; the user must change it at
first login.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from argon2 import PasswordHasher

from migrations.sql_runner import run_sql_file

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_USERNAME = "basit"
ADMIN_FULL_NAME = "Abdul Basit"
ADMIN_LEGACY_JALDI_ID = 999


def upgrade() -> None:
    """Insert reference data, then the first Super Admin."""
    password = os.environ.get("SEED_ADMIN_PASSWORD")
    if not password:
        raise RuntimeError("Set SEED_ADMIN_PASSWORD for this run (it is never written to disk).")

    run_sql_file("0002_seed_reference_data.sql")

    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO users (organization_id, role_id, username, full_name, password_hash,
                               must_change_password, status, is_assignable, legacy_jaldi_id)
            SELECT o.id, r.id, :username, :full_name, :password_hash,
                   true, 'active', true, :legacy_id
            FROM organizations o
            JOIN roles r ON r.organization_id = o.id AND r.key = 'super_admin'
            WHERE o.slug = 'teg'
            """
        ),
        {
            "username": ADMIN_USERNAME,
            "full_name": ADMIN_FULL_NAME,
            "password_hash": PasswordHasher().hash(password),
            "legacy_id": ADMIN_LEGACY_JALDI_ID,
        },
    )


def downgrade() -> None:
    """Remove all seeded rows, children first."""
    for table in [
        "users",
        "form_field_aliases",
        "programs",
        "labels",
        "lead_statuses",
        "role_menu_permissions",
        "roles",
        "app_menus",
        "organizations",
    ]:
        op.execute(f"DELETE FROM {table}")
