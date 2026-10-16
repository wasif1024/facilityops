from datetime import UTC, datetime
from uuid import UUID

from backend.app.api.container import RateLimitState
from backend.app.domain.intake import IntakeResult, IntakeStatus

from .rpc_fakes import call, make_client, principal_for


class FakeRepository:
    def __init__(self, existing: IntakeResult | None = None) -> None:
        self.existing = existing
        self.begin_calls = []

    async def begin_request(self, command, *, submitted_by_user_id=None):
        self.begin_calls.append((command, submitted_by_user_id))
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


REQUEST_ID = "e63a21bc-5d6d-4581-8da3-dfd439c434e5"


def test_submit_intake_returns_result_and_rate_headers():
    repository, coordinator = FakeRepository(), FakeCoordinator()
    with make_client(repository=repository, coordinator=coordinator) as client:
        response = call(
            client, "IntakeService/SubmitIntake", {"requestId": REQUEST_ID, "text": "sink leaking"}
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "result": {
            "requestId": REQUEST_ID,
            "status": "INTAKE_STATUS_HUMAN_REVIEW",
            "reasonCode": "content_safety_unavailable",
        }
    }
    assert response.headers["x-ratelimit-limit"] == "30"
    assert response.headers["x-ratelimit-remaining"] == "29"
    assert repository.begin_calls[0][1] == principal_for("alice").user_id


def test_idempotent_replay_does_not_call_coordinator():
    repository = FakeRepository(
        existing=IntakeResult(
            request_id=UUID(REQUEST_ID),
            status=IntakeStatus.HUMAN_REVIEW,
            reason_code="previously_processed",
        )
    )
    coordinator = FakeCoordinator()
    with make_client(repository=repository, coordinator=coordinator) as client:
        response = call(
            client, "IntakeService/SubmitIntake", {"requestId": REQUEST_ID, "text": "sink leaking"}
        )

    assert response.json()["result"]["reasonCode"] == "previously_processed"
    assert coordinator.calls == []


def test_processing_errors_do_not_echo_request_text():
    raw_text = "Private tenant name Avery Synthetic reported a leak"
    coordinator = FakeCoordinator(RuntimeError(f"failure while handling {raw_text}"))
    with make_client(repository=FakeRepository(), coordinator=coordinator) as client:
        response = call(client, "IntakeService/SubmitIntake", {"text": raw_text})

    assert response.status_code == 503
    assert response.json()["code"] == "unavailable"
    assert "Avery" not in response.text


def test_invalid_text_names_the_field_only():
    with make_client(repository=FakeRepository(), coordinator=FakeCoordinator()) as client:
        empty = call(client, "IntakeService/SubmitIntake", {"text": ""})
        bad_id = call(client, "IntakeService/SubmitIntake", {"requestId": "nope", "text": "x"})

    assert empty.json() == {"code": "invalid_argument", "message": "text is required"}
    assert bad_id.json()["code"] == "invalid_argument"


def test_rate_limited_requests_are_rejected_before_processing():
    async def exhausted(user_id):
        return RateLimitState(
            allowed=False, limit=30, remaining=0, reset_at=datetime(2030, 1, 1, tzinfo=UTC)
        )

    repository, coordinator = FakeRepository(), FakeCoordinator()
    with make_client(repository=repository, coordinator=coordinator, rate_limit=exhausted) as client:
        response = call(client, "IntakeService/SubmitIntake", {"text": "sink leaking"})

    assert response.status_code == 429
    assert response.json()["code"] == "resource_exhausted"
    assert repository.begin_calls == [] and coordinator.calls == []


def test_intake_requires_submit_permission():
    repository, coordinator = FakeRepository(), FakeCoordinator()
    with make_client(repository=repository, coordinator=coordinator) as client:
        response = call(client, "IntakeService/SubmitIntake", {"text": "sink leaking"}, user="root")

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert repository.begin_calls == [] and coordinator.calls == []
