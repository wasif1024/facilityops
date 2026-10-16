"""Which permissions each RPC requires: the single source of truth for API authorization.

Deny by default: an RPC missing from this table is refused, and a unit test fails if
any generated RPC lacks an entry. An empty set means "any authenticated user".
"""

from __future__ import annotations

from backend.app.domain.rbac import Permission

AUTHENTICATED: frozenset[Permission] = frozenset()


def _rpc(service: str, method: str) -> str:
    return f"facilityops.v1.{service}/{method}"


RPC_PERMISSIONS: dict[str, frozenset[Permission]] = {
    _rpc("AccountService", "GetMe"): AUTHENTICATED,
    _rpc("IntakeService", "SubmitIntake"): frozenset({Permission.INTAKE_SUBMIT}),
    _rpc("UserAdminService", "ListUsers"): frozenset({Permission.USERS_READ}),
    _rpc("UserAdminService", "GetUser"): frozenset({Permission.USERS_READ}),
    _rpc("UserAdminService", "CreateUser"): frozenset({Permission.USERS_MANAGE}),
    _rpc("UserAdminService", "UpdateUser"): frozenset({Permission.USERS_MANAGE}),
    _rpc("UserAdminService", "SetUserRoles"): frozenset({Permission.USERS_MANAGE}),
    _rpc("UserAdminService", "ListRoles"): frozenset({Permission.ROLES_READ}),
    _rpc("UserAdminService", "ListAuditEvents"): frozenset({Permission.AUDIT_READ}),
    _rpc("EvaluationService", "ListEvaluationRuns"): frozenset({Permission.EVALUATIONS_READ}),
    _rpc("EvaluationService", "GetEvaluationRun"): frozenset({Permission.EVALUATIONS_READ}),
    _rpc("EvaluationService", "StartEvaluationRun"): frozenset({Permission.EVALUATIONS_RUN}),
    _rpc("EvaluationService", "GetMetricTrend"): frozenset({Permission.EVALUATIONS_READ}),
    _rpc("MonitoringService", "GetAgentReport"): frozenset({Permission.MONITORING_READ}),
    _rpc("MonitoringService", "ListAgentRuns"): frozenset({Permission.MONITORING_READ}),
    _rpc("MonitoringService", "GetIntakeReport"): frozenset({Permission.MONITORING_READ}),
}


def required_permissions(service_name: str, method_name: str) -> frozenset[Permission] | None:
    """Permissions for an RPC, or None when the RPC has no policy (and must be denied)."""
    return RPC_PERMISSIONS.get(f"{service_name}/{method_name}")
