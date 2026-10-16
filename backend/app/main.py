from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.app.core.config import Settings, get_settings
from backend.app.middleware.security import (
    RequestBodyLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from backend.app.api.routes.intake import router as intake_router


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    if app_settings.app_env == "production":
        if (
            not app_settings.auth_enabled
            or not app_settings.entra_tenant_id
            or not app_settings.entra_api_audience
        ):
            raise ValueError("Production requires Entra authentication settings")
        if app_settings.rate_limit_hmac_secret == "development-only-change-me":
            raise ValueError("Production requires a dedicated rate-limit HMAC secret")
        if "*" in app_settings.frontend_origins or "*" in app_settings.allowed_hosts:
            raise ValueError("Production CORS origins and hosts must be explicit")
        if not app_settings.frontend_origins or any(
            not origin.startswith("https://") for origin in app_settings.frontend_origins
        ):
            raise ValueError("Production browser origins must use HTTPS")
    app = FastAPI(
        title="FacilityOps AI API",
        version="0.1.0",
        docs_url=None if app_settings.app_env == "production" else "/docs",
        redoc_url=None if app_settings.app_env == "production" else "/redoc",
    )
    app.dependency_overrides[get_settings] = lambda: app_settings

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=app_settings.max_request_body_bytes,
    )
    app.include_router(intake_router)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=app_settings.allowed_hosts,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.frontend_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
    )

    @app.get("/health/live", tags=["health"])
    def health_live() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
