"""Shared test setup.

Unit tests need no database: if ``DATABASE_URL`` is unset (for example in CI),
it points at a closed local port. Tests marked ``live_db`` hit the real Neon
database and only run when ``RUN_DB_TESTS=1``.
"""

import os

import pytest

LIVE_DB = os.environ.get("RUN_DB_TESTS") == "1"

if not LIVE_DB:
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
