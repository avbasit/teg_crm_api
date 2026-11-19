"""Shared test setup.

Unit tests need no database: if ``DATABASE_URL`` is unset (for example in CI),
it points at a closed local port. Tests marked ``live_db`` hit the real Neon
database and only run when ``RUN_DB_TESTS=1``.

Live API tests run inside ONE outer transaction per test that is always rolled
back: the app's own commits become savepoints, so nothing is ever written to Neon.
"""

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest

LIVE_DB = os.environ.get("RUN_DB_TESTS") == "1"

if LIVE_DB:
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
else:
    # Environment variables win over .env, so unit tests never touch Neon.
    os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:1/test")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``live_db`` tests unless RUN_DB_TESTS=1."""
    if LIVE_DB:
        return
    skip = pytest.mark.skip(reason="set RUN_DB_TESTS=1 to run against Neon")
    for item in items:
        if "live_db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio (the app's event loop)."""
    return "asyncio"


@pytest.fixture
async def db_session_factory() -> AsyncIterator[object]:
    """Session factory bound to a connection whose transaction is rolled back after the test."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import to_asyncpg_url

    url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ["DATABASE_URL"]
    local = "@127.0.0.1" in url or "@localhost" in url  # throwaway test Postgres, no TLS
    engine = create_async_engine(
        to_asyncpg_url(url),
        poolclass=NullPool,
        connect_args={"ssl": False if local else "require", "statement_cache_size": 0},
    )
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield factory
        finally:
            await transaction.rollback()  # discard everything the test (and the app) wrote
            if local:
                # The throwaway PGlite server hangs on a graceful close; drop the socket instead.
                await connection.invalidate()
    await engine.dispose()


@pytest.fixture
async def client(db_session_factory: object) -> AsyncIterator[object]:
    """HTTP client for the app, with every request using the rolled-back connection."""
    import httpx

    from app.core.database import get_db_session
    from app.main import app

    async def override() -> AsyncIterator[object]:
        async with db_session_factory() as session:  # type: ignore[operator]
            yield session

    app.dependency_overrides[get_db_session] = override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
    app.dependency_overrides.clear()


MakeUser = Callable[..., Awaitable[object]]


@pytest.fixture
async def make_user(db_session_factory: object) -> MakeUser:
    """Create a user with a known password inside the test transaction."""
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.models import Organization, Role, User

    async def _make(
        role_key: str,
        password: str = "Correct-Horse-42",
        *,
        must_change_password: bool = False,
        status: str = "active",
        reports_to: object | None = None,
    ) -> User:
        async with db_session_factory() as session:  # type: ignore[operator]
            org = await session.scalar(select(Organization).where(Organization.slug == "teg"))
            role = await session.scalar(
                select(Role).where(Role.organization_id == org.id, Role.key == role_key)
            )
            user = User(
                organization_id=org.id,
                role_id=role.id,
                username=f"t_{role_key}_{uuid.uuid4().hex[:8]}",
                full_name=f"Test {role_key.replace('_', ' ').title()}",
                password_hash=hash_password(password),
                must_change_password=must_change_password,
                status=status,
                reports_to_user_id=getattr(reports_to, "id", None),
            )
            session.add(user)
            await session.commit()
            return user

    return _make
