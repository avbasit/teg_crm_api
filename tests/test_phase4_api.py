"""Phase 4 API: pipeline and productivity dashboards, scope, exports.

"Done when" (project-phases): figures match direct counts, and each role sees only
its own scope. Each test isolates its data with its own campaign.
Run with ``RUN_DB_TESTS=1``; every test is rolled back (see conftest).
"""

import random
from datetime import date, timedelta

import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.live_db]

PASSWORD = "Correct-Horse-42"


async def sign_in(client, user):
    client.cookies.clear()
    response = await client.post(
        "/api/v1/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def phone() -> str:
    return f"+92398{random.randint(1_000_000, 9_999_999)}"


async def campaign_with_leads(client, agents, count, **fields):
    """An admin-made website campaign with ``count`` leads shared by round-robin."""
    program = (await client.get("/api/v1/settings/programs")).json()[0]
    created = await client.post(
        "/api/v1/campaigns",
        json={
            "name": f"Report test {random.randint(1, 10**6)}",
            "campaign_type": "website",
            "program_id": program["id"],
            "agents": [{"user_id": str(a.id)} for a in agents],
            **fields,
        },
    )
    assert created.status_code == 201, created.text
    campaign = created.json()
    key = (
        await client.post(f"/api/v1/campaigns/{campaign['id']}/api-keys", json={"name": "Web"})
    ).json()["key"]
    for index in range(count):
        sent = await client.post(
            "/api/v1/intake/leads",
            json={"name": f"Report Lead {index}", "phone": phone()},
            headers={"X-Api-Key": key},
        )
        assert sent.status_code == 201, sent.text
    return campaign, program


async def leads_of(client, campaign):
    response = await client.get(
        "/api/v1/leads", params={"campaign_id": campaign["id"], "limit": 200}
    )
    return response.json()["items"]


async def status_id(client, reference):
    statuses = (await client.get("/api/v1/settings/lead-statuses")).json()
    return next(s["id"] for s in statuses if s["reference"] == reference)


async def test_pipeline_figures_match_the_leads(client, make_user):
    a1, a2 = await make_user("agent"), await make_user("agent")
    admin = await make_user("administrator")
    await sign_in(client, admin)
    campaign, program = await campaign_with_leads(client, [a1, a2], 4)
    leads = await leads_of(client, campaign)
    mine = next(lead for lead in leads if lead["owner"]["id"] == str(a1.id))

    await sign_in(client, a1)
    call = await client.post(
        f"/api/v1/leads/{mine['id']}/activities", json={"type": "call", "outcome": "answered"}
    )
    assert call.status_code == 201, call.text
    qualified = await client.patch(
        f"/api/v1/leads/{mine['id']}/status",
        json={"status_id": await status_id(client, "qualified")},
    )
    assert qualified.status_code == 200

    await sign_in(client, admin)
    report = await client.get("/api/v1/reports/pipeline", params={"campaign_id": campaign["id"]})
    assert report.status_code == 200, report.text
    body = report.json()
    kpis = body["kpis"]
    assert kpis["new_leads"]["value"] == 4 == len(leads)
    assert kpis["contacted"]["value"] == 1 and kpis["qualified"]["value"] == 1
    assert kpis["contact_rate"]["value"] == pytest.approx(0.25)
    assert kpis["won"]["value"] == 0 and kpis["unassigned"] == 0
    assert kpis["avg_response_seconds"]["value"] >= 0
    assert sum(day["count"] for day in body["daily"]) == 4
    assert len(body["daily"]) == 30  # default: last 30 days
    assert sum(s["count"] for s in body["by_status"]) == 4
    assert body["by_source"] == [{"key": "webhook", "label": "Website", "count": 4, "won": 0}]
    assert body["by_program"][0]["label"] == program["name"] and body["by_program"][0]["count"] == 4
    assert body["campaigns"][0]["campaign"]["id"] == campaign["id"]
    assert body["campaigns"][0]["contacted"] == 1

    board = {row["user"]["id"]: row for row in body["leaderboard"]}
    assert board[str(a1.id)]["leads"] == 2 and board[str(a2.id)]["leads"] == 2
    assert board[str(a1.id)]["qualified"] == 1 and board[str(a1.id)]["calls"] == 1
    assert body["leaderboard"][0]["user"]["id"] == str(a1.id)  # ranked by results

    by_owner = await client.get(
        "/api/v1/reports/pipeline", params={"campaign_id": campaign["id"], "owner_id": str(a2.id)}
    )
    assert by_owner.json()["kpis"]["new_leads"]["value"] == 2


async def test_each_role_sees_only_its_scope(client, make_user):
    a1, a2 = await make_user("agent"), await make_user("agent")
    await sign_in(client, await make_user("administrator"))
    campaign, _ = await campaign_with_leads(client, [a1, a2], 4)

    await sign_in(client, a1)
    report = (
        await client.get("/api/v1/reports/pipeline", params={"campaign_id": campaign["id"]})
    ).json()
    assert report["scope"] == "own"
    assert report["kpis"]["new_leads"]["value"] == 2
    assert [row["user"]["id"] for row in report["leaderboard"]] == [str(a1.id)]
    options = (await client.get("/api/v1/reports/filters")).json()
    assert [o["id"] for o in options["owners"]] == [str(a1.id)] and options["teams"] == []
    peek = await client.get(
        "/api/v1/reports/pipeline", params={"campaign_id": campaign["id"], "owner_id": str(a2.id)}
    )
    assert peek.json()["kpis"]["new_leads"]["value"] == 0  # filtering can't widen the scope


async def test_productivity_counts_activity(client, make_user):
    agent = await make_user("agent")
    await sign_in(client, await make_user("administrator"))
    campaign, _ = await campaign_with_leads(client, [agent], 2)
    first, second = await leads_of(client, campaign)

    await sign_in(client, agent)
    for lead, body in [
        (first, {"type": "call", "outcome": "answered"}),
        (first, {"type": "whatsapp", "notes": "Sent brochure"}),
        (second, {"type": "call", "outcome": "did_not_respond"}),
        (second, {"type": "meeting", "outcome": "done", "location": "Office"}),
    ]:
        logged = await client.post(f"/api/v1/leads/{lead['id']}/activities", json=body)
        assert logged.status_code == 201, logged.text
    task = await client.post(
        f"/api/v1/leads/{first['id']}/tasks",
        json={"type": "call_back", "due_at": "2030-01-01T10:00:00+04:00"},
    )
    done = await client.patch(f"/api/v1/tasks/{task.json()['id']}", json={"completed": True})
    assert done.status_code == 200

    await sign_in(client, await make_user("head_of_sales"))
    params = {"campaign_id": campaign["id"], "owner_id": str(agent.id)}
    report = (await client.get("/api/v1/reports/productivity", params=params)).json()
    k = report["kpis"]
    assert (k["calls"]["value"], k["answered"]["value"]) == (2, 1)
    assert k["answer_rate"]["value"] == pytest.approx(0.5)
    assert (k["meetings"]["value"], k["messages"]["value"]) == (1, 1)
    assert k["first_contacts"]["value"] == 2  # a call and a meeting each made first contact
    assert k["tasks_completed"]["value"] == 1
    assert sum(d["answered"] + d["not_answered"] for d in report["calls_daily"]) == 2
    assert {o["outcome"]: o["count"] for o in report["call_outcomes"]} == {
        "answered": 1,
        "did_not_respond": 1,
    }
    assert (
        sum(h["count"] for h in report["calls_by_hour"]) == 2 and len(report["calls_by_hour"]) == 24
    )
    [row] = report["agents"]
    assert row["user"]["id"] == str(agent.id) and row["calls"] == 2 and row["tasks_completed"] == 1


async def test_periods_previous_period_and_bad_ranges(client, make_user):
    await sign_in(client, await make_user("administrator"))
    campaign, _ = await campaign_with_leads(client, [], 1)
    today = date.today()
    past = await client.get(
        "/api/v1/reports/pipeline",
        params={
            "campaign_id": campaign["id"],
            "date_from": str(today - timedelta(days=60)),
            "date_to": str(today - timedelta(days=31)),
        },
    )
    assert past.json()["kpis"]["new_leads"] == {"value": 0, "previous": 0}
    assert past.json()["period"]["previous_to"] == str(today - timedelta(days=61))
    backwards = await client.get(
        "/api/v1/reports/pipeline", params={"date_from": "2026-11-10", "date_to": "2026-11-01"}
    )
    assert backwards.status_code == 422 and backwards.json()["code"] == "invalid_period"
    too_long = await client.get(
        "/api/v1/reports/productivity", params={"date_from": "2020-01-01", "date_to": "2026-01-01"}
    )
    assert too_long.status_code == 422


async def test_exports_need_the_export_right(client, make_user):
    await sign_in(client, await make_user("administrator"))
    campaign, _ = await campaign_with_leads(client, [], 2)
    tricky = await client.post(
        "/api/v1/leads",
        json={"first_name": "=HYPERLINK(1)", "phone": phone(), "campaign_id": campaign["id"]},
    )
    assert tricky.status_code == 201

    board = await client.get("/api/v1/reports/pipeline/export")
    assert board.status_code == 200
    assert board.headers["content-type"].startswith("text/csv")
    assert board.text.startswith("﻿Agent,New leads")
    assert "attachment" in board.headers["content-disposition"]
    work = await client.get("/api/v1/reports/productivity/export")
    assert work.status_code == 200 and work.text.startswith("﻿Person,Calls")

    leads = await client.get("/api/v1/leads/export", params={"campaign_id": campaign["id"]})
    assert leads.status_code == 200
    lines = leads.text.lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("Lead ID,First Name,Last Name,Phone Number")
    assert len(lines) == 4  # header + 3 leads
    assert "'=HYPERLINK(1)" in leads.text  # formulas are neutralised for Excel

    await sign_in(client, await make_user("sales_manager"))  # Pipeline: view only
    assert (await client.get("/api/v1/reports/pipeline/export")).status_code == 403
    assert (await client.get("/api/v1/reports/pipeline")).status_code == 200
    await sign_in(client, await make_user("agent"))  # leads: no export right
    assert (await client.get("/api/v1/leads/export")).status_code == 403
