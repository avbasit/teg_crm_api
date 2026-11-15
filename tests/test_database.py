"""Tests for the Neon URL handling in ``app.core.database``."""

from app.core.database import to_asyncpg_url


def test_to_asyncpg_url_strips_libpq_options_and_sets_driver() -> None:
    """Neon URLs carry sslmode/channel_binding, which asyncpg rejects."""
    url = (
        "postgresql://user:secret@ep-x-pooler.c-1.us-east-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    assert to_asyncpg_url(url) == (
        "postgresql+asyncpg://user:secret@ep-x-pooler.c-1.us-east-1.aws.neon.tech/neondb"
    )


def test_to_asyncpg_url_keeps_other_query_params() -> None:
    """Unknown parameters are passed through untouched."""
    assert to_asyncpg_url("postgres://u:p@h/db?sslmode=require&foo=bar") == (
        "postgresql+asyncpg://u:p@h/db?foo=bar"
    )
