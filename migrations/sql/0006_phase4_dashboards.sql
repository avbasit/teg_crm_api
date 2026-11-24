-- =============================================================================
-- TEG CRM - Phase 4: dashboards (revision 0006)
-- The Overview (/dashboard) gets real pipeline charts; Productivity gets its own page.
-- =============================================================================

UPDATE app_menus
SET is_available = true, label = 'Productivity', route = '/dashboard/productivity'
WHERE key = 'dashboard.productivity';

-- Team-wide activity counts per type and period (calls, meetings, messages).
CREATE INDEX ix_activities_organization_id_type_occurred_at
  ON activities (organization_id, type, occurred_at);

-- Response-time and contact-rate figures for leads first contacted in a period.
CREATE INDEX ix_leads_organization_id_first_contacted_at
  ON leads (organization_id, first_contacted_at) WHERE deleted_at IS NULL AND first_contacted_at IS NOT NULL;
