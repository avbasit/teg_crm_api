"""Phase 3 API: campaigns, round-robin, duplicate routing, website intake, CSV import.

Decisions under test (19 Nov 2026): a repeat enquiry goes to the earlier owner if
active, otherwise round-robin; equal turns; nobody available → Unassigned (seen by
managers); paused campaigns still assign; Jaldi's blocking rules; markets list.
Run with ``RUN_DB_TESTS=1``; every test is rolled back (see conftest).
"""

import csv
import random
from pathlib import Path

import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.live_db]

PASSWORD = "Correct-Horse-42"
JALDI_CSV = (
    Path(__file__).resolve().parents[2]
    / "teg_crm"
    / "projectDocument"
    / "LeadExport-2026-11-10-200542.csv"
)


async def sign_in(client, user):
    client.cookies.clear()
    response = await client.post(
        "/api/v1/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def phone() -> str:
    return f"+92399{random.randint(1_000_000, 9_999_999)}"


async def new_campaign(client, agents=(), **fields):
    body = {
        "name": f"Test campaign {random.randint(1, 10**6)}",
        "campaign_type": "website",
        "agents": [{"user_id": str(a.id)} for a in agents],
        **fields,
    }
    response = await client.post("/api/v1/campaigns", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def website_key(client, campaign, **fields):
    response = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/api-keys", json={"name": "Website", **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


async def post_lead(client, key, headers=None, **body):
    return await client.post(
        "/api/v1/intake/leads",
        json={"name": "Web Lead", "phone": phone(), **body},
        headers={"X-Api-Key": key, **(headers or {})},
    )


async def campaign_leads(client, campaign):
    response = await client.get(
        "/api/v1/leads", params={"campaign_id": campaign["id"], "limit": 200}
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


# --- Markets and campaigns ------------------------------------------------------------------


async def test_markets_are_a_fixed_list_admins_can_edit(client, make_user):
    await sign_in(client, await make_user("administrator"))
    names = [m["name"] for m in (await client.get("/api/v1/settings/markets")).json()]
    assert {"Lahore", "Islamabad", "Doha", "KSA", "UAE"} <= set(names)
    added = await client.post("/api/v1/settings/markets", json={"name": "Karachi"})
    assert added.status_code == 201
    again = await client.post("/api/v1/settings/markets", json={"name": "karachi"})
    assert again.status_code == 409  # names are case-insensitive

    await sign_in(client, await make_user("head_of_sales"))
    assert (
        await client.post("/api/v1/settings/markets", json={"name": "Riyadh"})
    ).status_code == 403


async def test_create_campaign_with_programme_market_and_agents(client, make_user):
    a1, a2 = await make_user("agent"), await make_user("agent")
    await sign_in(client, await make_user("marketing_manager"))
    program = (await client.get("/api/v1/settings/programs")).json()[0]
    market = (await client.get("/api/v1/settings/markets")).json()[0]
    campaign = await new_campaign(
        client, [a1, a2], program_id=program["id"], market_id=market["id"]
    )
    assert campaign["status"] == "active"
    assert campaign["source"] == "webhook"  # default for a website campaign
    assert campaign["program"]["id"] == program["id"]
    assert campaign["market"]["name"] == market["name"]
    assert campaign["agents_total"] == 2 and campaign["agents_active"] == 2
    assert sum(a["is_next"] for a in campaign["agents"]) == 1
    assert campaign["intake_url"].endswith("/api/v1/intake/leads")

    counts = (await client.get("/api/v1/campaigns/counts")).json()
    assert counts["active"] >= 1
    listed = (await client.get("/api/v1/campaigns", params={"search": campaign["name"]})).json()
    assert [c["id"] for c in listed["items"]] == [campaign["id"]]

    await sign_in(client, await make_user("sales_manager"))
    assert (await client.get(f"/api/v1/campaigns/{campaign['id']}")).status_code == 200
    assert (
        await client.post("/api/v1/campaigns", json={"name": "X", "campaign_type": "manual"})
    ).status_code == 403
    await sign_in(client, await make_user("agent"))
    assert (await client.get("/api/v1/campaigns")).status_code == 403


# --- Round-robin and unassigned ------------------------------------------------------------


async def test_round_robin_gives_equal_turns_and_skips_switched_off_agents(client, make_user):
    agents = [await make_user("agent") for _ in range(3)]
    await sign_in(client, await make_user("administrator"))
    campaign = await new_campaign(client, agents)
    key = await website_key(client, campaign)

    for _ in range(6):
        assert (await post_lead(client, key)).status_code == 201
    owners = [lead["owner"]["id"] for lead in await campaign_leads(client, campaign)]
    assert sorted(owners.count(str(a.id)) for a in agents) == [2, 2, 2]

    off = agents[2]
    toggled = await client.patch(
        f"/api/v1/campaigns/{campaign['id']}/agents/{off.id}", json={"is_active": False}
    )
    assert toggled.status_code == 200
    paused = await client.patch(
        f"/api/v1/campaigns/{campaign['id']}/status", json={"status": "paused"}
    )
    assert paused.json()["status"] == "paused"
    for _ in range(2):  # paused campaigns still accept and assign
        assert (await post_lead(client, key)).status_code == 201
    owners = [lead["owner"]["id"] for lead in await campaign_leads(client, campaign)]
    assert owners.count(str(off.id)) == 2
    assert owners.count(str(agents[0].id)) == 3 and owners.count(str(agents[1].id)) == 3


async def test_no_one_available_leaves_lead_unassigned_for_managers(client, make_user):
    manager = await make_user("sales_manager")
    agent = await make_user("agent")
    await sign_in(client, await make_user("administrator"))
    campaign = await new_campaign(client, [], assignment_strategy="round_robin")
    key = await website_key(client, campaign)
    assert (await post_lead(client, key, name="Nobody Yet")).status_code == 201
    lead = (await campaign_leads(client, campaign))[0]
    assert lead["owner"] is None

    await sign_in(client, manager)
    seen = await client.get(
        "/api/v1/leads", params={"owner_id": "unassigned", "campaign_id": campaign["id"]}
    )
    assert [item["id"] for item in seen.json()["items"]] == [lead["id"]]
    taken = await client.patch(
        f"/api/v1/leads/{lead['id']}/owner", json={"owner_user_id": str(manager.id)}
    )
    assert taken.status_code == 200 and taken.json()["owner"]["id"] == str(manager.id)

    await sign_in(client, agent)  # agents can't assign, so they don't see unassigned leads
    hidden = await client.get("/api/v1/leads", params={"owner_id": "unassigned"})
    assert hidden.json()["total"] == 0


# --- Duplicates ----------------------------------------------------------------------------


async def test_repeat_enquiry_goes_to_earlier_owner_else_round_robin(client, make_user):
    first_owner, other = await make_user("agent"), await make_user("agent")
    await sign_in(client, await make_user("super_admin"))
    camp_a = await new_campaign(client, [first_owner])
    camp_b = await new_campaign(client, [other])
    key_a, key_b = await website_key(client, camp_a), await website_key(client, camp_b)
    person = phone()

    assert (await post_lead(client, key_a, phone=person, name="Sana Iqbal")).status_code == 201
    original = (await campaign_leads(client, camp_a))[0]
    assert original["owner"]["id"] == str(first_owner.id) and not original["is_duplicate"]

    assert (await post_lead(client, key_b, phone=person, name="Sana Iqbal")).status_code == 201
    repeat = (await campaign_leads(client, camp_b))[0]
    assert repeat["owner"]["id"] == str(first_owner.id)  # not the campaign's own agent
    assert repeat["is_duplicate"] is True
    detail = (await client.get(f"/api/v1/leads/{repeat['id']}")).json()
    assert detail["duplicate_of"]["id"] == original["id"]
    timeline = (await client.get(f"/api/v1/leads/{repeat['id']}/activities")).json()
    duplicate = next(a for a in timeline if a["type"] == "duplicate_detected")
    assert duplicate["meta"]["duplicate_of"] == original["id"]
    assignment = next(a for a in timeline if a["type"] == "assigned")
    assert assignment["meta"]["reason"] == "duplicate_routing"

    assert (await client.post(f"/api/v1/users/{first_owner.id}/pause", json={})).status_code == 200
    assert (await post_lead(client, key_b, phone=person, name="Sana Iqbal")).status_code == 201
    third = next(
        lead for lead in await campaign_leads(client, camp_b) if lead["id"] != repeat["id"]
    )
    assert third["owner"]["id"] == str(other.id)  # earlier owner paused → round-robin


async def test_duplicate_rules_can_block_repeat_website_leads(client, make_user):
    await sign_in(client, await make_user("administrator"))
    rules = (await client.get("/api/v1/settings/duplicates")).json()
    assert rules["routing"] == "previous_owner" and rules["import_block_same_campaign"] is True
    changed = await client.patch(
        "/api/v1/settings/duplicates", json={"digital_prevent_global": True}
    )
    assert changed.status_code == 200 and changed.json()["digital_prevent_global"] is True

    campaign = await new_campaign(client, [])
    key = await website_key(client, campaign)
    person = phone()
    assert (await post_lead(client, key, phone=person)).status_code == 201
    assert (await post_lead(client, key, phone=person)).status_code == 201  # accepted, dropped
    assert len(await campaign_leads(client, campaign)) == 1

    await sign_in(client, await make_user("head_of_sales"))
    assert (await client.get("/api/v1/settings/duplicates")).status_code == 200
    blocked = await client.patch("/api/v1/settings/duplicates", json={"routing": "round_robin"})
    assert blocked.status_code == 403


# --- Website intake ------------------------------------------------------------------------


async def test_website_key_checks_cors_spam_and_retries(client, make_user):
    await sign_in(client, await make_user("administrator"))
    campaign = await new_campaign(client, [])
    key = await website_key(client, campaign, allowed_origins=["https://tegconsultancy.com/"])
    client.cookies.clear()  # the intake never uses a session

    assert (await post_lead(client, "tegk_wrong")).status_code == 401
    missing = await client.post("/api/v1/intake/leads", json={"name": "X", "phone": phone()})
    assert missing.status_code == 401

    other_site = await post_lead(client, key, headers={"Origin": "https://evil.example"})
    assert other_site.status_code == 403
    ours = await post_lead(client, key, headers={"Origin": "https://tegconsultancy.com"})
    assert ours.status_code == 201
    assert ours.headers["access-control-allow-origin"] == "https://tegconsultancy.com"

    preflight = await client.options(
        "/api/v1/intake/leads",
        headers={"Origin": "https://tegconsultancy.com", "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 204
    assert "x-api-key" in preflight.headers["access-control-allow-headers"].lower()
    not_intake = await client.options("/api/v1/leads", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in not_intake.headers

    assert (await post_lead(client, key, _gotcha="I am a bot")).status_code == 201  # spam: dropped
    first = await post_lead(client, key, external_id="form-123")
    again = await post_lead(client, key, external_id="form-123")
    assert again.json() == {"status": "duplicate_event", "reference": first.json()["reference"]}
    no_contact = await client.post(
        "/api/v1/intake/leads", json={"name": "No contact"}, headers={"X-Api-Key": key}
    )
    assert no_contact.status_code == 422

    await sign_in(client, await make_user("administrator"))
    assert len(await campaign_leads(client, campaign)) == 2  # ours + form-123
    keys = (await client.get(f"/api/v1/campaigns/{campaign['id']}/api-keys")).json()
    assert keys[0]["last_used_at"] is not None and "key" not in keys[0]
    revoked = await client.delete(f"/api/v1/campaigns/{campaign['id']}/api-keys/{keys[0]['id']}")
    assert revoked.status_code == 204
    archived = await client.patch(
        f"/api/v1/campaigns/{campaign['id']}/status", json={"status": "archived"}
    )
    assert archived.status_code == 200
    client.cookies.clear()
    assert (await post_lead(client, key)).status_code == 401  # revoked


# --- CSV import ----------------------------------------------------------------------------

JALDI_MAPPING = {
    "Lead ID": "external_lead_id",
    "First Name": "first_name",
    "Last Name": "last_name",
    "Phone Number": "phone",
    "Email": "email",
    "Notes": "notes",
    "Date Created": "source_created_at",
}


def mapped(row_number: int, row: dict[str, str]) -> dict:
    return {
        "row_number": row_number,
        "values": {field: row.get(column) for column, field in JALDI_MAPPING.items()},
        "raw": row,
    }


async def test_csv_import_parses_notes_skips_duplicates_and_logs_rows(client, make_user):
    agent = await make_user("agent")
    await sign_in(client, await make_user("administrator"))
    campaign = await new_campaign(client, [agent], campaign_type="import")
    person = phone()
    rows = [
        {
            "First Name": "Hamza Malik",
            "Phone Number": person,
            "Notes": "what_is_your_budget?: PKR 20-30 lakh\ncity: Lahore\nwhatsapp_number: "
            + person,
            "Date Created": "2026-10-01 09:00:00 UTC",
        },
        {"First Name": "Hamza again", "Phone Number": person},  # same person, same campaign
        {"First Name": "No Contact"},
        {"First Name": "Zainab", "Email": f"zainab{random.randint(1, 10**6)}@example.com"},
    ]
    started = await client.post(
        "/api/v1/imports",
        json={
            "campaign_id": campaign["id"],
            "file_name": "test.csv",
            "total_rows": len(rows),
            "mapping": JALDI_MAPPING,
        },
    )
    assert started.status_code == 201, started.text
    import_id = started.json()["id"]
    batch = await client.post(
        f"/api/v1/imports/{import_id}/rows",
        json={"rows": [mapped(i + 1, r) for i, r in enumerate(rows)]},
    )
    assert batch.status_code == 200, batch.text
    result = batch.json()
    assert (result["imported"], result["skipped"], result["failed"]) == (2, 1, 1)
    assert [r["status"] for r in result["rows"]] == ["imported", "skipped", "failed", "imported"]

    resent = await client.post(
        f"/api/v1/imports/{import_id}/rows", json={"rows": [mapped(1, rows[0])]}
    )
    assert resent.json()["rows"][0]["message"] == "Already sent."
    done = (await client.post(f"/api/v1/imports/{import_id}/complete", json={})).json()
    assert (done["imported_rows"], done["skipped_duplicate_rows"], done["failed_rows"]) == (2, 1, 1)
    assert done["status"] == "completed"

    problems = (await client.get(f"/api/v1/imports/{import_id}/rows")).json()
    assert [(r["row_number"], r["error"]) for r in problems["items"]] == [
        (2, "Duplicate: this person is already in this campaign."),
        (3, "No phone number or email."),
    ]
    lead = (await client.get(f"/api/v1/leads/{result['rows'][0]['lead_id']}")).json()
    assert lead["budget_text"] == "PKR 20-30 lakh" and lead["city"] == "Lahore"
    assert lead["whatsapp"] == person and lead["source"] == "csv"
    assert lead["form_answers"]["what_is_your_budget?"] == "PKR 20-30 lakh"
    assert lead["owner"]["id"] == str(agent.id)
    assert lead["source_created_at"].startswith("2026-10-01T09:00:00")

    await sign_in(client, await make_user("agent"))
    assert (await client.get("/api/v1/imports")).status_code == 403


@pytest.mark.skipif(not JALDI_CSV.exists(), reason="Jaldi export not on this machine")
async def test_real_jaldi_sample_imports_cleanly(client, make_user):
    """A small sample of the real Jaldi export (rolled back; never committed or kept)."""
    with JALDI_CSV.open(encoding="utf-8-sig", newline="") as handle:
        sample = [row for _, row in zip(range(60), csv.DictReader(handle), strict=False)]
    await sign_in(client, await make_user("administrator"))
    campaign = await new_campaign(client, [await make_user("agent")], campaign_type="import")
    started = await client.post(
        "/api/v1/imports",
        json={
            "campaign_id": campaign["id"],
            "file_name": "jaldi-sample.csv",
            "total_rows": len(sample),
            "mapping": JALDI_MAPPING,
        },
    )
    batch = await client.post(
        f"/api/v1/imports/{started.json()['id']}/rows",
        json={"rows": [mapped(i + 1, r) for i, r in enumerate(sample)]},
    )
    assert batch.status_code == 200, batch.text
    result = batch.json()
    assert result["imported"] + result["skipped"] + result["failed"] == len(sample)
    assert result["failed"] == 0, [r["message"] for r in result["rows"] if r["status"] == "failed"]
    assert result["imported"] >= len(sample) * 0.8
    leads = await campaign_leads(client, campaign)
    assert all(lead["phone"] is None or lead["phone"].startswith("+") for lead in leads)
    with_answers = 0
    for lead in leads[:20]:
        detail = (await client.get(f"/api/v1/leads/{lead['id']}")).json()
        with_answers += bool(detail["form_answers"])
    assert with_answers > 0
