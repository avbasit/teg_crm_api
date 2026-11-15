"""Queries for the ``organizations`` table."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization


async def get_organization_by_slug(session: AsyncSession, slug: str) -> Organization | None:
    """Return the organisation with ``slug``, or ``None`` if it does not exist."""
    result = await session.execute(select(Organization).where(Organization.slug == slug))
    return result.scalar_one_or_none()
