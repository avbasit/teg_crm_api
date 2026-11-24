"""Phase 1 API: authentication, users, roles & permissions, teams, settings, audit log.

Run against Neon with ``RUN_DB_TESTS=1``; every test is rolled back (see conftest).
"""

import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.live_db]

PASSWORD = "Correct-Horse-42"


async def sign_in(client, user, password=PASSWORD):
    response = await client.post(
        "/api/v1/auth/login", json={"username": user.username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def role_id(client, key):
    roles = (await client.get("/api/v1/roles")).json()
    return next(r["id"] for r in roles if r["key"] == key)


# --- Authentication ---------------------------------------------------------------------


async def test_login_sets_session_and_returns_rights_and_menus(client, make_user):
    admin = await make_user("super_admin")
    body = await sign_in(client, admin)

    assert body["user"]["role"]["key"] == "super_admin"
    assert body["permissions"]["users"]["create"] is True
    top_level = [m["key"] for m in body["menus"]]
    expected = {"dashboard", "leads", "tasks", "campaigns", "users", "teams", "settings", "account"}
    assert expected <= set(top_level)
    settings = next(m for m in body["menus"] if m["key"] == "settings")
    children = {c["key"] for c in settings["children"]}
    assert "settings.integrations" not in children  # screens from later phases stay hidden
    assert "teg_session" in client.cookies

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == admin.username


async def test_agent_sees_only_their_work_and_account_menus(client, make_user):
    agent = await make_user("agent")
    body = await sign_in(client, agent)
    assert [m["key"] for m in body["menus"]] == ["dashboard", "leads", "tasks", "account"]
    assert (await client.get("/api/v1/users")).status_code == 403


async def test_wrong_password_is_rejected_without_revealing_the_account(client, make_user):
    user = await make_user("agent")
    wrong = await client.post(
        "/api/v1/auth/login", json={"username": user.username, "password": "nope"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"username": "no_such_user_x", "password": "nope"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["code"] == "invalid_credentials"


async def test_five_failures_lock_the_account(client, make_user):
    user = await make_user("agent")
    for _ in range(5):
        await client.post("/api/v1/auth/login", json={"username": user.username, "password": "x"})
    locked = await client.post(
        "/api/v1/auth/login", json={"username": user.username, "password": PASSWORD}
    )
    assert locked.status_code == 423
    assert locked.json()["code"] == "account_locked"


async def test_requests_without_a_session_are_401(client):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


async def test_forced_password_change_blocks_everything_else_until_done(client, make_user):
    user = await make_user("administrator", must_change_password=True)
    body = await sign_in(client, user)
    assert body["user"]["must_change_password"] is True

    blocked = await client.get("/api/v1/users")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "password_change_required"

    weak = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "short"},
    )
    assert weak.status_code == 422 and weak.json()["code"] == "weak_password"

    wrong_current = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "bad", "new_password": "A-much-better-pass-9"},
    )
    assert wrong_current.json()["code"] == "wrong_current_password"

    changed = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "A-much-better-pass-9"},
    )
    assert changed.status_code == 204
    assert (await client.get("/api/v1/users")).status_code == 200


async def test_invited_user_becomes_active_after_setting_own_password(client, make_user):
    invited = await make_user("agent", must_change_password=True, status="invited")
    body = await sign_in(client, invited)
    assert body["user"]["must_change_password"] is True

    changed = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "My-own-password-7"},
    )
    assert changed.status_code == 204
    client.cookies.clear()

    await sign_in(client, await make_user("administrator"))
    user = (await client.get(f"/api/v1/users/{invited.id}")).json()
    assert user["status"] == "active" and user["must_change_password"] is False


async def test_logout_ends_the_session(client, make_user):
    await sign_in(client, await make_user("agent"))
    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_form_posts_are_rejected_as_csrf_protection(client, make_user):
    user = await make_user("agent")
    response = await client.post(
        "/api/v1/auth/login", data={"username": user.username, "password": PASSWORD}
    )
    assert response.status_code == 415
    assert response.json()["code"] == "json_required"


# --- Users ------------------------------------------------------------------------------


async def test_admin_creates_user_who_must_change_password(client, make_user):
    await sign_in(client, await make_user("administrator"))
    created = await client.post(
        "/api/v1/users",
        json={
            "username": "new.agent.x1",
            "full_name": "New Agent",
            "email": "new.agent.x1@example.com",
            "role_id": await role_id(client, "agent"),
            "temporary_password": "Temporary-Pass-1",
        },
    )
    assert created.status_code == 201, created.text
    user = created.json()
    assert user["status"] == "invited" and user["must_change_password"] is True

    duplicate = await client.post(
        "/api/v1/users",
        json={
            "username": "NEW.agent.x1",
            "full_name": "Copy",
            "role_id": await role_id(client, "agent"),
            "temporary_password": "Temporary-Pass-1",
        },
    )
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "username_taken"

    audit = await client.get(
        "/api/v1/audit-logs", params={"entity_type": "user", "entity_id": user["id"]}
    )
    # Administrators can view the audit log; the creation is recorded.
    assert audit.status_code == 200
    assert audit.json()["items"][0]["action"] == "create"


async def test_admin_cannot_grant_or_manage_super_admin(client, make_user):
    super_admin = await make_user("super_admin")
    await sign_in(client, await make_user("administrator"))

    grant = await client.post(
        "/api/v1/users",
        json={
            "username": "sneaky.x1",
            "full_name": "Sneaky",
            "role_id": await role_id(client, "super_admin"),
            "temporary_password": "Temporary-Pass-1",
        },
    )
    assert grant.status_code == 403

    edit = await client.patch(f"/api/v1/users/{super_admin.id}", json={"full_name": "Hacked"})
    assert edit.status_code == 403
    pause = await client.post(f"/api/v1/users/{super_admin.id}/pause", json={})
    assert pause.status_code == 403


async def test_manager_must_be_more_senior(client, make_user):
    agent_boss = await make_user("agent")
    await sign_in(client, await make_user("super_admin"))
    response = await client.post(
        "/api/v1/users",
        json={
            "username": "junior.x1",
            "full_name": "Junior",
            "role_id": await role_id(client, "agent"),
            "reports_to_user_id": str(agent_boss.id),
            "temporary_password": "Temporary-Pass-1",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_manager"


async def test_pausing_a_user_signs_them_out(client, make_user):
    agent = await make_user("agent")
    admin = await make_user("administrator")

    await sign_in(client, agent)
    agent_cookie = client.cookies.get("teg_session")
    client.cookies.clear()

    await sign_in(client, admin)
    assert (await client.post(f"/api/v1/users/{agent.id}/pause", json={})).status_code == 200

    client.cookies.clear()
    client.cookies.set("teg_session", agent_cookie)
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_sales_manager_sees_only_their_team(client, make_user):
    manager = await make_user("sales_manager")
    in_team = await make_user("agent")
    outside = await make_user("agent")

    await sign_in(client, await make_user("super_admin"))
    team = await client.post(
        "/api/v1/teams",
        json={
            "name": "Lahore Desk X1",
            "members": [
                {"user_id": str(manager.id), "team_role": "manager"},
                {"user_id": str(in_team.id), "team_role": "member"},
            ],
        },
    )
    assert team.status_code == 201, team.text
    client.cookies.clear()

    await sign_in(client, manager)
    ids = {
        u["id"] for u in (await client.get("/api/v1/users", params={"limit": 200})).json()["items"]
    }
    assert str(in_team.id) in ids and str(manager.id) in ids
    assert str(outside.id) not in ids
    assert (await client.get(f"/api/v1/users/{outside.id}")).status_code == 404
    teams = (await client.get("/api/v1/teams")).json()
    assert [t["name"] for t in teams] == ["Lahore Desk X1"]


# --- Roles & permissions ----------------------------------------------------------------


async def test_rights_grid_rules(client, make_user):
    await sign_in(client, await make_user("super_admin"))
    super_admin_role = await role_id(client, "super_admin")
    agent_role = await role_id(client, "agent")

    grid = (await client.get(f"/api/v1/roles/{agent_role}/permissions")).json()
    assert grid["editable"] is True
    assert {row["menu_key"] for row in grid["permissions"]} >= {"leads.all", "users", "account"}

    locked = await client.put(
        f"/api/v1/roles/{super_admin_role}/permissions",
        json={"permissions": [{"menu_key": "users", "view": False}]},
    )
    assert locked.status_code == 403

    no_view = await client.put(
        f"/api/v1/roles/{agent_role}/permissions",
        json={"permissions": [{"menu_key": "campaigns", "edit": True}]},
    )
    assert no_view.status_code == 422 and no_view.json()["code"] == "view_required"

    granted = await client.put(
        f"/api/v1/roles/{agent_role}/permissions",
        json={"permissions": [{"menu_key": "campaigns", "view": True}]},
    )
    assert granted.status_code == 200
    row = next(r for r in granted.json()["permissions"] if r["menu_key"] == "campaigns")
    assert row["view"] is True


async def test_administrator_can_view_but_not_edit_rights(client, make_user):
    await sign_in(client, await make_user("administrator"))
    agent_role = await role_id(client, "agent")
    assert (await client.get(f"/api/v1/roles/{agent_role}/permissions")).status_code == 200
    edit = await client.put(
        f"/api/v1/roles/{agent_role}/permissions",
        json={"permissions": [{"menu_key": "campaigns", "view": True}]},
    )
    assert edit.status_code == 403


# --- Settings ---------------------------------------------------------------------------


async def test_customizations(client, make_user):
    await sign_in(client, await make_user("administrator"))

    label = await client.post(
        "/api/v1/settings/labels", json={"name": "VIP X1", "color": "#125FCD"}
    )
    assert label.status_code == 201
    duplicate = await client.post(
        "/api/v1/settings/labels", json={"name": "vip x1", "color": "#000000"}
    )
    assert duplicate.status_code == 409

    statuses = (await client.get("/api/v1/settings/lead-statuses")).json()
    assert statuses[0]["reference"] == "not_contacted"
    reversed_ids = [s["id"] for s in reversed(statuses)]
    reordered = await client.put("/api/v1/settings/lead-statuses/order", json={"ids": reversed_ids})
    assert reordered.status_code == 200
    assert reordered.json()[0]["id"] == reversed_ids[0]
    partial = await client.put(
        "/api/v1/settings/lead-statuses/order", json={"ids": reversed_ids[:2]}
    )
    assert partial.status_code == 422

    system_status = statuses[0]["id"]
    deactivate = await client.patch(
        f"/api/v1/settings/lead-statuses/{system_status}", json={"is_active": False}
    )
    assert deactivate.status_code == 409

    bad_tz = await client.patch("/api/v1/settings/organization", json={"timezone": "Mars/Base"})
    assert bad_tz.status_code == 422 and bad_tz.json()["code"] == "invalid_timezone"
    ok = await client.patch("/api/v1/settings/organization", json={"phone": "+92 300 0000000"})
    assert ok.status_code == 200 and ok.json()["phone"] == "+92 300 0000000"


async def test_agents_can_read_lookups_but_not_change_them(client, make_user):
    await sign_in(client, await make_user("agent"))
    assert (await client.get("/api/v1/settings/labels")).status_code == 200
    assert (await client.get("/api/v1/settings/lead-statuses")).status_code == 200
    change = await client.post("/api/v1/settings/labels", json={"name": "Nope", "color": "#000000"})
    assert change.status_code == 403
    assert (await client.get("/api/v1/settings/organization")).status_code == 403


# --- Phase 1 acceptance: each role sees exactly the screens in the rights table ----------

# Screens built so far (is_available) a role can VIEW, per docs/roles-and-permissions.md §4.
# Every role gets the Overview, My Tasks and My Account (Phases 1-2).
COMMON = {
    "dashboard": ["dashboard.pipeline", "dashboard.productivity"],
    "tasks": [],
    "account": [],
}
ADMIN_SETTINGS = [
    "settings.organization",
    "settings.customizations",
    "settings.roles",
    "settings.duplicates",
    "settings.audit_log",
]
ALL_LEADS = ["leads.all", "leads.unassigned", "leads.duplicates", "leads.import"]

EXPECTED_MENUS = {
    "super_admin": {
        "leads": ALL_LEADS,
        "campaigns": [],
        "users": [],
        "teams": [],
        "settings": ADMIN_SETTINGS,
    },
    "administrator": {
        "leads": ALL_LEADS,
        "campaigns": [],
        "users": [],
        "teams": [],
        "settings": ADMIN_SETTINGS,
    },
    "head_of_sales": {
        "leads": ALL_LEADS,
        "campaigns": [],
        "users": [],
        "teams": [],
        "settings": ["settings.customizations", "settings.duplicates"],
    },
    "marketing_manager": {
        "leads": ["leads.all", "leads.duplicates", "leads.import"],
        "campaigns": [],
        "settings": ["settings.customizations"],
    },
    "sales_manager": {
        "leads": ["leads.all", "leads.unassigned", "leads.duplicates"],
        "campaigns": [],
        "users": [],
        "teams": [],
    },
    "agent": {"leads": ["leads.all"]},
}


@pytest.mark.parametrize("role_key", list(EXPECTED_MENUS))
async def test_each_role_sees_exactly_its_menus(client, make_user, role_key):
    body = await sign_in(client, await make_user(role_key))
    menus = {m["key"]: [c["key"] for c in m["children"]] for m in body["menus"]}
    assert menus == {**COMMON, **EXPECTED_MENUS[role_key]}


# --- My Account, rules, counts ----------------------------------------------------------


async def test_my_account_profile_update(client, make_user):
    user = await make_user("agent")
    await sign_in(client, user)
    updated = await client.patch(
        "/api/v1/auth/me", json={"full_name": "  Ayesha Khan ", "phone": "+971 50 000 0000"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["user"]["full_name"] == "Ayesha Khan"
    assert updated.json()["user"]["phone"] == "+971 50 000 0000"
    empty = await client.patch("/api/v1/auth/me", json={"full_name": " "})
    assert empty.status_code == 422


async def test_organization_rules_need_the_right_screen(client, make_user):
    await sign_in(client, await make_user("administrator"))
    rules = (await client.get("/api/v1/settings/rules")).json()
    assert "qualified" in rules["mql_status_references"]

    deal = await client.patch(
        "/api/v1/settings/rules", json={"deal_value": {"is_active": False, "currency": "AED"}}
    )
    assert deal.status_code == 200
    assert deal.json()["deal_value"] == {"is_active": False, "currency": "AED"}

    # Administrators only VIEW Roles & Permissions, which owns the hide-history switch.
    hide = await client.patch("/api/v1/settings/rules", json={"hide_history_on_reassign": True})
    assert hide.status_code == 403

    unknown = await client.patch(
        "/api/v1/settings/rules", json={"mql_status_references": ["qualified", "nope"]}
    )
    assert unknown.status_code == 422
    client.cookies.clear()

    await sign_in(client, await make_user("super_admin"))
    hide = await client.patch("/api/v1/settings/rules", json={"hide_history_on_reassign": True})
    assert hide.status_code == 200 and hide.json()["hide_history_on_reassign"] is True


async def test_user_counts_and_teams_in_listing(client, make_user):
    member = await make_user("agent")
    await make_user("agent", status="invited", must_change_password=True)
    await sign_in(client, await make_user("super_admin"))

    counts = (await client.get("/api/v1/users/counts")).json()
    assert counts["invited"] >= 1 and counts["active"] >= 2

    await client.post(
        "/api/v1/teams",
        json={"name": "Dubai Desk X1", "members": [{"user_id": str(member.id)}]},
    )
    user = (await client.get(f"/api/v1/users/{member.id}")).json()
    assert [t["name"] for t in user["teams"]] == ["Dubai Desk X1"]
