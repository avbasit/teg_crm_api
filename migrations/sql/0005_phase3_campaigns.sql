-- =============================================================================
-- TEG CRM - Phase 3: campaigns and lead intake (revision 0005)
-- Decisions (19 Nov 2026):
--   * Markets are a fixed list, editable in Settings > Customizations.
--   * A repeat enquiry goes to the owner of the person's earlier lead; if that
--     owner isn't active, to the campaign round-robin.
--   * The duplicate badge is visible to everyone.
--   * Nobody available (or a manual campaign): the lead waits in Unassigned.
-- =============================================================================

-- Markets ---------------------------------------------------------------------------
CREATE TABLE markets (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  name            citext NOT NULL,
  is_active       boolean NOT NULL DEFAULT true,
  sort_order      smallint NOT NULL DEFAULT 0,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_markets_organization_id_name UNIQUE (organization_id, name)
);
COMMENT ON TABLE markets IS 'Target markets for campaigns (Lahore, Islamabad, Doha...). Edited in Customizations.';

INSERT INTO markets (organization_id, name, sort_order)
SELECT o.id, m.name, m.sort_order
FROM organizations o
CROSS JOIN (VALUES ('Lahore', 1), ('Islamabad', 2), ('Doha', 3), ('KSA', 4), ('UAE', 5)) AS m(name, sort_order);

ALTER TABLE campaigns ADD COLUMN market_id uuid REFERENCES markets(id);
CREATE INDEX ix_campaigns_market_id ON campaigns (market_id);
COMMENT ON COLUMN campaigns.market IS 'Legacy free-text market (Jaldi import); new campaigns use market_id.';

-- Website keys can be limited to the sites that may use them --------------------------
ALTER TABLE api_keys ADD COLUMN allowed_origins text[] NOT NULL DEFAULT '{}';
COMMENT ON COLUMN api_keys.allowed_origins IS 'If set, browser requests must come from one of these origins (https://example.com).';
CREATE INDEX ix_api_keys_campaign_id ON api_keys (campaign_id);

-- Round-robin lookups ------------------------------------------------------------------
CREATE INDEX ix_campaign_agents_campaign_id_rr ON campaign_agents (campaign_id, rr_position) WHERE is_active;
CREATE INDEX ix_leads_contact_id_created_at ON leads (contact_id, created_at) WHERE deleted_at IS NULL;

-- Menus: Unassigned list, then switch on the Phase 3 screens ----------------------------
UPDATE app_menus SET sort_order = 23, route = '/leads?dup=1' WHERE key = 'leads.duplicates';
UPDATE app_menus SET sort_order = 24 WHERE key = 'leads.import';
INSERT INTO app_menus (key, parent_key, label, route, icon, sort_order, is_available)
VALUES ('leads.unassigned', 'leads', 'Unassigned', '/leads?owner=unassigned', 'inbox', 22, true);

INSERT INTO role_menu_permissions (role_id, menu_key)
SELECT r.id, 'leads.unassigned' FROM roles r;
UPDATE role_menu_permissions p SET can_view = true
FROM roles r
WHERE p.role_id = r.id AND p.menu_key = 'leads.unassigned'
  AND r.key IN ('super_admin', 'administrator', 'head_of_sales', 'sales_manager');

UPDATE app_menus SET is_available = true
WHERE key IN ('campaigns', 'leads.duplicates', 'leads.import', 'settings.duplicates');

-- Duplicate rules: Jaldi's blocking rules stay; routing and badge follow the decisions ---
UPDATE organizations
SET settings = jsonb_set(
      jsonb_set(settings, '{duplicates,digital,routing}', '"previous_owner"', true),
      '{duplicates,tagging,badge_visible_to}', '["everyone"]', true);
