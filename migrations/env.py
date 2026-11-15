"""Alembic environment for TEG CRM.

Migrations run against Neon's *unpooled* (direct) connection, because DDL and
session-level settings do not work reliably through the PgBouncer pooler.
The URL comes from ``DATABASE_URL_UNPOOLED`` in the repository-root ``.env``
(or the process environment).
"""

import os
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")


def get_database_url() -> str:
    """Return the direct Neon URL, rewritten for the psycopg 3 driver.

    Raises:
        RuntimeError: If ``DATABASE_URL_UNPOOLED`` is not set.
    """
    url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("POSTGRES_URL_NON_POOLING")
    if not url:
        raise RuntimeError(
            "DATABASE_URL_UNPOOLED is not set. Copy .env.example to .env and fill it in."
        )
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (``alembic upgrade head --sql``)."""
    context.configure(url=get_database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in a single transaction against the database."""
    engine = create_engine(get_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=False)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
