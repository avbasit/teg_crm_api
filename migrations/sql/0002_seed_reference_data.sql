-- =============================================================================
-- TEG CRM - reference data (revision 0002)
-- Source of truth for roles/menus/rights: docs/roles-and-permissions.md.
-- The first Super Admin user is inserted by the migration's Python code
-- (password hashed at run time from SEED_ADMIN_PASSWORD, never stored in git).
-- =============================================================================

-- Organisation -----------------------------------------------------------------
INSERT INTO organizations (name, slug, timezone, default_currency, settings)
VALUES ('TEG Consultancy', 'teg', 'Asia/Karachi', 'USD', jsonb_build_object(
  'hide_history_on_reassign', false,
  'deal_value', jsonb_build_object('is_active', true, 'currency', 'USD'),
  'mql_status_references', jsonb_build_array('qualified','working_deal','deal_closed','future_prospect','information_seeker'),
  -- Mirrors current Jaldi settings; final rules are an open decision for Phase 3.
  'duplicates', jsonb_build_object(
    'import',  jsonb_build_object('prevent_global', false, 'block_same_campaign', true),
    'manual',  jsonb_build_object('prevent_global', false, 'block_same_agent', false),
    'digital', jsonb_build_object('prevent_global', false, 'block_same_campaign', false,
                                  'routing', 'next_available_agent'),
    'tagging', jsonb_build_object('tag_new', true, 'tag_existing', false,
                                  'badge_visible_to', jsonb_build_array('admins'))
  )
));

-- Menus (global) ----------------------------------------------------------------
INSERT INTO app_menus (key, parent_key, label, route, icon, sort_order, is_available) VALUES
  ('dashboard',               NULL,        'Dashboard',            NULL,                      'layout-dashboard', 10, true),
  ('dashboard.pipeline',      'dashboard', 'My Pipeline',          '/dashboard/pipeline',     'chart-pie',        11, false),
  ('dashboard.productivity',  'dashboard', 'My Productivity',      '/dashboard/productivity', 'activity',         12, false),
  ('dashboard.marketing',     'dashboard', 'Marketing (Meta)',     '/dashboard/marketing',    'megaphone',        13, false),
  ('leads',                   NULL,        'Leads',                NULL,                      'users-round',      20, true),
  ('leads.all',               'leads',     'All Leads',            '/leads',                  'list',             21, false),
  ('leads.duplicates',        'leads',     'Duplicates',           '/leads?duplicates=true',  'copy',             22, false),
  ('leads.import',            'leads',     'Import Leads',         '/leads/import',           'upload',           23, false),
  ('tasks',                   NULL,        'My Tasks',             '/tasks',                  'list-checks',      30, false),
  ('campaigns',               NULL,        'Campaigns',            '/campaigns',              'target',           40, false),
  ('users',                   NULL,        'Users',                '/users',                  'user-cog',         50, true),
  ('teams',                   NULL,        'Teams',                '/teams',                  'users',            60, true),
  ('settings',                NULL,        'Settings',             NULL,                      'settings',         70, true),
  ('settings.organization',   'settings',  'Organisation Profile', '/settings/organization',  'building-2',       71, true),
  ('settings.customizations', 'settings',  'Customizations',       '/settings/customizations','palette',          72, true),
  ('settings.roles',          'settings',  'Roles & Permissions',  '/settings/roles',         'shield-check',     73, true),
  ('settings.duplicates',     'settings',  'Duplicate Rules',      '/settings/duplicates',    'git-merge',        74, false),
  ('settings.integrations',   'settings',  'Integrations',         '/settings/integrations',  'plug',             75, false),
  ('settings.audit_log',      'settings',  'Audit Log',            '/settings/audit-log',     'scroll-text',      76, true),
  ('account',                 NULL,        'My Account',           '/account',                'circle-user',      80, true);

-- Roles -------------------------------------------------------------------------
INSERT INTO roles (organization_id, key, name, description, hierarchy_level, data_scope, is_system)
SELECT o.id, r.key, r.name, r.description, r.level, r.scope::data_scope, true
FROM organizations o
CROSS JOIN (VALUES
  ('super_admin',       'Super Admin',       'System owner. Only role that can edit roles and permissions or manage other Super Admins.', 100, 'all'),
  ('administrator',     'Administrator',     'Runs the CRM day to day: users, teams, campaigns, settings.',                                90, 'all'),
  ('head_of_sales',     'Head of Sales',     'Owns the sales pipeline, reassigns leads, sees all agents'' performance.',                  70, 'all'),
  ('marketing_manager', 'Marketing Manager', 'Owns campaigns, imports and ad-spend reporting; reads leads for quality.',                   70, 'all'),
  ('sales_manager',     'Sales Manager',     'Manages a team of agents; sees and reassigns within their team.',                           50, 'team'),
  ('agent',             'Agent',             'Works assigned leads: logs activities, changes status, manages own tasks.',                 10, 'own')
) AS r(key, name, description, level, scope)
WHERE o.slug = 'teg';

-- Rights matrix -------------------------------------------------------------------
-- Every role gets a row for every leaf menu (all false), so the Roles & Permissions
-- screen always has a full grid; then the defaults below switch rights on.
INSERT INTO role_menu_permissions (role_id, menu_key)
SELECT r.id, m.key
FROM roles r
CROSS JOIN app_menus m
WHERE m.route IS NOT NULL;

-- Letters: V view, C create, E edit, D delete, X export, A assign.
WITH matrix(role_key, menu_key, rights) AS (VALUES
  ('super_admin','dashboard.pipeline','VX'),       ('administrator','dashboard.pipeline','VX'),
  ('head_of_sales','dashboard.pipeline','VX'),     ('marketing_manager','dashboard.pipeline','VX'),
  ('sales_manager','dashboard.pipeline','V'),      ('agent','dashboard.pipeline','V'),

  ('super_admin','dashboard.productivity','VX'),   ('administrator','dashboard.productivity','VX'),
  ('head_of_sales','dashboard.productivity','VX'), ('marketing_manager','dashboard.productivity','V'),
  ('sales_manager','dashboard.productivity','V'),  ('agent','dashboard.productivity','V'),

  ('super_admin','dashboard.marketing','VX'),      ('administrator','dashboard.marketing','VX'),
  ('head_of_sales','dashboard.marketing','V'),     ('marketing_manager','dashboard.marketing','VX'),

  ('super_admin','leads.all','VCEDXA'),            ('administrator','leads.all','VCEDXA'),
  ('head_of_sales','leads.all','VCEXA'),           ('marketing_manager','leads.all','VX'),
  ('sales_manager','leads.all','VCEA'),            ('agent','leads.all','VCE'),

  ('super_admin','leads.duplicates','VED'),        ('administrator','leads.duplicates','VED'),
  ('head_of_sales','leads.duplicates','VE'),       ('marketing_manager','leads.duplicates','V'),
  ('sales_manager','leads.duplicates','V'),

  ('super_admin','leads.import','VC'),             ('administrator','leads.import','VC'),
  ('head_of_sales','leads.import','VC'),           ('marketing_manager','leads.import','VC'),

  ('super_admin','tasks','VCED'),                  ('administrator','tasks','VCED'),
  ('head_of_sales','tasks','VCED'),                ('marketing_manager','tasks','V'),
  ('sales_manager','tasks','VCE'),                 ('agent','tasks','VCE'),

  ('super_admin','campaigns','VCEDA'),             ('administrator','campaigns','VCEDA'),
  ('head_of_sales','campaigns','VEA'),             ('marketing_manager','campaigns','VCEDA'),
  ('sales_manager','campaigns','V'),

  ('super_admin','users','VCED'),                  ('administrator','users','VCED'),
  ('head_of_sales','users','V'),                   ('sales_manager','users','V'),

  ('super_admin','teams','VCED'),                  ('administrator','teams','VCED'),
  ('head_of_sales','teams','VCE'),                 ('sales_manager','teams','V'),

  ('super_admin','settings.organization','VE'),    ('administrator','settings.organization','VE'),

  ('super_admin','settings.customizations','VCED'),('administrator','settings.customizations','VCED'),
  ('head_of_sales','settings.customizations','V'), ('marketing_manager','settings.customizations','V'),

  ('super_admin','settings.roles','VCED'),         ('administrator','settings.roles','V'),

  ('super_admin','settings.duplicates','VE'),      ('administrator','settings.duplicates','VE'),
  ('head_of_sales','settings.duplicates','V'),

  ('super_admin','settings.integrations','VCED'),  ('administrator','settings.integrations','VCED'),
  ('marketing_manager','settings.integrations','VCE'),

  ('super_admin','settings.audit_log','VX'),       ('administrator','settings.audit_log','V'),

  ('super_admin','account','VE'),                  ('administrator','account','VE'),
  ('head_of_sales','account','VE'),                ('marketing_manager','account','VE'),
  ('sales_manager','account','VE'),                ('agent','account','VE')
)
UPDATE role_menu_permissions p
SET can_view   = position('V' IN m.rights) > 0,
    can_create = position('C' IN m.rights) > 0,
    can_edit   = position('E' IN m.rights) > 0,
    can_delete = position('D' IN m.rights) > 0,
    can_export = position('X' IN m.rights) > 0,
    can_assign = position('A' IN m.rights) > 0
FROM matrix m
JOIN roles r ON r.key = m.role_key
WHERE p.role_id = r.id AND p.menu_key = m.menu_key;

-- Lead statuses -------------------------------------------------------------------
INSERT INTO lead_statuses (organization_id, reference, name, color, sort_order,
                           is_system, is_contacted, is_mql, is_won, is_terminal)
SELECT o.id, s.reference, s.name, s.color, s.sort_order, s.is_system, s.is_contacted, s.is_mql, s.is_won, s.is_terminal
FROM organizations o
CROSS JOIN (VALUES
  ('not_contacted',      'Not Contacted',      '#64748B',  1, true,  false, false, false, false),
  ('unprocessed',        'Unprocessed',        '#94A3B8',  2, true,  false, false, false, false),
  ('qualified',          'Qualified',          '#125FCD',  3, true,  true,  true,  false, false),
  ('working_deal',       'Working Deal',       '#5659C7',  4, true,  true,  true,  false, false),
  ('deal_closed',        'Deal Closed',        '#16A34A',  5, true,  true,  true,  true,  true),
  ('lost_deal',          'Lost Deal',          '#DC2626',  6, true,  true,  false, false, true),
  ('future_prospect',    'Future Prospect',    '#0891B2',  7, true,  true,  true,  false, false),
  ('did_not_respond',    'Did Not Respond',    '#D97706',  8, true,  true,  false, false, false),
  ('unqualified',        'Unqualified',        '#9F1239',  9, true,  true,  false, false, true),
  ('information_seeker', 'Information Seeker', '#DB2777', 10, false, true,  true,  false, false)
) AS s(reference, name, color, sort_order, is_system, is_contacted, is_mql, is_won, is_terminal)
WHERE o.slug = 'teg';

-- Labels ----------------------------------------------------------------------------
INSERT INTO labels (organization_id, name, color)
SELECT o.id, l.name, l.color
FROM organizations o
CROSS JOIN (VALUES
  ('Eligible',   '#16A34A'), ('Ineligible', '#DC2626'), ('Good',      '#0891B2'),
  ('Excellent',  '#125FCD'), ('Lost',       '#6B7280'), ('Signed',    '#5659C7'),
  ('Cold',       '#64748B'), ('Onboarding', '#D97706'), ('Approved',  '#15803D'),
  ('Delivered',  '#0D9488'), ('Invited',    '#DB2777')
) AS l(name, color)
WHERE o.slug = 'teg';

-- Programmes ------------------------------------------------------------------------
INSERT INTO programs (organization_id, code, name, country)
SELECT o.id, p.code, p.name, p.country
FROM organizations o
CROSS JOIN (VALUES
  ('USA_E2',       'USA E-2 Treaty Investor',            'United States'),
  ('USA_EB5',      'USA EB-5 Investor',                  'United States'),
  ('PT_D2',        'Portugal D2 Entrepreneur Visa',      'Portugal'),
  ('CA_SUV',       'Canada Start-up Visa',               'Canada'),
  ('CA_SKILLED',   'Canada Skilled Immigration',         'Canada'),
  ('STUDENT',      'Student Visa',                       NULL),
  ('CBI_RBI',      'Citizenship / Residency by Investment', NULL),
  ('CARIBBEAN',    'Caribbean Citizenship by Investment', NULL),
  ('UK_IFV',       'UK Innovator Founder Visa',          'United Kingdom'),
  ('UK_INNOVATOR', 'UK Innovator',                       'United Kingdom'),
  ('UK_BUSINESS',  'UK Business Formation',              'United Kingdom'),
  ('LV_GV',        'Latvia Golden Visa',                 'Latvia')
) AS p(code, name, country)
WHERE o.slug = 'teg';

-- Form-field aliases (most common keys from the Jaldi export) ------------------------
INSERT INTO form_field_aliases (organization_id, raw_key, canonical_field)
SELECT o.id, a.raw_key, a.canonical_field
FROM organizations o
CROSS JOIN (VALUES
  ('full_name', 'full_name'), ('full name', 'full_name'),
  ('email', 'email'), ('email_address', 'email'),
  ('phone_number', 'phone_raw'), ('phone', 'phone_raw'),
  ('whatsapp_number', 'whatsapp_e164'),
  ('whatsapp_number_(with_country_code)', 'whatsapp_e164'),
  ('whatsapp_contact_number_(with_country_code)', 'whatsapp_e164'),
  ('what_is_your_whatsapp_number?', 'whatsapp_e164'),
  ('city', 'city'), ('county/region', 'city'), ('state', 'city'),
  ('which_country_are_you_interested_in?', 'country_of_interest'),
  ('preferred_country_or_program_(if_known)', 'country_of_interest'),
  ('i am interested in', 'country_of_interest'),
  ('what_is_your_budget?', 'budget_text'),
  ('estimated_investment_budget_(usd)', 'budget_text'),
  ('_estimated_investment_budget_(in_usd)', 'budget_text'),
  ('what_is_your_approximate_investment_budget_for_the_d-2_visa?', 'budget_text'),
  ('what_is_your_approximate_investment_budget_for_the_e-2_visa?', 'budget_text'),
  ('what_is_your_estimated_budget_for_uk_immigration_and_business_setup?', 'budget_text'),
  ('what_is_your_highest_level_of_education?', 'education_level'),
  ('have_you_taken_any_english_proficiency_test_(ielts_/_pte)?', 'english_test_status'),
  ('have_you_completed_your_ielts/pte_or_any_other_language_test?', 'english_test_status'),
  ('what''s_your_age?', 'age_text'),
  ('are_you_including_family_members?', 'has_family'),
  ('will_you_be_applying_with_family_members?', 'has_family')
) AS a(raw_key, canonical_field)
WHERE o.slug = 'teg';
