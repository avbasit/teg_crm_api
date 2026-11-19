# TEG CRM API

Back end for TEG CRM, TEG Consultancy's replacement for the Jaldi CRM. Python 3.13 + FastAPI on Vercel, with a Neon Postgres 18 database. This repository holds the API and the database migrations. The web front end is in [avbasit/teg_crm](https://github.com/avbasit/teg_crm), along with the project-wide design docs:

| Doc (in teg_crm) | What it covers |
|---|---|
| [project-phases.md](https://github.com/avbasit/teg_crm/blob/main/docs/project-phases.md) | Phase plan, stack, decisions |
| [database-design.md](https://github.com/avbasit/teg_crm/blob/main/docs/database-design.md) | Schema design (v3) |
| [roles-and-permissions.md](https://github.com/avbasit/teg_crm/blob/main/docs/roles-and-permissions.md) | Roles, hierarchy, menu rights |
| [jaldi-export-analysis.md](https://github.com/avbasit/teg_crm/blob/main/docs/jaldi-export-analysis.md) | Jaldi export and migration rules |

## Layout

```
app/
  main.py                            App factory; every route is under /api/v1
  core/config.py                     Settings from env / .env (pydantic-settings)
  core/database.py                   Async SQLAlchemy + asyncpg for Neon's pooler; `DbSession` dependency
  core/security.py                   argon2id passwords, session tokens (hash-only storage)
  core/errors.py                     AppError -> {"detail", "code"} responses
  dependencies.py                    CurrentUserDep, SessionUserDep, RequirePermission(menu, action)
  models/                            ORM models (mirror the SQL schema, never create it)
  schemas/                           Pydantic request/response models: the API contract
  repositories/                      All SQL queries; routers never query directly
  services/                          Business rules (auth, access/scope, users, roles, teams, settings)
  routers/                           One module per area: auth, users, roles, teams, settings, audit_logs
api/index.py                         Vercel entry point (vercel.json routes every path here)
migrations/
  sql/0001_initial_schema.sql        DDL - source of truth for the schema (30 tables)
  sql/0002_seed_reference_data.sql   Org, roles, menus, rights, statuses, labels, programmes
  versions/                          Alembic revisions that run the SQL files
scripts/
  split_csv.py                       Split large CSV exports into <=N MB parts on whole records
  verify_schema.py                   Read-only check of schema, seed data and the rights grid
tests/                               pytest; `live_db` tests run only with RUN_DB_TESTS=1
```

## Endpoints

All under `/api/v1`. The full contract is at `/api/v1/docs` (hidden when `APP_ENV=production`) and `/api/v1/openapi.json`.

**Auth** is a session cookie (`teg_session`) set by sign-in; see [ADR 0003](https://github.com/avbasit/teg_crm/blob/main/docs/adr/0003-server-side-sessions.md). Errors return `{"detail", "code"}`. POST requests must be JSON (CSRF guard, otherwise 415); PUT, PATCH and DELETE from another site already need a CORS preflight. Cross-origin requests are allowed only to `/intake/*`, which never uses the session cookie.

| Area | Endpoints | Right needed (menu · action) |
|---|---|---|
| System | `GET /health`, `GET /organization` | none |
| Auth | `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password` | signed in (allowed during a forced password change) |
| My Account | `PATCH /auth/me` (own name, email, phone) | signed in |
| Users | `GET /users` (status, role, search, paging; each user lists their teams), `GET /users/counts`, `POST /users`, `GET/PATCH /users/{id}`, `POST /users/{id}/pause\|activate\|archive\|reset-password` | `users` · view/create/edit/delete |
| Roles | `GET /roles`, `GET/PUT /roles/{id}/permissions` | `users` or `settings.roles` · view; `settings.roles` · edit |
| Teams | `GET/POST /teams`, `GET/PATCH/DELETE /teams/{id}`, `PUT /teams/{id}/members` | `teams` · view/create/edit/delete |
| Organisation | `GET/PATCH /settings/organization` | `settings.organization` · view/edit |
| Rules | `GET /settings/rules`, `PATCH /settings/rules` (hide history on reassign, deal value field, MQL statuses) | reading: signed in · hide-history: `settings.roles` edit · others: `settings.customizations` edit |
| Customizations | `GET/POST /settings/lead-statuses`, `PATCH /settings/lead-statuses/{id}`, `PUT /settings/lead-statuses/order`, `GET/POST /settings/labels`, `PATCH/DELETE /settings/labels/{id}`, `GET/POST /settings/programs`, `PATCH /settings/programs/{id}` | reading: signed in · changing: `settings.customizations` |
| Audit | `GET /audit-logs` (entity, actor, paging) | `settings.audit_log` · view |
| Leads | `GET /leads` (search, status, label, campaign, owner incl. `unassigned`, source, task status, dates, calls, duplicates, contacted, sort, paging), `GET /leads/owners`, `GET /leads/campaigns`, `POST /leads`, `GET/PATCH/DELETE /leads/{id}`, `PATCH /leads/{id}/status`, `PUT /leads/{id}/labels`, `GET/POST /leads/{id}/activities`, `GET/POST /leads/{id}/tasks` | `leads.all` · view/create/edit/delete |
| Lead owner | `PATCH /leads/{id}/owner`, `POST /leads/bulk/reassign` | `leads.all` · assign |
| Bulk | `POST /leads/bulk/status`, `POST /leads/bulk/labels`, `POST /leads/bulk/delete` | `leads.all` · edit / delete |
| Tasks | `GET /tasks` (`view` = overdue/today/upcoming/completed, `mine`), `PATCH /tasks/{id}` | `tasks` · view; `leads.all` · edit |
| Dashboard | `GET /dashboard/summary` (cards, follow-ups, recent activity, Gulf-time day) | `dashboard.pipeline` · view |
| Campaigns | `GET /campaigns` (status tab, search, paging), `GET /campaigns/counts`, `GET /campaigns/agent-options`, `POST /campaigns`, `GET/PATCH/DELETE /campaigns/{id}`, `PATCH /campaigns/{id}/status` | `campaigns` · view/create/edit/delete |
| Round-robin | `PUT /campaigns/{id}/agents`, `PATCH /campaigns/{id}/agents/{user_id}` (on/off) | `campaigns` · assign |
| Website keys | `GET/POST /campaigns/{id}/api-keys`, `DELETE /campaigns/{id}/api-keys/{key_id}` (revoke; the secret is returned once) | `campaigns` · edit |
| Website intake | `POST /intake/leads` with header `X-Api-Key` (CORS open for this path only) | a campaign's website key, no session |
| Imports | `POST /imports`, `POST /imports/{id}/rows` (≤ 250 mapped rows per call), `POST /imports/{id}/complete`, `GET /imports`, `GET /imports/{id}`, `GET /imports/{id}/rows` (problem rows) | `leads.import` · view/create |
| Markets | `GET /settings/markets`, `POST /settings/markets`, `PATCH /settings/markets/{id}` | reading: signed in · changing: `settings.customizations` |
| Duplicate rules | `GET/PATCH /settings/duplicates` | `settings.duplicates` · view/edit |

Rules enforced by the API (and tested):
- New users start **invited** with a temporary password and become **active** once they set their own.
- You can only manage users whose role is below yours (Super Admin manages everyone), and grant only roles below your own.
- A manager must have a more senior role than the people reporting to them.
- The last active Super Admin can't be paused, archived or demoted. The Super Admin role's rights are locked.
- Data scope: Sales Managers see only their teams' users and teams.
- Pausing, archiving or resetting a password signs the user out everywhere.
- Every change is written to `audit_logs`; every sign-in attempt to `auth_events`.
- Leads: only a **call or meeting** counts as contact. The first one sets the response time, and the status stays locked until it happens (bulk status changes skip leads not yet contacted).
- Reassigning a lead moves its open tasks to the new owner. History is never hidden.
- Each lead is linked to a contact by normalised phone (E.164, Pakistan by default), so earlier enquiries by the same person appear on the lead and repeats are flagged as duplicates.
- Lead lists, tasks and the dashboard respect the role's data scope (own, team, reporting line, all). Unassigned leads are visible to everyone with the Assign right, so managers can hand them out.
- Campaign leads (website, imports): a repeat enquiry goes to the person's earlier owner if that user is active, otherwise the round-robin (equal turns; agents switched off or not active are skipped). With nobody available, or a manual campaign, the lead is left unassigned. Paused campaigns still take leads; archived ones refuse them (410).
- Duplicate rules (Jaldi's defaults): an import skips a person already in the same campaign; manual and website duplicates are kept and flagged, with a "duplicate detected" entry on the timeline.
- Website submissions are stored in `webhook_events` before processing. A key can be limited to named sites (`Origin`). A filled `_gotcha` field is treated as spam and dropped silently. A repeated `external_id` is accepted only once.

## Setup and run (Windows)

Needs Python 3.13 and uv (`winget install Python.Python.3.13 astral-sh.uv`).

```powershell
uv sync
Copy-Item .env.example .env       # then fill in the Neon URLs (git-ignored)
uv run fastapi dev app/main.py    # http://localhost:8000/api/v1/docs
```

`DATABASE_URL` is the pooled URL, used by the API. `DATABASE_URL_UNPOOLED` is the same host without `-pooler` and is used by migrations.

| Command | Does |
|---|---|
| `uv run pytest -q` | Unit tests (no database needed) |
| `$env:RUN_DB_TESTS='1'; uv run pytest -q` | Also run the live tests against Neon. Each test runs in a transaction that is **rolled back**, so nothing is saved |
| `uv run ruff check .` / `uv run ruff format .` | Lint / format |
| `uv export --no-dev --no-hashes --no-emit-project --format requirements-txt -o requirements.txt` | Refresh `requirements.txt` after changing dependencies (Vercel installs from it; CI checks it matches `uv.lock`) |

Deployment (Vercel project, Neon integration, migrate workflow): see [deployment.md](https://github.com/avbasit/teg_crm/blob/main/docs/deployment.md).

## Database

```powershell
uv run alembic current                   # 0002 (head) on Neon since 15 Nov 2026
uv run alembic upgrade head              # apply new revisions
uv run python scripts/verify_schema.py   # read-only health check
```

To create the first database from scratch, the admin seed needs a temporary password for that single run:

```powershell
$env:SEED_ADMIN_PASSWORD = '...'; uv run alembic upgrade head; Remove-Item Env:SEED_ADMIN_PASSWORD
```

The first admin is `basit` (Super Admin) and has to change the password at first login.

New schema changes go in a new revision (`uv run alembic revision -m "..."`). Never edit 0001 or 0002 now that they've been applied.

## Conventions

- Python: snake_case modules, functions and variables; PascalCase classes; Google-style docstrings; Ruff (`uv run ruff check .`, `uv run ruff format .`).
- Database: see [database-design.md §1](https://github.com/avbasit/teg_crm/blob/main/docs/database-design.md#1-conventions). Every table has `created_at` and `updated_at`.
- API: `/api/v1/...`, plural nouns, kebab-case paths, snake_case JSON.
- Git: Conventional Commits; short-lived branches off `main`.

Jaldi exports contain customer PII. CSV and Excel files are git-ignored repo-wide. Never commit them.
