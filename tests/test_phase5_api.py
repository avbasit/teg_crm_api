"""Phase 5 API: Meta connect flow, lead webhook, missed-lead and spend jobs, Marketing report.

Meta is replaced by ``FakeMeta`` (same interface as the real client), and webhook
deliveries are signed with a test app secret, so the whole flow runs offline.
Decisions under test (25 Nov 2026): one CRM campaign per ad campaign, created
automatically; leads go to Unassigned until go-live; AED spend converted to USD.
Run with ``RUN_DB_TESTS=1``; every test is rolled back (see conftest).
"""

import hashlib
import hmac
import json
import random
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import get_settings
from app.integrations import meta as meta_module

pytestmark = [pytest.mark.anyio, pytest.mark.live_db]

PASSWORD = "Correct-Horse-42"
APP_SECRET = "test-app-secret"
VERIFY = "test-verify-token"
CRON = "test-cron-secret"


class FakeMeta:
    """Offline stand-in for the Graph API."""

    def __init__(self) -> None:
        suffix = random.randint(1, 10**9)
        self.page_id = f"page{suffix}"
        self.campaign_id = f"camp{suffix}"
        self.leads: dict[str, dict] = {}
        self.form_leads_list: list[dict] = []
        self.insight_rows: list[dict] = []
        self.subscribed: list[str] = []

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://facebook.test/dialog?state={state}&redirect_uri={redirect_uri}"

    async def exchange_code(self, code: str, redirect_uri: str):
        assert code == "good-code"
        return "user-token", 60 * 24 * 3600

    async def me(self, token: str):
        return {"id": "fbuser1", "name": "Aisha on Facebook"}

    async def pages(self, token: str):
        return [{"id": self.page_id, "name": "TEG Consultancy", "access_token": "page-token"}]

    async def subscribe_page(self, page_id: str, page_token: str) -> None:
        assert page_token == "page-token"
        self.subscribed.append(page_id)

    async def ad_accounts(self, token: str):
        return [
            {
                "account_id": "act1",
                "name": "TEG Ads",
                "currency": "AED",
                "timezone_name": "Asia/Dubai",
            }
        ]

    async def lead(self, leadgen_id: str, page_token: str):
        return self.leads[leadgen_id]

    async def forms(self, page_id: str, page_token: str):
        return [{"id": "form1", "name": "D2 enquiry form"}]

    async def form_leads(self, form_id: str, page_token: str, since: int):
        return self.form_leads_list

    async def insights(self, account_id: str, token: str, since: date, until: date):
        return self.insight_rows

    def make_lead(self, leadgen_id: str, phone: str) -> dict:
        lead = {
            "id": leadgen_id,
            "created_time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "field_data": [
                {"name": "full_name", "values": ["Usman Tariq"]},
                {"name": "phone_number", "values": [phone]},
                {"name": "email", "values": [f"{leadgen_id}@example.com"]},
                {"name": "city", "values": ["Lahore"]},
                {"name": "what_is_your_budget?", "values": ["USD 100k+"]},
            ],
            "campaign_id": self.campaign_id,
            "campaign_name": "D2 Lahore Nov",
            "adset_id": f"{self.campaign_id}-set",
            "adset_name": "Lahore 25-45",
            "ad_id": f"{self.campaign_id}-ad",
            "ad_name": "Video A",
            "form_id": "form1",
            "platform": "ig",
        }
        self.leads[leadgen_id] = lead
        return lead


@pytest.fixture
def fake_meta():
    config = get_settings()
    saved = {
        k: getattr(config, k)
        for k in (
            "meta_app_id",
            "meta_app_secret",
            "meta_webhook_verify_token",
            "integrations_secret_key",
            "cron_secret",
        )
    }
    config.meta_app_id, config.meta_app_secret = "123", APP_SECRET
    config.meta_webhook_verify_token, config.cron_secret = VERIFY, CRON
    config.integrations_secret_key = "test-integrations-key"
    fake = FakeMeta()
    meta_module.use_meta(fake)
    yield fake
    meta_module.use_meta(None)
    for key, value in saved.items():
        setattr(config, key, value)


async def sign_in(client, user):
    client.cookies.clear()
    response = await client.post(
        "/api/v1/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def phone() -> str:
    return f"+92397{random.randint(1_000_000, 9_999_999)}"


async def connect(client, fake) -> dict:
    started = (await client.get("/api/v1/integrations/meta/connect")).json()
    state = parse_qs(urlparse(started["url"]).query)["state"][0]
    back = await client.get(
        "/api/v1/integrations/meta/callback", params={"code": "good-code", "state": state}
    )
    assert back.status_code == 303, back.text
    assert back.headers["location"].endswith("/settings/integrations?connected=meta")
    return next(
        i for i in (await client.get("/api/v1/integrations")).json() if i["provider"] == "meta"
    )


async def deliver(client, fake, leadgen_id: str, *, secret: str = APP_SECRET):
    body = json.dumps({
        "object": "page",
        "entry": [{"id": fake.page_id, "changes": [{"field": "leadgen", "value": {
            "leadgen_id": leadgen_id, "page_id": fake.page_id, "form_id": "form1"}}]}],
    }).encode()  # fmt: skip
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return await client.post(
        "/api/v1/integrations/meta/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
    )


async def leads_from(client, source_campaign: str):
    items = (await client.get("/api/v1/leads", params={"source": "facebook", "limit": 200})).json()[
        "items"
    ]
    return [
        lead for lead in items if lead["campaign"] and lead["campaign"]["name"] == source_campaign
    ]


async def test_webhook_verification(client, fake_meta):
    ok = await client.get(
        "/api/v1/integrations/meta/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "12345"},
    )
    assert ok.status_code == 200 and ok.text == "12345"
    bad = await client.get(
        "/api/v1/integrations/meta/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
    )
    assert bad.status_code == 403


async def test_connect_picks_the_page_and_ad_account(client, make_user, fake_meta):
    await sign_in(client, await make_user("administrator"))
    before = next(
        i for i in (await client.get("/api/v1/integrations")).json() if i["provider"] == "meta"
    )
    assert before["status"] == "not_connected" and before["configured"] is True
    card = await connect(client, fake_meta)
    assert card["status"] == "connected" and card["connected_as"] == "Aisha on Facebook"
    assert card["assign_mode"] == "unassigned"
    assert [(p["name"], p["is_selected"], p["subscribed"]) for p in card["pages"]] == [
        ("TEG Consultancy", True, True)
    ]
    assert [(a["currency"], a["is_selected"]) for a in card["ad_accounts"]] == [("AED", True)]
    assert fake_meta.subscribed == [fake_meta.page_id]
    assert "token" not in json.dumps(card).lower().replace("token_expires_at", "")
    others = {
        i["provider"]: i["available"] for i in (await client.get("/api/v1/integrations")).json()
    }
    assert others == {"meta": True, "tiktok": False, "linkedin": False, "snapchat": False}

    await sign_in(client, await make_user("agent"))
    assert (await client.get("/api/v1/integrations")).status_code == 403


async def test_webhook_lead_lands_unassigned_in_an_auto_campaign(client, make_user, fake_meta):
    await sign_in(client, await make_user("administrator"))
    await connect(client, fake_meta)
    fake_meta.make_lead("lg1", phone())

    first = await deliver(client, fake_meta, "lg1")
    assert first.status_code == 200 and first.json() == {"handled": 1}
    again = await deliver(client, fake_meta, "lg1")  # Meta retries: no duplicate
    assert again.json() == {"handled": 0}
    forged = await deliver(client, fake_meta, "lg1", secret="wrong")
    assert forged.status_code == 403

    [lead] = await leads_from(client, "D2 Lahore Nov")
    assert lead["owner"] is None  # Unassigned until go-live
    assert lead["full_name"] == "Usman Tariq" and lead["city"] == "Lahore"
    assert lead["budget_text"] == "USD 100k+" and lead["sub_source"] == "instagram"
    detail = (await client.get(f"/api/v1/leads/{lead['id']}")).json()
    assert (
        detail["source_campaign_name"] == "D2 Lahore Nov" and detail["source_ad_name"] == "Video A"
    )
    campaigns = (await client.get("/api/v1/campaigns", params={"search": "D2 Lahore Nov"})).json()[
        "items"
    ]
    assert campaigns[0]["campaign_type"] == "paid_social" and campaigns[0]["source"] == "facebook"


async def test_campaign_mode_uses_the_round_robin(client, make_user, fake_meta):
    agent = await make_user("agent")
    await sign_in(client, await make_user("administrator"))
    await connect(client, fake_meta)
    fake_meta.make_lead("lg-a", phone())
    await deliver(client, fake_meta, "lg-a")  # creates the CRM campaign
    [campaign] = (await client.get("/api/v1/campaigns", params={"search": "D2 Lahore Nov"})).json()[
        "items"
    ]
    await client.put(
        f"/api/v1/campaigns/{campaign['id']}/agents", json={"agents": [{"user_id": str(agent.id)}]}
    )
    mode = await client.patch(
        "/api/v1/integrations/meta/settings", json={"assign_mode": "campaign"}
    )
    assert mode.json()["assign_mode"] == "campaign"

    fake_meta.make_lead("lg-b", phone())
    await deliver(client, fake_meta, "lg-b")
    owners = [lead["owner"] for lead in await leads_from(client, "D2 Lahore Nov")]
    assert (
        sum(1 for o in owners if o and o["id"] == str(agent.id)) == 1
    )  # lg-b; lg-a stays unassigned


async def test_jobs_catch_missed_leads_and_sync_spend_for_the_marketing_report(
    client, make_user, fake_meta
):
    await sign_in(client, await make_user("administrator"))
    await connect(client, fake_meta)
    missed = fake_meta.make_lead("lg-missed", phone())
    fake_meta.form_leads_list = [missed]
    today = datetime.now(UTC).date()
    fake_meta.insight_rows = [
        {"campaign_id": fake_meta.campaign_id, "campaign_name": "D2 Lahore Nov",
         "adset_id": f"{fake_meta.campaign_id}-set", "adset_name": "Lahore 25-45",
         "ad_id": f"{fake_meta.campaign_id}-ad", "ad_name": "Video A", "spend": "367.25",
         "impressions": "12000", "clicks": "340", "date_start": str(today),
         "actions": [{"action_type": "lead", "value": "3"}]},
    ]  # fmt: skip

    assert (await client.get("/api/v1/jobs/leads")).status_code == 401
    bearer = {"Authorization": f"Bearer {CRON}"}
    leads_job = await client.get("/api/v1/jobs/leads", headers=bearer)
    assert leads_job.status_code == 200, leads_job.text
    assert list(leads_job.json()["results"].values())[0]["created"] == 1
    spend_job = await client.get("/api/v1/jobs/spend", headers=bearer)
    assert list(spend_job.json()["results"].values())[0]["spend_rows"] == 1

    [lead] = await leads_from(client, "D2 Lahore Nov")
    statuses = (await client.get("/api/v1/settings/lead-statuses")).json()
    won = next(s["id"] for s in statuses if s["reference"] == "deal_closed")
    await client.post(
        f"/api/v1/leads/{lead['id']}/activities", json={"type": "call", "outcome": "answered"}
    )
    await client.patch(f"/api/v1/leads/{lead['id']}", json={"deal_value": 5000})
    assert (
        await client.patch(f"/api/v1/leads/{lead['id']}/status", json={"status_id": won})
    ).status_code == 200

    campaign_id = lead["campaign"]["id"]
    report = (
        await client.get("/api/v1/reports/marketing", params={"campaign_id": campaign_id})
    ).json()
    k = report["kpis"]
    assert report["currency"] == "USD" and report["spend_currencies"] == ["AED"]
    assert k["spend"]["value"] == pytest.approx(367.25 * 0.272294, abs=0.01)  # = USD 100.00
    assert k["leads"]["value"] == 1 and k["won"]["value"] == 1
    assert k["cost_per_lead"]["value"] == pytest.approx(100.0, abs=0.01)
    assert k["roas"]["value"] == pytest.approx(50.0, abs=0.01)
    assert k["platform_leads"] == 3 and k["impressions"] == 12000
    levels = {(r["level"], r["name"]) for r in report["rows"]}
    assert {("campaign", "D2 Lahore Nov"), ("adset", "Lahore 25-45"), ("ad", "Video A")} <= levels
    ad_row = next(r for r in report["rows"] if r["level"] == "ad")
    assert ad_row["leads"] == 1 and ad_row["won"] == 1

    export = await client.get(
        "/api/v1/reports/marketing/export", params={"campaign_id": campaign_id}
    )
    assert export.status_code == 200 and export.text.startswith("﻿Level,Name,Spend (USD)")
    await sign_in(client, await make_user("sales_manager"))  # no Marketing dashboard
    assert (await client.get("/api/v1/reports/marketing")).status_code == 403
