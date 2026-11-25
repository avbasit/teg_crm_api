"""Settings › Integrations: connection status per ad platform (tokens are never returned)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.users import UserRef

Provider = Literal["meta", "tiktok", "linkedin", "snapchat"]


class PageOut(BaseModel):
    """A Facebook Page the connected user manages."""

    id: uuid.UUID
    external_id: str
    name: str
    is_selected: bool
    subscribed: bool = Field(description="Meta sends this Page's new leads to TEG CRM.")


class AdAccountOut(BaseModel):
    """An ad account."""

    id: uuid.UUID
    external_id: str
    name: str
    currency: str
    timezone: str | None
    is_selected: bool


class IntegrationOut(BaseModel):
    """One platform card on Settings › Integrations."""

    provider: Provider
    label: str
    available: bool = Field(description="TEG CRM has a connector for this platform yet.")
    configured: bool = Field(description="The platform app's keys are set in Vercel.")
    status: Literal["not_connected", "connected", "needs_reconnect", "disconnected"]
    connected_as: str | None = None
    connected_by: UserRef | None = None
    connected_at: datetime | None = None
    token_expires_at: datetime | None = None
    assign_mode: Literal["unassigned", "campaign"] = "unassigned"
    pages: list[PageOut] = []
    ad_accounts: list[AdAccountOut] = []
    last_webhook_at: datetime | None = None
    last_lead_sync_at: datetime | None = None
    last_spend_sync_at: datetime | None = None
    last_error: str | None = None
    leads_last_30_days: int = 0
    webhook_url: str | None = None
    redirect_uri: str | None = None


class ConnectUrl(BaseModel):
    """Where to send the browser to sign in with the platform."""

    url: str


class SelectionIn(BaseModel):
    """`PUT /integrations/meta/selection`."""

    page_ids: list[uuid.UUID] = Field(max_length=50)
    ad_account_ids: list[uuid.UUID] = Field(max_length=50)


class AssignModeIn(BaseModel):
    """`PATCH /integrations/meta/settings`."""

    assign_mode: Literal["unassigned", "campaign"]


class SyncIn(BaseModel):
    """`POST /integrations/meta/sync`."""

    what: Literal["leads", "spend"]


class SyncResult(BaseModel):
    """What a manual or scheduled sync did."""

    created: int = 0
    skipped: int = 0
    failed: int = 0
    spend_rows: int = 0
