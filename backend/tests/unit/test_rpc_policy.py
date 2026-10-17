import pytest
from connectrpc.code import Code
from connectrpc.errors import ConnectError

from backend.app.api.interceptors import ErrorBoundaryInterceptor
from backend.app.api.policy import RPC_PERMISSIONS
from backend.app.gen.facilityops.v1 import (
    account_connect,
    evaluations_connect,
    incidents_connect,
    intake_connect,
    monitoring_connect,
    operations_connect,
    users_connect,
)

from .rpc_fakes import call, make_client

SERVICES = (
    account_connect.AccountService,
    intake_connect.IntakeService,
    incidents_connect.IncidentService,
    users_connect.UserAdminService,
    evaluations_connect.EvaluationService,
    monitoring_connect.MonitoringService,
    operations_connect.ReviewQueueService,
    operations_connect.WorkOrderService,
)


def _generated_rpcs() -> set[str]:
    return {
        f"{service.desc().type_name}/{method.name}"
        for service in SERVICES
        for method in service.desc().methods
    }


def test_every_rpc_has_an_access_policy():
    assert _generated_rpcs() == set(RPC_PERMISSIONS)


def test_rpc_without_policy_is_denied(monkeypatch):
    monkeypatch.delitem(RPC_PERMISSIONS, "facilityops.v1.AccountService/GetMe")
    with make_client() as client:
        response = call(client, "AccountService/GetMe", {})

    assert response.status_code == 403
    assert response.json()["message"] == "No access policy for this RPC"


def test_any_authenticated_user_can_read_their_account():
    with make_client() as client:
        response = call(client, "AccountService/GetMe", {})

    assert response.status_code == 200
    assert response.json()["roles"] == ["tenant"]


@pytest.mark.parametrize(
    "rpc",
    [
        "UserAdminService/ListUsers",
        "UserAdminService/SetUserRoles",
        "UserAdminService/ListAuditEvents",
        "EvaluationService/StartEvaluationRun",
        "MonitoringService/GetAgentReport",
    ],
)
def test_tenant_cannot_call_admin_rpcs(rpc):
    with make_client() as client:
        response = call(client, rpc, {})

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_invalid_dev_user_header_is_rejected():
    with make_client() as client:
        response = call(client, "AccountService/GetMe", {}, user="Robert'); DROP")

    assert response.json()["code"] == "invalid_argument"


@pytest.mark.asyncio
async def test_error_boundary_hides_unexpected_error_details(caplog):
    class Ctx:
        request_headers: dict[str, str] = {}

        class method:
            service_name = "facilityops.v1.Test"
            name = "Leak"

    async def failing(request, ctx):
        raise RuntimeError("INSERT ... VALUES ('Avery Synthetic, unit 4B')")

    with pytest.raises(ConnectError) as raised:
        await ErrorBoundaryInterceptor().intercept_unary(failing, object(), Ctx())

    assert raised.value.code is Code.INTERNAL
    assert raised.value.message == "Internal error"
    # Logs are exported to Application Insights: type and RPC only, never the message.
    assert "Unhandled RuntimeError in facilityops.v1.Test/Leak" in caplog.text
    assert "Avery" not in caplog.text
