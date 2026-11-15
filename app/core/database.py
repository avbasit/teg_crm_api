"""Async database access for Neon Postgres (SQLAlchemy 2 + asyncpg).

Serverless notes:
    * Vercel functions are short-lived, so the app does not keep its own
      connection pool (``NullPool``). Neon's PgBouncer pooler, reached through the
      ``-pooler`` host in ``DATABASE_URL``, does the pooling instead.
    * PgBouncer in transaction mode breaks asyncpg's prepared-statement cache,
      so both caches are switched off.
    * asyncpg doesn't understand libpq URL options such as ``sslmode`` or
      ``channel_binding``. They are removed from the URL, and TLS is required
      through ``connect_args``.
"""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding", "options", "connect_timeout"}


def to_asyncpg_url(url: str) -> str:
    """Convert a libpq-style Postgres URL into one SQLAlchemy+asyncpg accepts.

    Args:
        url: URL as provided by Neon/Vercel, e.g. ``postgresql://u:p@host/db?sslmode=require``.

    Returns:
        The same URL with the ``postgresql+asyncpg`` scheme and libpq-only query
        parameters removed.
    """
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in _LIBPQ_ONLY_PARAMS]
    return urlunsplit(("postgresql+asyncpg", parts.netloc, parts.path, urlencode(query), ""))


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine (created lazily on first use)."""
    return create_async_engine(
        to_asyncpg_url(get_settings().database_url),
        poolclass=NullPool,
        connect_args={
            "ssl": "require",
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "server_settings": {"application_name": "teg-crm-api"},
        },
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory bound to :func:`get_engine`."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields one session per request.

    Commits only when the route itself calls ``commit()``; anything else is
    rolled back when the session closes.
    """
    async with get_session_factory()() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
"""Route parameter type for a per-request database session: ``session: DbSession``."""
