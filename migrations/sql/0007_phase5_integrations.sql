-- =============================================================================
-- TEG CRM - Phase 5: ad platform integrations (revision 0007)
-- Decisions (25 Nov 2026): Meta first (one Page, one ad account, billed in AED);
-- one CRM campaign per ad campaign, created automatically; leads go to Unassigned
-- until go-live; TikTok, LinkedIn and Snapchat follow with their own connectors.
-- Tokens are stored only encrypted (INTEGRATIONS_SECRET_KEY in the API environment).
-- =============================================================================

ALTER TYPE lead_source ADD VALUE IF NOT EXISTS 'linkedin';
ALTER TYPE lead_source ADD VALUE IF NOT EXISTS 'snapchat';

CREATE TYPE integration_provider AS ENUM ('meta', 'tiktok', 'linkedin', 'snapchat');
CREATE TYPE integration_status   AS ENUM ('connected', 'needs_reconnect', 'disconnected');

CREATE TABLE integrations (
  id                     uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id        uuid NOT NULL REFERENCES organizations(id),
  provider               integration_provider NOT NULL,
  status                 integration_status NOT NULL DEFAULT 'connected',
  external_user_id       text,
  external_user_name     text,
  access_token_encrypted text,
  token_expires_at       timestamptz,
  scopes                 text[] NOT NULL DEFAULT '{}',
  settings               jsonb NOT NULL DEFAULT '{}',
  connected_by_user_id   uuid REFERENCES users(id),
  connected_at           timestamptz NOT NULL DEFAULT now(),
  last_webhook_at        timestamptz,
  last_lead_sync_at      timestamptz,
  last_spend_sync_at     timestamptz,
  last_error             text,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_integrations_organization_id_provider UNIQUE (organization_id, provider)
);
COMMENT ON TABLE integrations IS 'One connected ad platform per organisation. Tokens are encrypted; settings.assign_mode = unassigned | campaign.';

CREATE TABLE integration_pages (
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  integration_id       uuid NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
  external_page_id     text NOT NULL,
  name                 text NOT NULL,
  page_token_encrypted text,
  is_selected          boolean NOT NULL DEFAULT false,
  subscribed_at        timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_integration_pages_integration_id_external_page_id UNIQUE (integration_id, external_page_id)
);
CREATE INDEX ix_integration_pages_external_page_id ON integration_pages (external_page_id);
COMMENT ON TABLE integration_pages IS 'Facebook Pages (or equivalent) the connected user manages; selected ones send leads.';

CREATE TABLE ad_accounts (
  id                  uuid PRIMARY KEY DEFAULT uuidv7(),
  integration_id      uuid NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
  external_account_id text NOT NULL,
  name                text NOT NULL,
  currency            char(3) NOT NULL,
  timezone            text,
  is_selected         boolean NOT NULL DEFAULT false,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_ad_accounts_integration_id_external_account_id UNIQUE (integration_id, external_account_id)
);
COMMENT ON TABLE ad_accounts IS 'Ad accounts; selected ones have their daily spend synced.';

CREATE TABLE ad_campaigns (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  provider        integration_provider NOT NULL,
  external_id     text NOT NULL,
  name            text NOT NULL,
  ad_account_id   uuid REFERENCES ad_accounts(id) ON DELETE SET NULL,
  crm_campaign_id uuid REFERENCES campaigns(id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_ad_campaigns_organization_id_provider_external_id UNIQUE (organization_id, provider, external_id)
);
CREATE INDEX ix_ad_campaigns_crm_campaign_id ON ad_campaigns (crm_campaign_id);
COMMENT ON COLUMN ad_campaigns.crm_campaign_id IS 'The CRM campaign its leads go into: linked by a user (Campaign › Meta ads), or created automatically on the first lead of an unlinked ad.';

CREATE TABLE ad_sets (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  ad_campaign_id uuid NOT NULL REFERENCES ad_campaigns(id) ON DELETE CASCADE,
  external_id    text NOT NULL,
  name           text NOT NULL,
  crm_campaign_id uuid REFERENCES campaigns(id) ON DELETE SET NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_ad_sets_ad_campaign_id_external_id UNIQUE (ad_campaign_id, external_id)
);

CREATE TABLE ads (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  ad_set_id   uuid NOT NULL REFERENCES ad_sets(id) ON DELETE CASCADE,
  external_id text NOT NULL,
  name        text NOT NULL,
  crm_campaign_id uuid REFERENCES campaigns(id) ON DELETE SET NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_ads_ad_set_id_external_id UNIQUE (ad_set_id, external_id)
);
CREATE INDEX ix_ad_sets_crm_campaign_id ON ad_sets (crm_campaign_id) WHERE crm_campaign_id IS NOT NULL;
CREATE INDEX ix_ads_crm_campaign_id ON ads (crm_campaign_id) WHERE crm_campaign_id IS NOT NULL;
COMMENT ON COLUMN ads.crm_campaign_id IS 'Jaldi-style link: leads from this ad go to this CRM campaign. The most specific link wins (ad, then ad set, then ad campaign).';

CREATE TABLE ad_insights_daily (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  organization_id uuid NOT NULL REFERENCES organizations(id),
  ad_id           uuid NOT NULL REFERENCES ads(id) ON DELETE CASCADE,
  day             date NOT NULL,
  spend           numeric(14,2) NOT NULL DEFAULT 0,
  currency        char(3) NOT NULL,
  impressions     integer NOT NULL DEFAULT 0,
  clicks          integer NOT NULL DEFAULT 0,
  platform_leads  integer NOT NULL DEFAULT 0,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_ad_insights_daily_ad_id_day UNIQUE (ad_id, day)
);
CREATE INDEX ix_ad_insights_daily_organization_id_day ON ad_insights_daily (organization_id, day);
COMMENT ON TABLE ad_insights_daily IS 'Spend and delivery per ad per day (ad account timezone), from the daily sync.';

ALTER TABLE leads ADD COLUMN ad_id uuid REFERENCES ads(id) ON DELETE SET NULL;
CREATE INDEX ix_leads_ad_id ON leads (ad_id) WHERE ad_id IS NOT NULL;
COMMENT ON COLUMN leads.ad_id IS 'The ad a lead-form lead came from (cost per lead by campaign, ad set and ad).';

-- Spend in AED, deal value in USD: AED is pegged at 3.6725 per USD.
UPDATE organizations
SET settings = settings || '{"currency_rates": {"AED": 0.272294, "USD": 1}}';

-- Screens: Settings > Integrations and the Marketing dashboard.
UPDATE app_menus SET is_available = true WHERE key = 'settings.integrations';
UPDATE app_menus SET is_available = true, label = 'Marketing', route = '/dashboard/marketing'
WHERE key = 'dashboard.marketing';
