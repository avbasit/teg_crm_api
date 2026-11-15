"""`/organization`: public details of the tenant served by this deployment."""

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.core.database import DbSession
from app.repositories.organization_repository import get_organization_by_slug
from app.schemas.organization import OrganizationPublic

router = APIRouter(prefix="/organization", tags=["organization"])


@router.get("", response_model=OrganizationPublic, summary="Public organisation details")
async def get_organization(session: DbSession) -> OrganizationPublic:
    """Return name, logo and locale settings for the login screen and app shell."""
    organization = await get_organization_by_slug(session, get_settings().organization_slug)
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organisation not found")
    return OrganizationPublic.model_validate(organization)
