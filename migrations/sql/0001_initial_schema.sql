-- =============================================================================
-- TEG CRM - initial schema (revision 0001)
-- Target: Neon Postgres 18, database `neondb`, schema `public`.
-- Design notes: docs/database-design.md. Roles model: docs/roles-and-permissions.md.
--
-- Conventions
--   * snake_case, plural table names; `<entity>_id` foreign keys.
--   * Every table has created_at and updated_at (timestamptz, UTC). updated_at is
--     maintained by the trg_<table>_set_updated_at trigger created at the end.
--   * Constraint names: fk_<table>_<col>, uq_<table>_<cols>, ck_<table>_<rule>,
--     indexes ix_<table>_<cols>. Inline FK/UNIQUE names are normalised at the end.
--   * Log tables (audit_logs, auth_events) are append-only, enforced by trigger.
--   * Meta/TikTok ad tables are added by the Phase 5 migration.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS citext;   -- case-insensitive username / email
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- fast ILIKE search on name / phone / email

-- -----------------------------------------------------------------------------
-- Shared trigger functions
-- -----------------------------------------------------------------------------
CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;
COMMENT ON FUNCTION set_updated_at() IS 'BEFORE UPDATE trigger: stamps updated_at on every row change.';

CREATE FUNCTION prevent_log_modification() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Table % is append-only; % is not allowed', TG_TABLE_NAME, TG_OP;
END $$;
COMMENT ON FUNCTION prevent_log_modification() IS 'Blocks UPDATE/DELETE on append-only log tables.';

-- -----------------------------------------------------------------------------
-- Enums (values fixed in code; admin-editable values live in lookup tables)
-- -----------------------------------------------------------------------------
CREATE TYPE user_status          AS ENUM ('active','paused','archived','invited');
CREATE TYPE data_scope           AS ENUM ('own','team','subtree','all');
CREATE TYPE team_member_role     AS ENUM ('manager','member');
CREATE TYPE lead_source          AS ENUM ('facebook','tiktok','csv','excel','webhook','bayut','dubizzle',
                                          'zapier','manual','event','whatsapp');
CREATE TYPE campaign_type        AS ENUM ('paid_social','event','website','portal','import','manual','referral');
CREATE TYPE campaign_status      AS ENUM ('draft','active','paused','archived');
CREATE TYPE activity_type        AS ENUM ('lead_added','assigned','call','whatsapp','text','email','meeting',
                                          'comment','status_change','label_change','edit','task_created',
                                          'task_completed','duplicate_detected','imported');
CREATE TYPE task_type            AS ENUM ('call_back','send_email','send_whatsapp','send_text','meeting');
CREATE TYPE import_status        AS ENUM ('uploaded','mapped','running','completed','failed');
CREATE TYPE auth_event_type      AS ENUM ('login_succeeded','login_failed','logout','token_refreshed',
                                          'password_changed','password_reset_requested',
                                          'password_reset_completed','account_locked','invite_accepted');
CREATE TYPE webhook_event_status AS ENUM ('received','processed','failed','ignored');
CREATE TYPE job_run_status       AS ENUM ('running','succeeded','failed');

-- =============================================================================
-- 1. Organisation, menus, roles, permissions
-- =============================================================================
CREATE TABLE organizations (
  id               uuid PRIMARY KEY DEFAULT uuidv7(),
  name             text NOT NULL,
  slug             citext NOT NULL UNIQUE,
  logo_url         text,
  phone            text,
  timezone         text NOT NULL DEFAULT 'Asia/Karachi',
  default_currency char(3) NOT NULL DEFAULT 'USD',
  settings         jsonb NOT NULL DEFAULT '{}',
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE organizations IS 'Tenant. Single org (TEG Consultancy) today; every business table carries organization_id.';
COMMENT ON COLUMN organizations.settings IS 'Org rules: hide_history_on_reassign, deal_value, duplicates, mql_status_references.';

CREATE TABLE app_menus (
  key          text PRIMARY KEY,
  parent_key   text REFERENCES app_menus(key),
  label        text NOT NULL,
  route        text,
  icon         text,
  sort_order   smallint NOT NULL DEFAULT 0,
  is_available boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE app_menus IS 'Global catalogue of sidebar menus/screens. Seeded by migration; the unit of menu-based rights.';
COMMENT ON COLUMN app_menus.is_available IS 'false = screen not built yet; hidden for everyone regardless of rights.';

CREATE TABLE roles (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  key             text NOT NULL,
  name            text NOT NULL,
  description     text,
  hierarchy_level smallint NOT NULL,
  data_scope      data_scope NOT NULL,
  is_system       boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_roles_organization_id_key UNIQUE (organization_id, key),
  CONSTRAINT ck_roles_hierarchy_level_range CHECK (hierarchy_level BETWEEN 1 AND 100)
);
COMMENT ON TABLE roles IS 'User roles. hierarchy_level: higher = more senior (100 = super admin). data_scope = default lead visibility.';

CREATE TABLE role_menu_permissions (
  role_id    uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  menu_key   text NOT NULL REFERENCES app_menus(key),
  can_view   boolean NOT NULL DEFAULT false,
  can_create boolean NOT NULL DEFAULT false,
  can_edit   boolean NOT NULL DEFAULT false,
  can_delete boolean NOT NULL DEFAULT false,
  can_export boolean NOT NULL DEFAULT false,
  can_assign boolean NOT NULL DEFAULT false,
  data_scope data_scope,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (role_id, menu_key),
  CONSTRAINT ck_role_menu_permissions_actions_need_view
    CHECK (can_view OR NOT (can_create OR can_edit OR can_delete OR can_export OR can_assign))
);
COMMENT ON TABLE role_menu_permissions IS 'Menu-based rights: one row per role per menu. Enforced by the API on every request.';
COMMENT ON COLUMN role_menu_permissions.data_scope IS 'NULL = inherit roles.data_scope.';
COMMENT ON COLUMN role_menu_permissions.can_assign IS 'Reassign leads / manage campaign agents.';

-- =============================================================================
-- 2. Users, hierarchy, teams, credentials
-- =============================================================================
CREATE TABLE users (
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id      uuid NOT NULL REFERENCES organizations(id),
  role_id              uuid NOT NULL REFERENCES roles(id),
  reports_to_user_id   uuid REFERENCES users(id),
  username             citext NOT NULL UNIQUE,
  email                citext UNIQUE,
  full_name            text NOT NULL,
  phone                text,
  avatar_url           text,
  password_hash        text,
  must_change_password boolean NOT NULL DEFAULT false,
  status               user_status NOT NULL DEFAULT 'invited',
  is_assignable        boolean NOT NULL DEFAULT true,
  failed_login_count   smallint NOT NULL DEFAULT 0,
  locked_until         timestamptz,
  last_login_at        timestamptz,
  last_active_at       timestamptz,
  password_changed_at  timestamptz,
  legacy_jaldi_id      integer UNIQUE,
  archived_at          timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_users_not_self_manager CHECK (reports_to_user_id IS DISTINCT FROM id),
  CONSTRAINT ck_users_active_has_password CHECK (status <> 'active' OR password_hash IS NOT NULL)
);
CREATE INDEX ix_users_reports_to_user_id     ON users (reports_to_user_id);
CREATE INDEX ix_users_role_id                ON users (role_id);
CREATE INDEX ix_users_organization_id_status ON users (organization_id, status);
COMMENT ON TABLE users IS 'Staff accounts. Username is the login id and is globally unique.';
COMMENT ON COLUMN users.reports_to_user_id IS 'Line manager (hierarchy). Must have a higher role level (checked in API); cycles blocked by trigger.';
COMMENT ON COLUMN users.password_hash IS 'argon2id hash; NULL while invited.';
COMMENT ON COLUMN users.is_assignable IS 'May receive leads via round-robin, independent of role.';
COMMENT ON COLUMN users.legacy_jaldi_id IS 'Jaldi user id (e.g. 999) used to map migrated lead ownership.';

CREATE FUNCTION check_users_reporting_cycle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.reports_to_user_id IS NOT NULL AND EXISTS (
    WITH RECURSIVE chain(id) AS (
      SELECT NEW.reports_to_user_id
      UNION
      SELECT u.reports_to_user_id FROM users u JOIN chain c ON u.id = c.id
      WHERE u.reports_to_user_id IS NOT NULL
    )
    SELECT 1 FROM chain WHERE id = NEW.id
  ) THEN
    RAISE EXCEPTION 'reports_to_user_id would create a reporting cycle for user %', NEW.id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_users_reporting_cycle
  BEFORE INSERT OR UPDATE OF reports_to_user_id ON users
  FOR EACH ROW EXECUTE FUNCTION check_users_reporting_cycle();

CREATE TABLE teams (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  name            text NOT NULL,
  description     text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_teams_organization_id_name UNIQUE (organization_id, name)
);
COMMENT ON TABLE teams IS 'Groups of agents run by one or more managers (Jaldi Settings > Teams).';

CREATE TABLE team_members (
  team_id    uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  team_role  team_member_role NOT NULL DEFAULT 'member',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (team_id, user_id)
);
CREATE INDEX ix_team_members_user_id ON team_members (user_id);
COMMENT ON TABLE team_members IS 'Team membership. team_role = manager drives the `team` data scope.';

CREATE TABLE user_sessions (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  text NOT NULL UNIQUE,
  user_agent  text,
  ip_address  inet,
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id);
COMMENT ON TABLE user_sessions IS 'Refresh tokens (SHA-256 hash only), rotated on every refresh.';

CREATE TABLE user_invitations (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash         text NOT NULL UNIQUE,
  invited_by_user_id uuid REFERENCES users(id),
  expires_at         timestamptz NOT NULL,
  accepted_at        timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_user_invitations_user_id ON user_invitations (user_id);
COMMENT ON TABLE user_invitations IS 'Invite links emailed to new users (token hash only).';

CREATE TABLE password_reset_tokens (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  text NOT NULL UNIQUE,
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_password_reset_tokens_user_id ON password_reset_tokens (user_id);
COMMENT ON TABLE password_reset_tokens IS 'Single-use password reset links (token hash only).';

-- =============================================================================
-- 3. Configuration lookups
-- =============================================================================
CREATE TABLE lead_statuses (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  reference       text NOT NULL,
  name            text NOT NULL,
  color           text NOT NULL,
  sort_order      smallint NOT NULL,
  is_system       boolean NOT NULL DEFAULT false,
  is_contacted    boolean NOT NULL DEFAULT true,
  is_mql          boolean NOT NULL DEFAULT false,
  is_won          boolean NOT NULL DEFAULT false,
  is_terminal     boolean NOT NULL DEFAULT false,
  is_active       boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_lead_statuses_organization_id_reference UNIQUE (organization_id, reference)
);
COMMENT ON TABLE lead_statuses IS 'Admin-editable pipeline statuses (Settings > Customizations).';
COMMENT ON COLUMN lead_statuses.reference IS 'Stable code used by code and reports, e.g. working_deal.';
COMMENT ON COLUMN lead_statuses.is_contacted IS 'false for not_contacted / unprocessed (= Pending).';
COMMENT ON COLUMN lead_statuses.is_terminal IS 'Lost / unqualified / closed: no open tasks expected.';

CREATE TABLE labels (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  name            citext NOT NULL,
  color           text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_labels_organization_id_name UNIQUE (organization_id, name)
);
COMMENT ON TABLE labels IS 'Coloured lead tags (many-to-many via lead_labels).';

CREATE TABLE programs (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  code            text NOT NULL,
  name            text NOT NULL,
  country         text,
  is_active       boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_programs_organization_id_code UNIQUE (organization_id, code)
);
COMMENT ON TABLE programs IS 'Immigration programmes (E2, D2, SUV...) for per-programme reporting.';

CREATE TABLE form_field_aliases (
  organization_id uuid NOT NULL REFERENCES organizations(id),
  raw_key         text NOT NULL,
  canonical_field text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, raw_key)
);
COMMENT ON TABLE form_field_aliases IS 'Maps raw lead-form question keys (normalised snake_case) to canonical lead columns.';

-- =============================================================================
-- 4. Campaigns and intake credentials
-- =============================================================================
CREATE TABLE campaigns (
  id                  uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id     uuid NOT NULL REFERENCES organizations(id),
  name                text NOT NULL,
  source              lead_source NOT NULL,
  campaign_type       campaign_type NOT NULL,
  program_id          uuid REFERENCES programs(id),
  market              text,
  status              campaign_status NOT NULL DEFAULT 'draft',
  assignment_strategy text NOT NULL DEFAULT 'round_robin',
  last_lead_at        timestamptz,
  created_by_user_id  uuid REFERENCES users(id),
  legacy_jaldi_name   text,
  deleted_at          timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_campaigns_assignment_strategy CHECK (assignment_strategy IN ('round_robin','manual'))
);
CREATE INDEX ix_campaigns_organization_id_status ON campaigns (organization_id, status) WHERE deleted_at IS NULL;
CREATE INDEX ix_campaigns_program_id             ON campaigns (program_id);
COMMENT ON TABLE campaigns IS 'Lead container per source/channel; leads are round-robin assigned to campaign_agents.';
COMMENT ON COLUMN campaigns.market IS 'Target market, e.g. Lahore, Islamabad, Doha, KSA, UAE.';

CREATE TABLE campaign_agents (
  campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES users(id),
  is_active   boolean NOT NULL DEFAULT true,
  rr_position integer NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (campaign_id, user_id)
);
CREATE INDEX ix_campaign_agents_user_id ON campaign_agents (user_id);
COMMENT ON TABLE campaign_agents IS 'Agents in a campaign round-robin. is_active = currently receiving leads.';

CREATE TABLE api_keys (
  id                  uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id     uuid NOT NULL REFERENCES organizations(id),
  name                text NOT NULL,
  key_prefix          text NOT NULL,
  key_hash            text NOT NULL UNIQUE,
  scopes              text[] NOT NULL DEFAULT '{leads:create}',
  campaign_id         uuid REFERENCES campaigns(id),
  created_by_user_id  uuid REFERENCES users(id),
  last_used_at        timestamptz,
  expires_at          timestamptz,
  revoked_at          timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE api_keys IS 'Org API keys for website forms / Zapier to POST leads (replaces Jaldi Website Integration key). Only the hash is stored.';
COMMENT ON COLUMN api_keys.key_prefix IS 'First characters of the key, shown in the UI to identify it.';

-- =============================================================================
-- 5. Contacts and leads
-- =============================================================================
CREATE TABLE contacts (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  full_name       text NOT NULL,
  phone_e164      text,
  email           citext,
  city            text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_contacts_organization_id_phone_e164 ON contacts (organization_id, phone_e164);
CREATE INDEX ix_contacts_organization_id_email      ON contacts (organization_id, email);
COMMENT ON TABLE contacts IS 'One real person; a contact can have many leads (enquiries) across campaigns.';

CREATE TABLE leads (
  id                    uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id       uuid NOT NULL REFERENCES organizations(id),
  contact_id            uuid REFERENCES contacts(id),
  campaign_id           uuid REFERENCES campaigns(id),
  owner_user_id         uuid REFERENCES users(id),
  status_id             uuid NOT NULL REFERENCES lead_statuses(id),

  first_name            text NOT NULL,
  last_name             text,
  full_name             text GENERATED ALWAYS AS (trim(first_name || ' ' || coalesce(last_name, ''))) STORED,
  phone_raw             text,
  phone_e164            text,
  phone_country_code    text,
  whatsapp_e164         text,
  email                 citext,

  city                  text,
  country_of_interest   text,
  budget_text           text,
  education_level       text,
  english_test_status   text,
  age_text              text,
  has_family            boolean,
  form_answers          jsonb NOT NULL DEFAULT '{}',
  notes                 text,

  source                lead_source NOT NULL,
  sub_source            text,
  external_lead_id      text,
  source_campaign_name  text,
  source_adset_name     text,
  source_ad_name        text,
  source_form_name      text,
  source_created_at     timestamptz,

  deal_value            numeric(14,2),
  deal_currency         char(3),

  is_duplicate          boolean NOT NULL DEFAULT false,
  duplicate_of_lead_id  uuid REFERENCES leads(id),

  calls_made            integer NOT NULL DEFAULT 0,
  times_assigned        integer NOT NULL DEFAULT 0,
  first_contacted_at    timestamptz,
  response_time_seconds integer,
  last_assigned_at      timestamptz,
  last_activity_at      timestamptz,
  last_activity_note    text,
  next_task_due_at      timestamptz,

  legacy_jaldi_id       bigint UNIQUE,
  deleted_at            timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT ck_leads_has_contact_method CHECK (phone_e164 IS NOT NULL OR phone_raw IS NOT NULL OR email IS NOT NULL),
  CONSTRAINT ck_leads_not_self_duplicate CHECK (duplicate_of_lead_id IS DISTINCT FROM id),
  CONSTRAINT ck_leads_counters_non_negative CHECK (calls_made >= 0 AND times_assigned >= 0)
);
CREATE INDEX ix_leads_organization_id_created_at ON leads (organization_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX ix_leads_owner_user_id_status_id    ON leads (owner_user_id, status_id)        WHERE deleted_at IS NULL;
CREATE INDEX ix_leads_status_id                  ON leads (status_id);
CREATE INDEX ix_leads_campaign_id                ON leads (campaign_id);
CREATE INDEX ix_leads_contact_id                 ON leads (contact_id);
CREATE INDEX ix_leads_organization_id_phone_e164 ON leads (organization_id, phone_e164);
CREATE INDEX ix_leads_organization_id_email      ON leads (organization_id, email);
CREATE INDEX ix_leads_next_task_due_at           ON leads (next_task_due_at) WHERE next_task_due_at IS NOT NULL;
CREATE UNIQUE INDEX uq_leads_organization_id_source_external_lead_id
  ON leads (organization_id, source, external_lead_id) WHERE external_lead_id IS NOT NULL;
CREATE INDEX ix_leads_full_name_trgm  ON leads USING gin (full_name gin_trgm_ops);
CREATE INDEX ix_leads_phone_e164_trgm ON leads USING gin (phone_e164 gin_trgm_ops);
CREATE INDEX ix_leads_email_trgm      ON leads USING gin ((email::text) gin_trgm_ops);
CREATE INDEX ix_leads_form_answers    ON leads USING gin (form_answers jsonb_path_ops);
COMMENT ON TABLE leads IS 'One enquiry: a person on a campaign, owned by an agent. Counters are kept in sync by the service layer.';
COMMENT ON COLUMN leads.owner_user_id IS 'Assigned agent; NULL = unassigned.';
COMMENT ON COLUMN leads.form_answers IS 'All raw form answers, keys normalised to snake_case.';
COMMENT ON COLUMN leads.external_lead_id IS 'Provider lead id (e.g. Facebook leadgen id); unique per source for idempotent intake.';
COMMENT ON COLUMN leads.legacy_jaldi_id IS 'Jaldi Lead ID for migration reconciliation.';

CREATE TABLE lead_labels (
  lead_id    uuid NOT NULL REFERENCES leads(id)  ON DELETE CASCADE,
  label_id   uuid NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (lead_id, label_id)
);
CREATE INDEX ix_lead_labels_label_id ON lead_labels (label_id);
COMMENT ON TABLE lead_labels IS 'Labels attached to a lead.';

-- =============================================================================
-- 6. Work: tasks (mutable) and activity / assignment logs (append-only by convention)
-- =============================================================================
CREATE TABLE tasks (
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id      uuid NOT NULL REFERENCES organizations(id),
  lead_id              uuid NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  assigned_to_user_id  uuid REFERENCES users(id),
  type                 task_type NOT NULL,
  notes                text,
  due_at               timestamptz NOT NULL,
  completed_at         timestamptz,
  completed_by_user_id uuid REFERENCES users(id),
  created_by_user_id   uuid REFERENCES users(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_tasks_open_assigned_to_user_id_due_at ON tasks (assigned_to_user_id, due_at) WHERE completed_at IS NULL;
CREATE INDEX ix_tasks_lead_id ON tasks (lead_id);
COMMENT ON TABLE tasks IS 'Follow-ups on a lead. Status (due / overdue / completed) is derived from due_at and completed_at.';

CREATE TABLE activities (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  organization_id uuid NOT NULL REFERENCES organizations(id),
  lead_id         uuid NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  user_id         uuid REFERENCES users(id),
  type            activity_type NOT NULL,
  outcome         text,
  notes           text,
  location        text,
  from_status_id  uuid REFERENCES lead_statuses(id),
  to_status_id    uuid REFERENCES lead_statuses(id),
  meta            jsonb NOT NULL DEFAULT '{}',
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_activities_lead_id_occurred_at                ON activities (lead_id, occurred_at DESC);
CREATE INDEX ix_activities_organization_id_user_id_type_occurred_at ON activities (organization_id, user_id, type, occurred_at);
COMMENT ON TABLE activities IS 'Lead timeline log: single source for timelines and productivity metrics. user_id NULL = SYSTEM.';
COMMENT ON COLUMN activities.outcome IS 'Call: answered / did_not_respond / dead_number. Meeting: done / rescheduled / no_show / cancelled.';
COMMENT ON COLUMN activities.occurred_at IS 'Business time (backdated for imports); created_at is insert time.';

CREATE TABLE lead_assignments (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  lead_id             uuid NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  from_user_id        uuid REFERENCES users(id),
  to_user_id          uuid REFERENCES users(id),
  assigned_by_user_id uuid REFERENCES users(id),
  reason              text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_lead_assignments_reason CHECK (reason IN ('round_robin','manual','bulk','duplicate_routing','import'))
);
CREATE INDEX ix_lead_assignments_lead_id_created_at ON lead_assignments (lead_id, created_at DESC);
CREATE INDEX ix_lead_assignments_to_user_id         ON lead_assignments (to_user_id);
COMMENT ON TABLE lead_assignments IS 'Assignment history log (times assigned, last assigned). assigned_by_user_id NULL = SYSTEM.';

-- =============================================================================
-- 7. Imports and intake logs
-- =============================================================================
CREATE TABLE imports (
  id                     uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id        uuid NOT NULL REFERENCES organizations(id),
  campaign_id            uuid REFERENCES campaigns(id),
  file_name              text NOT NULL,
  file_url               text,
  column_mapping         jsonb NOT NULL DEFAULT '{}',
  status                 import_status NOT NULL DEFAULT 'uploaded',
  total_rows             integer,
  imported_rows          integer,
  skipped_duplicate_rows integer,
  failed_rows            integer,
  created_by_user_id     uuid REFERENCES users(id),
  completed_at           timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_imports_campaign_id ON imports (campaign_id);
COMMENT ON TABLE imports IS 'CSV / Excel upload jobs and the Jaldi migration runs. file_url points to Vercel Blob.';

CREATE TABLE import_rows (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_id  uuid NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
  row_number integer NOT NULL,
  raw        jsonb NOT NULL,
  lead_id    uuid REFERENCES leads(id) ON DELETE SET NULL,
  error      text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_import_rows_import_id_row_number UNIQUE (import_id, row_number)
);
COMMENT ON TABLE import_rows IS 'Row-level import log: raw row, resulting lead, or error.';

CREATE TABLE webhook_events (
  id                uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id   uuid REFERENCES organizations(id),
  provider          text NOT NULL,
  api_key_id        uuid REFERENCES api_keys(id),
  external_event_id text,
  payload           jsonb NOT NULL,
  headers           jsonb NOT NULL DEFAULT '{}',
  status            webhook_event_status NOT NULL DEFAULT 'received',
  error             text,
  attempts          smallint NOT NULL DEFAULT 0,
  lead_id           uuid REFERENCES leads(id) ON DELETE SET NULL,
  processed_at      timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_webhook_events_provider_external_event_id
  ON webhook_events (provider, external_event_id) WHERE external_event_id IS NOT NULL;
CREATE INDEX ix_webhook_events_status_created_at ON webhook_events (status, created_at);
COMMENT ON TABLE webhook_events IS 'Inbound lead log (website forms, Zapier; Facebook/TikTok from Phase 5). Stored before processing so nothing is lost.';
COMMENT ON COLUMN webhook_events.provider IS 'website, zapier, facebook, tiktok, bayut, dubizzle.';

-- =============================================================================
-- 8. System logs
-- =============================================================================
CREATE TABLE audit_logs (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  organization_id uuid NOT NULL REFERENCES organizations(id),
  actor_user_id   uuid REFERENCES users(id),
  entity_type     text NOT NULL,
  entity_id       text NOT NULL,
  action          text NOT NULL,
  changes         jsonb,
  ip_address      inet,
  user_agent      text,
  request_id      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_logs_entity_type_entity_id_created_at ON audit_logs (entity_type, entity_id, created_at DESC);
CREATE INDEX ix_audit_logs_actor_user_id_created_at         ON audit_logs (actor_user_id, created_at DESC);
CREATE INDEX ix_audit_logs_organization_id_created_at       ON audit_logs (organization_id, created_at DESC);
COMMENT ON TABLE audit_logs IS 'Append-only change log for users, roles, permissions, settings, leads. Shown in Settings > Audit Log.';
COMMENT ON COLUMN audit_logs.action IS 'create, update, delete, restore, permission_change, role_change, export, bulk_update.';
COMMENT ON COLUMN audit_logs.changes IS '{"field": [old, new]}.';

CREATE TABLE auth_events (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  organization_id    uuid REFERENCES organizations(id),
  user_id            uuid REFERENCES users(id),
  username_attempted citext,
  event_type         auth_event_type NOT NULL,
  ip_address         inet,
  user_agent         text,
  meta               jsonb NOT NULL DEFAULT '{}',
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_auth_events_user_id_created_at    ON auth_events (user_id, created_at DESC);
CREATE INDEX ix_auth_events_event_type_created_at ON auth_events (event_type, created_at DESC);
COMMENT ON TABLE auth_events IS 'Append-only login / security log (successful and failed logins, resets, lockouts).';

CREATE TABLE email_logs (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  organization_id     uuid REFERENCES organizations(id),
  user_id             uuid REFERENCES users(id),
  to_email            citext NOT NULL,
  template            text NOT NULL,
  provider_message_id text,
  status              text NOT NULL DEFAULT 'queued',
  error               text,
  sent_at             timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_email_logs_status CHECK (status IN ('queued','sent','delivered','bounced','failed'))
);
CREATE INDEX ix_email_logs_user_id ON email_logs (user_id);
COMMENT ON TABLE email_logs IS 'Outbound email log (invites, password resets; later notifications) sent via Resend.';

CREATE TABLE job_runs (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  organization_id uuid REFERENCES organizations(id),
  job_name        text NOT NULL,
  status          job_run_status NOT NULL DEFAULT 'running',
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz,
  items_processed integer NOT NULL DEFAULT 0,
  error           text,
  meta            jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_job_runs_job_name_started_at ON job_runs (job_name, started_at DESC);
COMMENT ON TABLE job_runs IS 'Background job log (Vercel Cron / QStash): imports, overdue-task alerts, later Meta sync.';

-- =============================================================================
-- 9. Triggers and constraint-name normalisation
-- =============================================================================
DO $$
DECLARE t record;
BEGIN
  -- updated_at on every table that has it
  FOR t IN
    SELECT table_name FROM information_schema.columns
    WHERE table_schema = 'public' AND column_name = 'updated_at'
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION set_updated_at()',
      'trg_' || t.table_name || '_set_updated_at', t.table_name);
  END LOOP;

  -- append-only log tables
  FOR t IN SELECT unnest(ARRAY['audit_logs','auth_events']) AS table_name
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION prevent_log_modification()',
      'trg_' || t.table_name || '_append_only', t.table_name);
  END LOOP;

  -- rename auto-named FK / UNIQUE constraints to fk_<table>_<cols> / uq_<table>_<cols>
  FOR t IN
    SELECT rel.relname AS table_name, con.conname, con.contype,
           string_agg(att.attname, '_' ORDER BY k.ord) AS cols
    FROM pg_constraint con
    JOIN pg_class rel     ON rel.oid = con.conrelid
    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
    CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
    WHERE nsp.nspname = 'public' AND con.contype IN ('f','u')
      AND con.conname NOT LIKE 'fk\_%' AND con.conname NOT LIKE 'uq\_%'
    GROUP BY rel.relname, con.conname, con.contype
  LOOP
    EXECUTE format('ALTER TABLE %I RENAME CONSTRAINT %I TO %I', t.table_name, t.conname,
      left(CASE t.contype WHEN 'f' THEN 'fk_' ELSE 'uq_' END || t.table_name || '_' || t.cols, 63));
  END LOOP;
END $$;
