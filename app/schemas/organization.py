"""Organisation schemas."""

from pydantic import BaseModel, ConfigDict


class OrganizationPublic(BaseModel):
    """Public organisation details (shown on the login screen and in the app shell)."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    slug: str
    logo_url: str | None
    timezone: str
    default_currency: str
