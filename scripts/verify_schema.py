"""Read-only health check of the TEG CRM schema and seed data on Neon.

Usage (from the repo root):
    uv run python scripts/verify_schema.py

Prints table counts, seed counts, convention checks and the role/menu rights
grid. Makes no changes.
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

CHECKS = {
    "tables in public": """select count(*) from information_schema.tables
        where table_schema='public' and table_name <> 'alembic_version'""",
    "tables missing created_at/updated_at": """select count(*) from information_schema.tables t
        where table_schema='public' and table_name <> 'alembic_version' and (
          not exists (select 1 from information_schema.columns c where c.table_schema='public'
                      and c.table_name=t.table_name and column_name='created_at') or
          not exists (select 1 from information_schema.columns c where c.table_schema='public'
                      and c.table_name=t.table_name and column_name='updated_at'))""",
    "updated_at triggers": (
        "select count(*) from pg_trigger where tgname like 'trg\\_%\\_set_updated_at'"
    ),
    "FK/UQ names off-convention": """select count(*) from pg_constraint c
        join pg_namespace n on n.oid=c.connamespace
        where nspname='public' and contype in ('f','u') and conname !~ '^(fk|uq)_'""",
    "organizations": "select count(*) from organizations",
    "roles": "select count(*) from roles",
    "app_menus": "select count(*) from app_menus",
    "role_menu_permissions": "select count(*) from role_menu_permissions",
    "  granting view": "select count(*) from role_menu_permissions where can_view",
    "lead_statuses": "select count(*) from lead_statuses",
    "labels": "select count(*) from labels",
    "programs": "select count(*) from programs",
    "form_field_aliases": "select count(*) from form_field_aliases",
    "users": "select count(*) from users",
}

GRID = """
select p.menu_key, r.key,
  concat(case when can_view then 'V' end, case when can_create then 'C' end,
         case when can_edit then 'E' end, case when can_delete then 'D' end,
         case when can_export then 'X' end, case when can_assign then 'A' end)
from role_menu_permissions p
join roles r on r.id = p.role_id
join app_menus a on a.key = p.menu_key
order by a.sort_order, r.hierarchy_level desc
"""

ADMIN = """
select u.username, u.full_name, r.name, u.status, u.must_change_password,
       u.legacy_jaldi_id, left(u.password_hash, 9)
from users u join roles r on r.id = u.role_id
"""


def main() -> None:
    """Run all checks and print the results."""
    with psycopg.connect(os.environ["DATABASE_URL_UNPOOLED"]) as conn:
        print("server:", conn.execute("show server_version").fetchone()[0])
        for label, sql in CHECKS.items():
            print(f"{label:<38} {conn.execute(sql).fetchone()[0]}")

        roles = [
            "super_admin",
            "administrator",
            "head_of_sales",
            "marketing_manager",
            "sales_manager",
            "agent",
        ]
        grid: dict[str, dict[str, str]] = {}
        for menu, role, rights in conn.execute(GRID):
            grid.setdefault(menu, {})[role] = rights or "-"
        print("\n" + f"{'menu':<25}" + "".join(f"{r[:13]:<14}" for r in roles))
        for menu, cells in grid.items():
            print(f"{menu:<25}" + "".join(f"{cells.get(r, '?'):<14}" for r in roles))

        print("\nadmin:", conn.execute(ADMIN).fetchall())


if __name__ == "__main__":
    main()
