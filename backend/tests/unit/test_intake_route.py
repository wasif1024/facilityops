from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.app.api.dependencies.auth import get_current_principal
from backend.app.api.dependencies.rate_limit import RateLimitState, enforce_rate_limit
from backend.app.api.routes.intake import get_coordinator, get_repository
from backend.app.core.config import Settings
from backend.app.domain.intake import IntakeResult, IntakeStatus
from backend.app.main import create_app


class FakeRepository:
    def __init__(self, existing: IntakeResult | None = None) -> None:
        self.existing = existing
        self.begin_calls = []

    async def begin_request(self, command):
        self.begin_calls.append(command)
        return self.existing


class FakeCoordinator:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    async def process(self, command):
        self.calls.append(command)
        if self.error:
            raise self.error
        return IntakeResult(
            request_id=command.request_id,
            status=IntakeStatus.HUMAN_REVIEW,
            reason_code="content_safety_unavailable",
        )


def make_client(
    repository: FakeRepository,
    coordinator: FakeCoordinator,
) -> TestClient:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_coordinator] = lambda: coordinator
    app.dependency_overrides[get_current_principal] = lambda: type(
        "LocalPrincipal", (), {"subject": "tenant-test", "roles": {"tenant"}}
    )()
    app.dependency_overrides[enforce_rate_limit] = lambda: RateLimitState(
        limit=30,
        remaining=29,
        reset_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_intake_route_returns_fail_closed_review_and_rate_headers():
    repository = FakeRepository()
    coordinator = FakeCoordinator()
    with make_client(repository, coordinator) as client:
        response = client.post(
            "/api/v1/intake",
            json={
                "request_id": "e63a21bc-5d6d-4581-8da3-dfd439c434e5",
                "text": "sink leaking",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "human_review"
    assert response.headers["x-ratelimit-limit"] == "30"
    assert response.headers["x-ratelimit-remaining"] == "29"
    assert len(coordinator.calls) == 1


def test_idempotent_replay_does_not_call_coordinator():
    request_id = UUID("e63a21bc-5d6d-4581-8da3-dfd439c434e5")
    repository = FakeRepository(
        existing=IntakeResult(
            request_id=request_id,
            status=IntakeStatus.HUMAN_REVIEW,
            reason_code="previously_processed",
        )
    )
    coordinator = FakeCoordinator()
    with make_client(repository, coordinator) as client:
        response = client.post(
            "/api/v1/intake",
            json={"request_id": str(request_id), "text": "sink leaking"},
        )

    assert response.status_code == 200
    assert response.json()["reason_code"] == "previously_processed"
    assert coordinator.calls == []


def test_endpoint_errors_do_not_echo_request_text():
    repository = FakeRepository()
    coordinator = FakeCoordinator(RuntimeError("internal processing failure"))
    raw_text = "Private tenant name Avery Synthetic reported a leak"
    with make_client(repository, coordinator) as client:
        response = client.post(
            "/api/v1/intake",
            json={
                "request_id": "e63a21bc-5d6d-4581-8da3-dfd439c434e5",
                "text": raw_text,
            },
        )

    assert response.status_code == 503
    assert raw_text not in response.text
