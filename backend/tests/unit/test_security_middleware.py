import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app


def make_client(**overrides: object) -> TestClient:
    settings = Settings(
        app_env="test",
        frontend_origins=["http://localhost:5173"],
        allowed_hosts=["testserver", "localhost"],
        **overrides,
    )
    return TestClient(create_app(settings))


def test_health_has_request_id_and_security_headers() -> None:
    with make_client() as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


def test_valid_request_id_is_preserved() -> None:
    request_id = "9a2c2f3f-b04d-4fbd-a52f-2a751f9cd9ca"
    with make_client() as client:
        response = client.get("/health/live", headers={"X-Request-ID": request_id})

    assert response.headers["x-request-id"] == request_id


def test_cors_allows_only_configured_origin() -> None:
    with make_client() as client:
        allowed = client.options(
            "/health/live",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = client.get(
            "/health/live", headers={"Origin": "https://untrusted.example"}
        )

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers


def test_body_size_limit_applies_to_chunked_requests() -> None:
    with make_client(max_request_body_bytes=1024) as client:
        response = client.post("/health/live", content=b"x" * 1025)

    assert response.status_code == 413


def test_production_requires_entra_authentication() -> None:
    with pytest.raises(ValueError, match="Production requires authentication"):
        create_app(Settings(app_env="production"))


def test_production_rejects_http_frontend_origin() -> None:
    settings = Settings(
        app_env="production",
        auth_enabled=True,
        entra_tenant_id="tenant-id",
        entra_api_audience="api://facilityops",
        rate_limit_hmac_secret="production-secret-not-for-development",
        frontend_origins=["http://facilityops.example"],
    )

    with pytest.raises(ValueError, match="HTTPS"):
        create_app(settings)
