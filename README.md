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
  models/                            ORM models (mirror the SQL schema, never create it)
  schemas/                           Pydantic request/response models: the API contract
  repositories/                      All SQL queries; routers never query directly
  routers/                           One module per area: health, organization, ...
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

## Endpoints (Phase 0)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | API version plus a database round trip (schema revision, latency). Returns 503 if Neon is unreachable |
| GET | `/api/v1/organization` | Public organisation details (name, logo, timezone, currency) |
| GET | `/api/v1/openapi.json` | OpenAPI schema; the web app generates its TypeScript types from it |
| GET | `/api/v1/docs` | Interactive docs (hidden when `APP_ENV=production`) |

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
| `$env:RUN_DB_TESTS='1'; uv run pytest -q` | Also run the live tests against Neon |
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
