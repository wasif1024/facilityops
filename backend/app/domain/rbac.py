"""Role-based access control vocabulary.

Entra ID proves who a caller is; the application database decides what they may do.
Roles and their permissions are seeded by migrations ``0002`` and ``0003`` and read from the
database at request time. ``SYSTEM_ROLE_PERMISSIONS`` mirrors that seed so tests can
detect drift between code and data.
"""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    INTAKE_SUBMIT = "intake:submit"
    INTAKE_READ_OWN = "intake:read_own"
    INTAKE_READ_ALL = "intake:read_all"
    WORK_ORDERS_READ_ASSIGNED = "work_orders:read_assigned"
    WORK_ORDERS_READ_ALL = "work_orders:read_all"
    WORK_ORDERS_APPROVE = "work_orders:approve"
    WORK_ORDERS_ASSIGN = "work_orders:assign"
    WORK_ORDERS_UPDATE_ASSIGNED = "work_orders:update_assigned"
    REVIEW_QUEUE_MANAGE = "review_queue:manage"
    PII_REVEAL_ASSIGNED = "pii:reveal_assigned"
    PII_REVEAL_ALL = "pii:reveal_all"
    USERS_READ = "users:read"
    USERS_MANAGE = "users:manage"
    ROLES_READ = "roles:read"
    AUDIT_READ = "audit:read"
    EVALUATIONS_READ = "evaluations:read"
    EVALUATIONS_RUN = "evaluations:run"
    MONITORING_READ = "monitoring:read"


class SystemRole(StrEnum):
    TENANT = "tenant"
    TECHNICIAN = "technician"
    FACILITY_MANAGER = "facility_manager"
    ADMIN = "admin"


# Separation of duties: the admin role manages access and reads aggregate reports,
# but cannot reveal PII or approve work. Operational authority stays with managers.
SYSTEM_ROLE_PERMISSIONS: dict[SystemRole, frozenset[Permission]] = {
    SystemRole.TENANT: frozenset({Permission.INTAKE_SUBMIT, Permission.INTAKE_READ_OWN}),
    SystemRole.TECHNICIAN: frozenset(
        {
            Permission.INTAKE_SUBMIT,
            Permission.WORK_ORDERS_READ_ASSIGNED,
            Permission.WORK_ORDERS_UPDATE_ASSIGNED,
            Permission.PII_REVEAL_ASSIGNED,
        }
    ),
    SystemRole.FACILITY_MANAGER: frozenset(
        {
            Permission.INTAKE_SUBMIT,
            Permission.INTAKE_READ_ALL,
            Permission.WORK_ORDERS_READ_ALL,
            Permission.WORK_ORDERS_APPROVE,
            Permission.WORK_ORDERS_ASSIGN,
            Permission.REVIEW_QUEUE_MANAGE,
            Permission.PII_REVEAL_ALL,
            Permission.EVALUATIONS_READ,
            Permission.MONITORING_READ,
        }
    ),
    SystemRole.ADMIN: frozenset(
        {
            Permission.USERS_READ,
            Permission.USERS_MANAGE,
            Permission.ROLES_READ,
            Permission.AUDIT_READ,
            Permission.EVALUATIONS_READ,
            Permission.EVALUATIONS_RUN,
            Permission.MONITORING_READ,
        }
    ),
}


class IdentityProvider(StrEnum):
    # Workforce Entra ID: staff.
    ENTRA = "entra"
    # Entra External ID: residents who sign up themselves.
    ENTRA_EXTERNAL = "entra_external"
    DEVELOPMENT = "development"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
