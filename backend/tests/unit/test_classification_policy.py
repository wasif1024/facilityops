import pytest

from backend.app.domain.classification_policy import ClassificationPolicy, Route
from backend.app.domain.intake import (
    Classification,
    IntakeCommand,
    IntakeStatus,
    MaintenanceCategory,
)
from backend.app.domain.workflow import IntakeCoordinator

from .test_intake_workflow import (
    FakeDecisionWriter,
    FakePiiRedactor,
    FakeSafetyInspector,
    FakeWorkOrderWriter,
)

POLICY = ClassificationPolicy()


def c(confidence=0.9, priority=3, category=MaintenanceCategory.ELECTRICAL, review=False):
    return Classification(
        category=category,
        priority=priority,
        confidence=confidence,
        reason_code="test",
        requires_human_review=review,
    )


@pytest.mark.parametrize(
    ("first", "confirmations", "route", "reason"),
    [
        (c(0.86), [], Route.WORK_ORDER, "classified"),
        (c(0.86, priority=4), [], Route.PENDING_APPROVAL, "approval_required"),
        (c(0.65), [], Route.HUMAN_REVIEW, "classification_uncertain"),
        (c(0.9, review=True), [], Route.HUMAN_REVIEW, "classification_uncertain"),
        # The noisy band: agreement automates, disagreement goes to a person.
        (c(0.74), [c(0.78), c(0.74)], Route.WORK_ORDER, "classified_by_agreement"),
        (c(0.78, priority=4), [c(0.74, priority=4)] * 2, Route.PENDING_APPROVAL, "approval_required"),
        (c(0.74), [c(0.78), c(0.78, priority=2)], Route.HUMAN_REVIEW, "classification_unconfirmed"),
        (c(0.74), [c(0.78), c(0.78, category=MaintenanceCategory.HVAC)], Route.HUMAN_REVIEW, "classification_unconfirmed"),
        (c(0.74), [c(0.78), None], Route.HUMAN_REVIEW, "classification_unconfirmed"),
        (c(0.74), [c(0.78), c(0.9, review=True)], Route.HUMAN_REVIEW, "classification_unconfirmed"),
    ],
)
def test_routing_decisions(first, confirmations, route, reason):
    decision = POLICY.decide(first, confirmations)

    assert (decision.route, decision.reason_code) == (route, reason)


def test_only_the_band_needs_confirmation():
    assert POLICY.needs_confirmation(c(0.70)) and POLICY.needs_confirmation(c(0.79))
    assert not POLICY.needs_confirmation(c(0.80))
    assert not POLICY.needs_confirmation(c(0.69))
    assert not POLICY.needs_confirmation(c(0.75, review=True))


def test_borderline_answer_without_confirmations_is_a_programming_error():
    with pytest.raises(ValueError):
        POLICY.decide(c(0.74), [])


def test_disabled_confirmation_falls_back_to_one_threshold():
    single = ClassificationPolicy(confirmations=0, minimum_confidence=0.75)

    assert single.decide(c(0.74)).route is Route.HUMAN_REVIEW
    assert single.decide(c(0.78)).route is Route.WORK_ORDER
    assert not single.needs_confirmation(c(0.74))


class SequenceClassifier:
    """Returns the queued classifications in order; raises for queued exceptions."""

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls = 0

    async def classify(self, redacted_text):
        result = self.results[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


def coordinator(classifier) -> IntakeCoordinator:
    async def handoff(request_id, reason):
        return None

    return IntakeCoordinator(
        pii_redactor=FakePiiRedactor(),
        safety_inspector=FakeSafetyInspector(),
        classifier=classifier,
        work_order_writer=FakeWorkOrderWriter(),
        decision_writer=FakeDecisionWriter(),
        emergency_handoff=handoff,
    )


@pytest.mark.asyncio
async def test_coordinator_confirms_borderline_answers():
    classifier = SequenceClassifier(c(0.74), c(0.78), c(0.74))

    result = await coordinator(classifier).process(IntakeCommand(text="lights flicker"))

    assert classifier.calls == 3
    assert result.status is IntakeStatus.WORK_ORDER_CREATED
    assert result.reason_code == "classified_by_agreement"
    assert result.confidence == 0.74  # the first answer is what gets stored


@pytest.mark.asyncio
async def test_coordinator_sends_disagreement_and_failures_to_a_person():
    disagree = SequenceClassifier(c(0.74), c(0.78, priority=4), c(0.74))
    failing = SequenceClassifier(c(0.74), TimeoutError(), c(0.74))

    first = await coordinator(disagree).process(IntakeCommand(text="lights flicker"))
    second = await coordinator(failing).process(IntakeCommand(text="lights flicker"))

    assert (first.status, first.reason_code) == (IntakeStatus.HUMAN_REVIEW, "classification_unconfirmed")
    assert (second.status, second.reason_code) == (IntakeStatus.HUMAN_REVIEW, "classification_unconfirmed")


@pytest.mark.asyncio
async def test_confident_answers_cost_one_call():
    classifier = SequenceClassifier(c(0.86))

    await coordinator(classifier).process(IntakeCommand(text="lights flicker"))

    assert classifier.calls == 1
