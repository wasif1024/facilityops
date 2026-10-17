from uuid import uuid4

import pytest

from backend.app.domain.intake import (
    Classification,
    IntakeCommand,
    IntakeStatus,
    MaintenanceCategory,
    PiiRedaction,
)
from backend.app.domain.workflow import IntakeCoordinator


class FakePiiRedactor:
    def __init__(self, redacted_text: str = "[PERSON] reported a leaking sink") -> None:
        self.redacted_text = redacted_text
        self.calls: list[str] = []
        self.attachments: list[str] = []

    async def redact(self, text: str, language: str = "en", *, attachment: str = "") -> PiiRedaction:
        self.calls.append(text)
        self.attachments.append(attachment)
        if not attachment:
            return PiiRedaction(
                redacted_text=self.redacted_text,
                encrypted_mapping=b"encrypted-synthetic-mapping",
            )
        return PiiRedaction(
            redacted_text="\n".join(part for part in (text, attachment) if part),
            encrypted_mapping=b"encrypted-synthetic-mapping",
            redacted_user_text=text,
            redacted_attachment=attachment,
        )


class FakeSafetyInspector:
    def __init__(
        self,
        reason: str | None = None,
        image_reason: str | None = None,
        image_error: Exception | None = None,
    ) -> None:
        self.reason = reason
        self.image_reason = image_reason
        self.image_error = image_error
        self.calls: list[str] = []
        self.documents: list[list[str]] = []
        self.images: list[bytes] = []

    async def assess(self, redacted_text: str, *, documents=()) -> str | None:
        self.calls.append(redacted_text)
        self.documents.append(list(documents))
        return self.reason

    async def assess_image(self, image: bytes) -> str | None:
        self.images.append(image)
        if self.image_error:
            raise self.image_error
        return self.image_reason


class FakeClassifier:
    def __init__(self, classification: Classification | None = None) -> None:
        self.classification = classification or Classification(
            category=MaintenanceCategory.PLUMBING,
            priority=2,
            confidence=0.94,
            reason_code="leak_reported",
        )
        self.calls: list[str] = []

    async def classify(self, redacted_text: str) -> Classification:
        self.calls.append(redacted_text)
        return self.classification


class FakeWorkOrderWriter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object):
        self.calls.append(kwargs)
        return uuid4()


class FakeDecisionWriter:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record_result(self, **kwargs: object) -> None:
        self.records.append(kwargs)


@pytest.fixture
def dependencies():
    redactor = FakePiiRedactor()
    safety = FakeSafetyInspector()
    classifier = FakeClassifier()
    work_orders = FakeWorkOrderWriter()
    decision_writer = FakeDecisionWriter()
    handoffs: list[tuple[object, str]] = []

    async def emergency_handoff(request_id, reason_code):
        handoffs.append((request_id, reason_code))

    coordinator = IntakeCoordinator(
        pii_redactor=redactor,
        safety_inspector=safety,
        classifier=classifier,
        work_order_writer=work_orders,
        decision_writer=decision_writer,
        emergency_handoff=emergency_handoff,
    )
    return (
        coordinator,
        redactor,
        safety,
        classifier,
        work_orders,
        handoffs,
        decision_writer,
    )


@pytest.mark.asyncio
async def test_emergency_bypasses_ai_and_routes_to_human(dependencies):
    (
        coordinator,
        redactor,
        safety,
        classifier,
        work_orders,
        handoffs,
        decision_writer,
    ) = dependencies
    command = IntakeCommand(text="There is a gas leak in the utility room")

    result = await coordinator.process(command)

    assert result.status is IntakeStatus.EMERGENCY_HANDOFF
    assert result.reason_code == "emergency_gas"
    assert handoffs == [(command.request_id, "emergency_gas")]
    assert redactor.calls == []
    assert safety.calls == []
    assert classifier.calls == []
    assert work_orders.calls == []
    assert decision_writer.records[0]["redacted_text"] is None


@pytest.mark.asyncio
async def test_classifier_only_receives_redacted_text(dependencies):
    coordinator, redactor, safety, classifier, work_orders, _, decision_writer = (
        dependencies
    )

    result = await coordinator.process(
        IntakeCommand(text="Alex Rivera reports a leaking sink")
    )

    assert result.status is IntakeStatus.WORK_ORDER_CREATED
    assert redactor.calls == ["Alex Rivera reports a leaking sink"]
    assert safety.calls == ["[PERSON] reported a leaking sink"]
    assert classifier.calls == ["[PERSON] reported a leaking sink"]
    assert work_orders.calls[0]["redacted_text"] == "[PERSON] reported a leaking sink"
    assert work_orders.calls[0]["encrypted_mapping"] == b"encrypted-synthetic-mapping"
    assert (
        decision_writer.records[0]["encrypted_mapping"]
        == b"encrypted-synthetic-mapping"
    )


@pytest.mark.asyncio
async def test_safety_failure_stops_classification_and_creation(dependencies):
    coordinator, _, safety, classifier, work_orders, _, _ = dependencies
    safety.reason = "prompt_attack_detected"

    result = await coordinator.process(IntakeCommand(text="Please inspect my unit"))

    assert result.status is IntakeStatus.HUMAN_REVIEW
    assert result.reason_code == "prompt_attack_detected"
    assert classifier.calls == []
    assert work_orders.calls == []


@pytest.mark.asyncio
async def test_uncertain_classification_requires_human_review(dependencies):
    coordinator, _, _, classifier, work_orders, _, _ = dependencies
    classifier.classification = Classification(
        category=MaintenanceCategory.OTHER,
        priority=2,
        confidence=0.51,
        reason_code="ambiguous_request",
    )

    result = await coordinator.process(IntakeCommand(text="Something sounds strange"))

    assert result.status is IntakeStatus.HUMAN_REVIEW
    assert result.reason_code == "classification_uncertain"
    assert work_orders.calls == []


@pytest.mark.asyncio
async def test_high_priority_work_order_is_created_pending_approval(dependencies):
    coordinator, _, _, classifier, work_orders, _, _ = dependencies
    classifier.classification = Classification(
        category=MaintenanceCategory.ELECTRICAL,
        priority=5,
        confidence=0.98,
        reason_code="power_failure",
    )

    result = await coordinator.process(IntakeCommand(text="Power is out in the lift"))

    assert result.status is IntakeStatus.PENDING_APPROVAL
    assert work_orders.calls[0]["approval_required"] is True
