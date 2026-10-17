import asyncio
from uuid import uuid4

import pytest

from backend.app.domain.escalation import HIGH_PRIORITY_EQUIPMENT, escalation_reason
from backend.app.domain.events import Event, EventType
from backend.app.domain.intake import Classification, IntakeCommand, IntakeStatus, MaintenanceCategory
from backend.app.domain.workflow import IntakeCoordinator

from .test_intake_workflow import FakeClassifier, FakeDecisionWriter, FakePiiRedactor, FakeSafetyInspector, FakeWorkOrderWriter


def classification(category: MaintenanceCategory, priority: int) -> Classification:
    return Classification(category=category, priority=priority, confidence=0.95, reason_code="test")


@pytest.mark.parametrize(
    ("category", "priority", "expected"),
    [
        (MaintenanceCategory.HVAC, 4, HIGH_PRIORITY_EQUIPMENT),
        (MaintenanceCategory.ELECTRICAL, 5, HIGH_PRIORITY_EQUIPMENT),
        (MaintenanceCategory.PLUMBING, 4, HIGH_PRIORITY_EQUIPMENT),
        (MaintenanceCategory.HVAC, 3, None),
        (MaintenanceCategory.CLEANING, 5, None),
        (MaintenanceCategory.SECURITY, 4, None),
    ],
)
def test_escalation_criteria(category, priority, expected):
    assert escalation_reason(classification(category, priority)) == expected


def build(result: Classification, *, enabled: bool = True):
    writer = FakeDecisionWriter()
    coordinator = IntakeCoordinator(
        pii_redactor=FakePiiRedactor(),
        safety_inspector=FakeSafetyInspector(),
        classifier=FakeClassifier(result),
        work_order_writer=FakeWorkOrderWriter(),
        decision_writer=writer,
        emergency_handoff=lambda *_: asyncio.sleep(0),
        escalate_incidents=enabled,
    )
    return coordinator, writer


@pytest.mark.asyncio
async def test_qualifying_request_keeps_its_work_order_and_is_escalated():
    coordinator, writer = build(classification(MaintenanceCategory.HVAC, 4))

    result = await coordinator.process(IntakeCommand(text="The boiler is making a banging noise"))

    assert result.status is IntakeStatus.PENDING_APPROVAL and result.work_order_id is not None
    record = writer.records[-1]
    assert record["escalation_reason"] == HIGH_PRIORITY_EQUIPMENT
    assert "intake.escalation" in [s.step for s in record["decision_steps"]]


@pytest.mark.asyncio
async def test_routine_request_is_not_escalated():
    coordinator, writer = build(classification(MaintenanceCategory.PLUMBING, 2))

    await coordinator.process(IntakeCommand(text="The tap drips"))

    assert writer.records[-1]["escalation_reason"] is None


@pytest.mark.asyncio
async def test_escalation_can_be_switched_off():
    coordinator, writer = build(classification(MaintenanceCategory.HVAC, 5), enabled=False)

    await coordinator.process(IntakeCommand(text="No heating anywhere"))

    assert writer.records[-1]["escalation_reason"] is None


def test_events_refuse_free_text():
    with pytest.raises(ValueError, match="ids and codes only"):
        Event(type=EventType.INTAKE_ESCALATED, aggregate_id=uuid4(), payload={"note": "The tenant in unit 4B says the boiler has been banging loudly since Monday night"})
