"""SQLAlchemy ORM models.

The schema itself is owned by the SQL migrations in ``migrations/sql``. Models
mirror those tables for querying and never create or alter them.
"""

from app.models.ads import (
    Ad,
    AdAccount,
    AdCampaign,
    AdInsightDaily,
    AdSet,
    Integration,
    IntegrationPage,
)
from app.models.base import Base
from app.models.configuration import Label, LeadStatus, Market, Program
from app.models.identity import (
    AppMenu,
    Role,
    RoleMenuPermission,
    Team,
    TeamMember,
    User,
    UserSession,
)
from app.models.intake import ApiKey, Import, ImportRow, WebhookEvent
from app.models.leads import (
    Activity,
    Campaign,
    CampaignAgent,
    Contact,
    Lead,
    LeadAssignment,
    LeadLabel,
    Task,
)
from app.models.logs import AuditLog, AuthEvent
from app.models.organization import Organization

__all__ = [
    "Activity",
    "Ad",
    "AdAccount",
    "AdCampaign",
    "AdInsightDaily",
    "AdSet",
    "Integration",
    "IntegrationPage",
    "ApiKey",
    "AppMenu",
    "AuditLog",
    "AuthEvent",
    "Base",
    "Campaign",
    "CampaignAgent",
    "Contact",
    "Import",
    "ImportRow",
    "Label",
    "Lead",
    "LeadAssignment",
    "LeadLabel",
    "LeadStatus",
    "Market",
    "Organization",
    "Program",
    "Role",
    "RoleMenuPermission",
    "Task",
    "Team",
    "TeamMember",
    "User",
    "UserSession",
    "WebhookEvent",
]
