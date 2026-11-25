"""Meta Graph API client: sign-in, Pages, ad accounts, lead ads and ad insights.

Kept deliberately thin: every method is one Graph call (plus paging), so the
service layer can be tested with :class:`FakeMeta` in place of the network.
Docs: developers.facebook.com/docs/marketing-api/guides/lead-ads.
"""

import hashlib
import hmac
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings

LEAD_FIELDS = (
    "id,created_time,field_data,ad_id,ad_name,adset_id,adset_name,"
    "campaign_id,campaign_name,form_id,platform,is_organic"
)
INSIGHT_FIELDS = (
    "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,spend,impressions,clicks,actions"
)


class MetaError(Exception):
    """A Graph API error. ``expired`` means the user must reconnect."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        """Keep Meta's error code (190 = invalid or expired token)."""
        super().__init__(message)
        self.code = code

    @property
    def expired(self) -> bool:
        """Whether the token is no longer valid."""
        return self.code == 190


class MetaApi(Protocol):
    """What TEG CRM needs from Meta (the real client and the test fake both provide it)."""

    def authorize_url(self, state: str, redirect_uri: str) -> str: ...  # noqa: D102
    async def exchange_code(self, code: str, redirect_uri: str) -> tuple[str, int | None]: ...  # noqa: D102
    async def me(self, token: str) -> dict[str, Any]: ...  # noqa: D102
    async def pages(self, token: str) -> list[dict[str, Any]]: ...  # noqa: D102
    async def subscribe_page(self, page_id: str, page_token: str) -> None: ...  # noqa: D102
    async def ad_accounts(self, token: str) -> list[dict[str, Any]]: ...  # noqa: D102
    async def lead(self, leadgen_id: str, page_token: str) -> dict[str, Any]: ...  # noqa: D102
    async def forms(self, page_id: str, page_token: str) -> list[dict[str, Any]]: ...  # noqa: D102
    async def form_leads(  # noqa: D102
        self, form_id: str, page_token: str, since: int
    ) -> list[dict[str, Any]]: ...
    async def insights(  # noqa: D102
        self, account_id: str, token: str, since: date, until: date
    ) -> list[dict[str, Any]]: ...


def valid_signature(body: bytes, header: str | None) -> bool:
    """Check Meta's ``X-Hub-Signature-256`` (HMAC-SHA256 of the raw body with the app secret)."""
    secret = get_settings().meta_app_secret
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


class MetaClient:
    """The real Graph API over HTTPS."""

    def __init__(self) -> None:
        """Read the app id, secret and API version from settings."""
        config = get_settings()
        self.app_id = config.meta_app_id or ""
        self.app_secret = config.meta_app_secret or ""
        self.config_id = config.meta_config_id
        self.base = f"https://graph.facebook.com/{config.meta_graph_version}"
        self.dialog = f"https://www.facebook.com/{config.meta_graph_version}/dialog/oauth"

    async def _call(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = path if path.startswith("https://") else f"{self.base}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(method, url, params=params)
        body: dict[str, Any] = response.json() if response.content else {}
        if response.status_code >= 400 or "error" in body:
            error = body.get("error") or {}
            raise MetaError(
                error.get("message") or f"Meta error {response.status_code}", code=error.get("code")
            )
        return body

    async def _all(
        self, path: str, params: dict[str, Any], limit: int = 2000
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        body = await self._call("GET", path, params)
        while True:
            rows.extend(body.get("data", []))
            next_url = (body.get("paging") or {}).get("next")
            if not next_url or len(rows) >= limit:
                return rows
            body = await self._call("GET", next_url)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        """Facebook Login for Business dialog."""
        params = {"client_id": self.app_id, "redirect_uri": redirect_uri, "state": state}
        if self.config_id:
            params |= {"config_id": self.config_id, "response_type": "code",
                       "override_default_response_type": "true"}  # fmt: skip
        else:
            params["scope"] = (
                "pages_show_list,pages_read_engagement,pages_manage_metadata,pages_manage_ads,"
                "leads_retrieval,ads_read,business_management"
            )
        return f"{self.dialog}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> tuple[str, int | None]:
        """Code → short-lived token → long-lived user token (~60 days)."""
        short = await self._call(
            "GET",
            "oauth/access_token",
            {
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },  # fmt: skip
        )
        long = await self._call(
            "GET",
            "oauth/access_token",
            {
                "grant_type": "fb_exchange_token",
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "fb_exchange_token": short["access_token"],
            },  # fmt: skip
        )
        return long["access_token"], long.get("expires_in")

    async def me(self, token: str) -> dict[str, Any]:
        """The signed-in Facebook user."""
        return await self._call("GET", "me", {"fields": "id,name", "access_token": token})

    async def pages(self, token: str) -> list[dict[str, Any]]:
        """Pages the user manages, each with a Page token (long-lived user → non-expiring)."""
        return await self._all(
            "me/accounts", {"fields": "id,name,access_token", "access_token": token}
        )

    async def subscribe_page(self, page_id: str, page_token: str) -> None:
        """Ask Meta to send this Page's new leads to our webhook."""
        await self._call(
            "POST",
            f"{page_id}/subscribed_apps",
            {"subscribed_fields": "leadgen", "access_token": page_token},
        )

    async def ad_accounts(self, token: str) -> list[dict[str, Any]]:
        """Ad accounts the user can read."""
        return await self._all(
            "me/adaccounts",
            {"fields": "account_id,name,currency,timezone_name", "access_token": token},
        )

    async def lead(self, leadgen_id: str, page_token: str) -> dict[str, Any]:
        """One lead with its answers and ad/campaign names."""
        return await self._call(
            "GET", leadgen_id, {"fields": LEAD_FIELDS, "access_token": page_token}
        )

    async def forms(self, page_id: str, page_token: str) -> list[dict[str, Any]]:
        """The Page's lead forms."""
        return await self._all(
            f"{page_id}/leadgen_forms", {"fields": "id,name,status", "access_token": page_token}
        )

    async def form_leads(self, form_id: str, page_token: str, since: int) -> list[dict[str, Any]]:
        """A form's leads created after ``since`` (Unix time)."""
        filtering = f'[{{"field":"time_created","operator":"GREATER_THAN","value":{since}}}]'
        return await self._all(
            f"{form_id}/leads",
            {
                "fields": LEAD_FIELDS,
                "filtering": filtering,
                "access_token": page_token,
                "limit": 100,
            },
        )

    async def insights(
        self, account_id: str, token: str, since: date, until: date
    ) -> list[dict[str, Any]]:
        """Spend per ad per day (ad account timezone)."""
        return await self._all(
            f"act_{account_id}/insights",
            {
                "level": "ad",
                "time_increment": 1,
                "fields": INSIGHT_FIELDS,
                "time_range": f'{{"since":"{since}","until":"{until}"}}',
                "access_token": token,
                "limit": 500,
            },
            limit=20000,
        )


_client: MetaApi | None = None


def get_meta() -> MetaApi:
    """The Meta client (tests replace it with a fake via :func:`use_meta`)."""
    global _client  # noqa: PLW0603 - process-wide singleton, swapped only in tests
    if _client is None:
        _client = MetaClient()
    return _client


def use_meta(client: MetaApi | None) -> None:
    """Swap the client (tests)."""
    global _client  # noqa: PLW0603
    _client = client
