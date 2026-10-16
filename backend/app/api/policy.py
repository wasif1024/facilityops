"""Which permissions each RPC requires: the single source of truth for API authorization.

Deny by default: an RPC missing from this table is refused, and a unit test fails if
any generated RPC lacks an entry. A plain set means "all of these"; ``AnyOf`` means "at
least one", for RPCs whose handler narrows access further (for example a technician
reading only their own work orders). An empty set means "any authenticated user".
"""

from __future__ import annotations

from backend.app.domain.rbac import Permission

AUTHENTICATED: frozenset[Permission] = frozenset()


class AnyOf(frozenset):
    """Satisfied by any one of the permissions; the handler enforces the rest."""


def is_satisfied(required: frozenset[Permission], granted: frozenset[Permission]) -> bool:
    if isinstance(required, AnyOf):
        return bool(required & granted)
    return required.issubset(granted)


def _rpc(service: str, method: str) -> str:
    return f"facilityops.v1.{service}/{method}"


RPC_PERMISSIONS: dict[str, frozenset[Permission]] = {
    _rpc("AccountService", "GetMe"): AUTHENTICATED,
    _rpc("IntakeService", "SubmitIntake"): frozenset({Permission.INTAKE_SUBMIT}),
    _rpc("IntakeService", "ListMyRequests"): frozenset({Permission.INTAKE_READ_OWN}),
    _rpc("IntakeService", "ListRequests"): frozenset({Permission.INTAKE_READ_ALL}),
    _rpc("IntakeService", "GetRequest"): AnyOf({Permission.INTAKE_READ_OWN, Permission.INTAKE_READ_ALL}),
    _rpc("IntakeService", "RevealRequestDetails"): AnyOf(
        {Permission.PII_REVEAL_ALL, Permission.PII_REVEAL_ASSIGNED}
    ),
    _rpc("ReviewQueueService", "ListReviewCases"): frozenset({Permission.REVIEW_QUEUE_MANAGE}),
    _rpc("ReviewQueueService", "ResolveReviewCase"): frozenset({Permission.REVIEW_QUEUE_MANAGE}),
    _rpc("WorkOrderService", "ListWorkOrders"): AnyOf(
        {Permission.WORK_ORDERS_READ_ALL, Permission.WORK_ORDERS_READ_ASSIGNED}
    ),
    _rpc("WorkOrderService", "GetWorkOrder"): AnyOf(
        {Permission.WORK_ORDERS_READ_ALL, Permission.WORK_ORDERS_READ_ASSIGNED}
    ),
    _rpc("WorkOrderService", "ApproveWorkOrder"): frozenset({Permission.WORK_ORDERS_APPROVE}),
    _rpc("WorkOrderService", "RejectWorkOrder"): frozenset({Permission.WORK_ORDERS_APPROVE}),
    _rpc("WorkOrderService", "AssignWorkOrder"): frozenset({Permission.WORK_ORDERS_ASSIGN}),
    _rpc("WorkOrderService", "CompleteWorkOrder"): frozenset(
        {Permission.WORK_ORDERS_UPDATE_ASSIGNED}
    ),
    _rpc("WorkOrderService", "ListTechnicians"): frozenset({Permission.WORK_ORDERS_ASSIGN}),
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
