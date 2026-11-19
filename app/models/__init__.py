"""SQLAlchemy ORM models.

The schema itself is owned by the SQL migrations in ``migrations/sql``. Models
mirror those tables for querying and never create or alter them.
"""

from app.models.base import Base
from app.models.configuration import Label, LeadStatus, Program
from app.models.identity import (
    AppMenu,
    Role,
    RoleMenuPermission,
    Team,
    TeamMember,
    User,
    UserSession,
)
from app.models.leads import Activity, Campaign, Contact, Lead, LeadAssignment, LeadLabel, Task
from app.models.logs import AuditLog, AuthEvent
from app.models.organization import Organization

__all__ = [
    "Activity",
    "Campaign",
    "Contact",
    "Lead",
    "LeadAssignment",
    "LeadLabel",
    "Task",
    "AppMenu",
    "AuditLog",
    "AuthEvent",
    "Base",
    "Label",
    "LeadStatus",
    "Organization",
    "Program",
    "Role",
    "RoleMenuPermission",
    "Team",
    "TeamMember",
    "User",
    "UserSession",
]
