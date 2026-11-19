"""Phase 2 API: leads, contact rules, activities, tasks, bulk actions, dashboard.

Decisions under test (19 Nov 2026): contacted = call or meeting; no status change
before contact; history is never hidden on reassign; repeat enquiries link to a contact.
Run with ``RUN_DB_TESTS=1``; every test is rolled back (see conftest).
"""

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.live_db]

PASSWORD = "Correct-Horse-42"


async def sign_in(client, user):
    client.cookies.clear()
    response = await client.post(
        "/api/v1/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


async def add_lead(client, **fields):
    body = {"first_name": "Test", "last_name": "Lead", "phone": "0300 1234567", **fields}
    response = await client.post("/api/v1/leads", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def status_id(client, reference):
    statuses = (await client.get("/api/v1/settings/lead-statuses")).json()
    return next(s["id"] for s in statuses if s["reference"] == reference)


def iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


# --- Create, phone, contacts ---------------------------------------------------------------


async def test_manual_lead_starts_not_contacted_owned_by_creator(client, make_user):
    agent = await make_user("agent")
    await sign_in(client, agent)
    lead = await add_lead(client, phone="0300 1234567", email="Ayesha@Example.com")

    assert lead["status"]["reference"] == "not_contacted"
    assert lead["is_contacted"] is False
    assert lead["owner"]["id"] == str(agent.id)
    assert lead["phone"] == "+923001234567"  # normalised to E.164 (default region PK)
    assert lead["email"] == "ayesha@example.com"
    assert lead["source"] == "manual"


async def test_repeat_enquiry_is_linked_to_the_same_person(client, make_user):
    await sign_in(client, await make_user("administrator"))
    first = await add_lead(client, first_name="Bilal", phone="+923331112223")
    second = await add_lead(client, first_name="Bilal", phone="0333 1112223")

    assert second["is_duplicate"] is True
    detail = (await client.get(f"/api/v1/leads/{second['id']}")).json()
    assert [e["id"] for e in detail["other_enquiries"]] == [first["id"]]


async def test_agent_cannot_create_leads_for_someone_else(client, make_user):
    other = await make_user("agent")
    await sign_in(client, await make_user("agent"))
    response = await client.post(
        "/api/v1/leads",
        json={"first_name": "X", "phone": "03001112233", "owner_user_id": str(other.id)},
    )
    assert response.status_code == 403


async def test_lead_needs_phone_or_email(client, make_user):
    await sign_in(client, await make_user("agent"))
    response = await client.post("/api/v1/leads", json={"first_name": "No contact"})
    assert response.status_code == 422 and response.json()["code"] == "contact_required"


# --- Decision: contacted = call or meeting; no status change before ------------------------


async def test_status_is_locked_until_a_call_or_meeting(client, make_user):
    await sign_in(client, await make_user("agent"))
    lead = await add_lead(client)
    qualified = await status_id(client, "qualified")

    blocked = await client.patch(
        f"/api/v1/leads/{lead['id']}/status", json={"status_id": qualified}
    )
    assert blocked.status_code == 422 and blocked.json()["code"] == "not_contacted"

    for log in ({"type": "whatsapp", "notes": "sent brochure"}, {"type": "comment", "notes": "x"}):
        body = (await client.post(f"/api/v1/leads/{lead['id']}/activities", json=log)).json()
        assert body["is_contacted"] is False  # WhatsApp/comment don't count as contact

    no_outcome = await client.post(f"/api/v1/leads/{lead['id']}/activities", json={"type": "call"})
    assert no_outcome.status_code == 422

    called = await client.post(
        f"/api/v1/leads/{lead['id']}/activities",
        json={"type": "call", "outcome": "did_not_respond", "notes": "no answer"},
    )
    assert called.status_code == 201
    detail = called.json()
    assert detail["is_contacted"] is True and detail["calls_made"] == 1
    assert detail["response_time_seconds"] is not None
    assert detail["last_activity_note"].startswith("Call (did not respond)")

    allowed = await client.patch(
        f"/api/v1/leads/{lead['id']}/status", json={"status_id": qualified}
    )
    assert allowed.status_code == 200 and allowed.json()["status"]["reference"] == "qualified"


async def test_meeting_counts_as_contact_and_can_create_follow_up(client, make_user):
    await sign_in(client, await make_user("agent"))
    lead = await add_lead(client)
    response = await client.post(
        f"/api/v1/leads/{lead['id']}/activities",
        json={
            "type": "meeting",
            "outcome": "done",
            "location": "Dubai office",
            "follow_up": {"type": "send_email", "due_at": iso(timedelta(days=1))},
        },
    )
    assert response.status_code == 201
    assert response.json()["is_contacted"] is True
    assert response.json()["task_status"] == "due"


# --- Scope, reassignment, history -------------------------------------------------------------


async def test_agents_see_only_their_own_leads(client, make_user):
    alice, bob = await make_user("agent"), await make_user("agent")
    await sign_in(client, alice)
    alice_lead = await add_lead(client, first_name="AliceLead", phone="03004440001")
    await sign_in(client, bob)
    assert (await client.get(f"/api/v1/leads/{alice_lead['id']}")).status_code == 404
    names = [lead["first_name"] for lead in (await client.get("/api/v1/leads")).json()["items"]]
    assert "AliceLead" not in names


async def test_reassign_keeps_full_history_and_moves_open_tasks(client, make_user):
    manager = await make_user("sales_manager")
    agent_a, agent_b = await make_user("agent"), await make_user("agent")
    await sign_in(client, await make_user("super_admin"))
    await client.post(
        "/api/v1/teams",
        json={"name": "Scope Team X2", "members": [
            {"user_id": str(manager.id), "team_role": "manager"},
            {"user_id": str(agent_a.id)}, {"user_id": str(agent_b.id)},
        ]},
    )  # fmt: skip

    await sign_in(client, agent_a)
    lead = await add_lead(client)
    await client.post(
        f"/api/v1/leads/{lead['id']}/activities",
        json={"type": "call", "outcome": "answered", "notes": "keen on D2"},
    )
    await client.post(
        f"/api/v1/leads/{lead['id']}/tasks",
        json={"type": "call_back", "due_at": iso(timedelta(hours=3))},
    )
    denied = await client.patch(
        f"/api/v1/leads/{lead['id']}/owner", json={"owner_user_id": str(agent_b.id)}
    )
    assert denied.status_code == 403  # agents have no Assign right

    await sign_in(client, manager)
    moved = await client.patch(
        f"/api/v1/leads/{lead['id']}/owner", json={"owner_user_id": str(agent_b.id)}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["times_assigned"] == 2

    await sign_in(client, agent_b)
    timeline = (await client.get(f"/api/v1/leads/{lead['id']}/activities")).json()
    assert any(
        a["type"] == "call" and a["notes"] == "keen on D2" for a in timeline
    )  # nothing hidden
    tasks = (await client.get("/api/v1/tasks", params={"view": "today"})).json()["items"]
    assert [t["lead"]["id"] for t in tasks] == [lead["id"]]  # open task followed the lead


async def test_sales_manager_cannot_assign_outside_their_team(client, make_user):
    manager, outsider = await make_user("sales_manager"), await make_user("agent")
    await sign_in(client, manager)
    lead = await add_lead(client)
    response = await client.patch(
        f"/api/v1/leads/{lead['id']}/owner", json={"owner_user_id": str(outsider.id)}
    )
    assert response.status_code == 422 and response.json()["code"] == "invalid_owner"


# --- Labels, edit, tasks ----------------------------------------------------------------------


async def test_labels_edit_and_timeline(client, make_user):
    await sign_in(client, await make_user("agent"))
    lead = await add_lead(client)
    labels = (await client.get("/api/v1/settings/labels")).json()
    eligible = next(label["id"] for label in labels if label["name"] == "Eligible")

    labelled = await client.put(
        f"/api/v1/leads/{lead['id']}/labels", json={"label_ids": [eligible]}
    )
    assert [label["name"] for label in labelled.json()["labels"]] == ["Eligible"]
    edited = await client.patch(
        f"/api/v1/leads/{lead['id']}", json={"city": "Lahore", "budget_text": "PKR 20-30 lakh"}
    )
    assert edited.json()["city"] == "Lahore"
    types = [a["type"] for a in (await client.get(f"/api/v1/leads/{lead['id']}/activities")).json()]
    assert {"label_change", "edit", "lead_added", "assigned"} <= set(types)


async def test_task_lifecycle_drives_task_status(client, make_user):
    await sign_in(client, await make_user("agent"))
    lead = await add_lead(client)
    task = (
        await client.post(
            f"/api/v1/leads/{lead['id']}/tasks",
            json={"type": "call_back", "due_at": iso(timedelta(hours=-2)), "notes": "call back"},
        )
    ).json()
    assert task["is_overdue"] is True
    assert (await client.get(f"/api/v1/leads/{lead['id']}")).json()["task_status"] == "overdue"
    overdue = (await client.get("/api/v1/tasks", params={"view": "overdue"})).json()
    assert overdue["total"] == 1

    done = await client.patch(f"/api/v1/tasks/{task['id']}", json={"completed": True})
    assert done.status_code == 200 and done.json()["completed_at"] is not None
    assert (await client.get(f"/api/v1/leads/{lead['id']}")).json()["task_status"] == "none"
    naive = await client.post(
        f"/api/v1/leads/{lead['id']}/tasks",
        json={"type": "meeting", "due_at": "2026-12-01T10:00:00"},
    )
    assert naive.status_code == 422


# --- Bulk, filters, delete --------------------------------------------------------------------


async def test_bulk_status_skips_uncontacted_and_bulk_labels(client, make_user):
    await sign_in(client, await make_user("administrator"))
    contacted = await add_lead(client, phone="03005550001")
    fresh = await add_lead(client, phone="03005550002")
    await client.post(
        f"/api/v1/leads/{contacted['id']}/activities", json={"type": "call", "outcome": "answered"}
    )
    result = await client.post(
        "/api/v1/leads/bulk/status",
        json={
            "lead_ids": [contacted["id"], fresh["id"]],
            "status_id": await status_id(client, "working_deal"),
        },
    )
    assert result.json()["updated"] == 1 and result.json()["skipped"] == 1

    cold = next(
        x["id"] for x in (await client.get("/api/v1/settings/labels")).json() if x["name"] == "Cold"
    )
    added = await client.post(
        "/api/v1/leads/bulk/labels",
        json={"lead_ids": [contacted["id"], fresh["id"]], "label_ids": [cold], "mode": "add"},
    )
    assert added.json()["updated"] == 2
    filtered = (await client.get("/api/v1/leads", params={"label_id": cold})).json()
    assert {x["id"] for x in filtered["items"]} == {contacted["id"], fresh["id"]}


async def test_filters_search_and_delete(client, make_user):
    await sign_in(client, await make_user("administrator"))
    lead = await add_lead(client, first_name="Zubair", phone="0321 9876543")
    by_phone = (await client.get("/api/v1/leads", params={"search": "9876543"})).json()
    assert [x["id"] for x in by_phone["items"]] == [lead["id"]]
    by_name = (await client.get("/api/v1/leads", params={"search": "zubair"})).json()
    assert by_name["total"] >= 1
    not_contacted = (
        await client.get("/api/v1/leads", params={"contacted": "false", "search": "zubair"})
    ).json()
    assert not_contacted["total"] >= 1

    assert (await client.delete(f"/api/v1/leads/{lead['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/leads/{lead['id']}")).status_code == 404


async def test_agent_cannot_delete_or_bulk_reassign(client, make_user):
    await sign_in(client, await make_user("agent"))
    lead = await add_lead(client)
    assert (await client.delete(f"/api/v1/leads/{lead['id']}")).status_code == 403
    response = await client.post(
        "/api/v1/leads/bulk/reassign", json={"lead_ids": [lead["id"]], "owner_user_id": None}
    )
    assert response.status_code == 403


# --- Dashboard --------------------------------------------------------------------------------


async def test_dashboard_summary_counts_in_scope(client, make_user):
    agent = await make_user("agent")
    await sign_in(client, agent)
    lead = await add_lead(client)
    await client.post(
        f"/api/v1/leads/{lead['id']}/activities", json={"type": "call", "outcome": "answered"}
    )
    await client.post(
        f"/api/v1/leads/{lead['id']}/tasks",
        json={"type": "call_back", "due_at": iso(timedelta(minutes=-5))},
    )
    summary = (await client.get("/api/v1/dashboard/summary")).json()
    assert summary["scope"] == "own" and summary["timezone"] == "Asia/Dubai"
    cards = summary["cards"]
    assert cards["new_today"] == 1 and cards["calls_today"] == 1 and cards["tasks_overdue"] == 1
    assert cards["not_contacted"] == 0
    assert summary["follow_ups"][0]["lead"]["id"] == lead["id"]
    assert summary["recent_activity"][0]["type"] in {"task_created", "call"}
