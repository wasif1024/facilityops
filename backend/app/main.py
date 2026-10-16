"""ASGI host: security middleware, a health endpoint and the Connect RPC services.

Run with ``uvicorn backend.app.main:create_app --factory``. There is deliberately no
module-level app, so importing this module (for example in tests) configures nothing.

The frontend talks to the backend over Connect RPC (Connect, gRPC-Web or gRPC
protocols) using the contracts in ``proto/facilityops/v1``. Each service is mounted at
its own path, for example ``/facilityops.v1.IntakeService/SubmitIntake``.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.app.api.auth import DEV_USER_HEADER
from backend.app.api.container import Container
from backend.app.api.interceptors import AuthInterceptor, ErrorBoundaryInterceptor
from backend.app.api.rpc.intake import IntakeRpc
from backend.app.api.rpc.operations import ReviewQueueRpc, WorkOrderRpc
from backend.app.api.rpc.reports import EvaluationRpc, MonitoringRpc
from backend.app.api.rpc.users import AccountRpc, UserAdminRpc
from backend.app.core.config import Settings, get_settings
from backend.app.core.telemetry import configure_telemetry
from backend.app.gen.facilityops.v1.account_connect import AccountServiceASGIApplication
from backend.app.gen.facilityops.v1.evaluations_connect import (
    EvaluationServiceASGIApplication,
)
from backend.app.gen.facilityops.v1.intake_connect import IntakeServiceASGIApplication
from backend.app.gen.facilityops.v1.monitoring_connect import (
    MonitoringServiceASGIApplication,
)
from backend.app.gen.facilityops.v1.operations_connect import (
    ReviewQueueServiceASGIApplication,
    WorkOrderServiceASGIApplication,
)
from backend.app.gen.facilityops.v1.users_connect import UserAdminServiceASGIApplication
from backend.app.middleware.security import (
    RequestBodyLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)

# Request headers browsers may send with Connect, gRPC-Web and our own conventions.
_CONNECT_REQUEST_HEADERS = [
    "Authorization",
    "Content-Type",
    "Connect-Protocol-Version",
    "Connect-Timeout-Ms",
    "Grpc-Timeout",
    "X-Grpc-Web",
    "X-User-Agent",
    "X-Request-ID",
]
# Response headers and trailers the browser client must be able to read.
_CONNECT_EXPOSED_HEADERS = [
    "Grpc-Status",
    "Grpc-Message",
    "Grpc-Status-Details-Bin",
    "X-Request-ID",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
]


def _check_production_settings(settings: Settings) -> None:
    if settings.app_env != "production":
        return
    if not settings.auth_enabled or not settings.entra_tenant_id or not settings.entra_api_audience:
        raise ValueError("Production requires Entra authentication settings")
    if settings.rate_limit_hmac_secret == "development-only-change-me":
        raise ValueError("Production requires a dedicated rate-limit HMAC secret")
    if "*" in settings.frontend_origins or "*" in settings.allowed_hosts:
        raise ValueError("Production CORS origins and hosts must be explicit")
    if not settings.frontend_origins or any(
        not origin.startswith("https://") for origin in settings.frontend_origins
    ):
        raise ValueError("Production browser origins must use HTTPS")


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    _check_production_settings(app_settings)
    # Before the app object exists, so the distro can instrument it.
    configure_telemetry(app_settings)
    services = container or Container.default(app_settings)

    # FastAPI is only the host; the API contract is the protobuf schema, so no OpenAPI docs.
    app = FastAPI(
        title="FacilityOps AI", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None
    )

    interceptors = (
        ErrorBoundaryInterceptor(),
        AuthInterceptor(app_settings, services.resolve_principal),
    )
    options = {
        "interceptors": interceptors,
        # Match the body-size middleware so photo and voice uploads fit.
        "read_max_bytes": app_settings.max_request_body_bytes,
    }
    for rpc_app in (
        AccountServiceASGIApplication(AccountRpc(), **options),
        IntakeServiceASGIApplication(IntakeRpc(services), **options),
        UserAdminServiceASGIApplication(UserAdminRpc(services), **options),
        EvaluationServiceASGIApplication(EvaluationRpc(services), **options),
        MonitoringServiceASGIApplication(MonitoringRpc(services), **options),
        ReviewQueueServiceASGIApplication(ReviewQueueRpc(services), **options),
        WorkOrderServiceASGIApplication(WorkOrderRpc(services), **options),
    ):
        app.mount(rpc_app.path, rpc_app)

    @app.get("/health/live", tags=["health"])
    def health_live() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=app_settings.max_request_body_bytes)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=app_settings.allowed_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.frontend_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            *_CONNECT_REQUEST_HEADERS,
            *([] if app_settings.app_env == "production" else [DEV_USER_HEADER]),
        ],
        expose_headers=_CONNECT_EXPOSED_HEADERS,
        max_age=7200,
    )
    return app

