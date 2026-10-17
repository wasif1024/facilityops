"""Phase 2 events foundation: escalation, transactional outbox, delivery, idempotency, dead letters."""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from backend.app.db.models import IntakeRequestRecord
from backend.app.domain.escalation import HIGH_PRIORITY_EQUIPMENT
from backend.app.domain.intake import Classification, IntakeCommand, IntakeResult, IntakeStatus, MaintenanceCategory
from backend.app.services.incident_worker import build_dispatcher
from backend.app.services.intake_repository import SqlIntakeRepository

from .conftest import call, scalar
from .test_operations import make_user


async def escalated_request(session_factory) -> UUID:
    """A classified HVAC P4 request, stored as the coordinator would store it."""
    command = IntakeCommand(text="The boiler bangs and the radiators are cold")
    async with session_factory() as session:
        repository = SqlIntakeRepository(session)
        await repository.begin_request(command, submitted_by_user_id=None)
        classification = Classification(
            category=MaintenanceCategory.HVAC, priority=4, confidence=0.93, reason_code="no_heating"
        )
        work_order_id = await repository.create(
            request_id=command.request_id,
            redacted_text="The boiler bangs and the radiators are cold",
            classification=classification,
            approval_required=True,
            encrypted_mapping=b"",
        )
        await repository.record_result(
            command=command,
            result=IntakeResult(
                request_id=command.request_id,
                status=IntakeStatus.PENDING_APPROVAL,
                reason_code="approval_required",
                work_order_id=work_order_id,
            ),
            redacted_text="The boiler bangs and the radiators are cold",
            encrypted_mapping=None,
            escalation_reason=HIGH_PRIORITY_EQUIPMENT,
        )
    return command.request_id


@pytest.mark.asyncio
async def test_escalation_writes_the_incident_and_its_event_together(client, session_factory):
    request_id = await escalated_request(session_factory)

    status = await scalar(session_factory, "SELECT status FROM incidents WHERE request_id = :r", r=request_id)
    event = await scalar(
        session_factory, "SELECT event_type FROM outbox_events WHERE payload->>'request_id' = :r", r=str(request_id)
    )
    assert (status, event) == ("escalated", "intake.escalated")
    # The work order still exists: escalation adds an investigation, it doesn't replace approval.
    assert await scalar(session_factory, "SELECT count(*) FROM work_orders WHERE request_id = :r", r=request_id) == 1


@pytest.mark.asyncio
async def test_worker_delivers_once_and_fails_closed_without_a_team(client, session_factory):
    request_id = await escalated_request(session_factory)
    dispatcher = build_dispatcher(session_factory)

    assert await dispatcher.run_once() == 1
    assert await dispatcher.run_once() == 0  # nothing left to deliver

    incident = (await _incident(session_factory, request_id))
    assert incident == ("needs_review", "incident_team_unavailable")
    steps = await _steps(session_factory, request_id)
    assert steps[-2:] == ["incident.investigation_started", "incident.needs_review"]

    # Redelivery (for example after a crash before the event was marked delivered) changes nothing.
    async with session_factory() as session:
        await session.execute(text("UPDATE outbox_events SET published_at = NULL"))
        await session.commit()
    assert await dispatcher.run_once() == 1
    assert await _steps(session_factory, request_id) == steps


@pytest.mark.asyncio
async def test_undeliverable_event_is_retried_then_dead_lettered_to_a_person(client, session_factory):
    request_id = await escalated_request(session_factory)

    class BrokenTeam:
        async def investigate(self, session, incident):
            raise ConnectionError("model unavailable")

    dispatcher = build_dispatcher(session_factory, team=BrokenTeam())
    for _ in range(3):
        await dispatcher.run_once()

    attempts, error, dead = await _event(session_factory, request_id)
    assert (attempts, error, dead) == (3, "ConnectionError", True)
    assert await _incident(session_factory, request_id) == ("needs_review", "event_delivery_failed")
    assert await dispatcher.run_once() == 0  # dead letters are not retried


@pytest.mark.asyncio
async def test_concurrent_workers_never_take_the_same_event(client, session_factory):
    ids = [await escalated_request(session_factory) for _ in range(6)]
    workers = [build_dispatcher(session_factory) for _ in range(3)]

    await asyncio.gather(*(w.run_once() for w in workers))
    await asyncio.gather(*(w.run_once() for w in workers))

    handled = await scalar(session_factory, "SELECT count(*) FROM processed_events")
    started = await scalar(
        session_factory, "SELECT count(*) FROM decision_steps WHERE step = 'incident.investigation_started'"
    )
    assert handled == started == len(ids)


@pytest.mark.asyncio
async def test_manager_escalates_manually_but_never_an_emergency(client, session_factory):
    await make_user(session_factory, "fm", "facility_manager")
    request_id = uuid4()
    emergency_id = uuid4()
    async with session_factory() as session:
        for rid, status in ((request_id, "work_order_created"), (emergency_id, "emergency_handoff")):
            session.add(IntakeRequestRecord(id=uuid4(), request_id=rid, source="text", status=status, reason_code="x"))
        await session.commit()

    escalated = call(client, "IncidentService/EscalateRequest", {"requestId": str(request_id)}, user="fm").json()
    assert escalated["incident"]["status"] == "INCIDENT_STATUS_ESCALATED"
    assert escalated["incident"]["escalationReason"] == "manual_escalation"

    again = call(client, "IncidentService/EscalateRequest", {"requestId": str(request_id)}, user="fm").json()
    assert "already has an incident" in again["message"]
    emergency = call(client, "IncidentService/EscalateRequest", {"requestId": str(emergency_id)}, user="fm").json()
    assert "on-call" in emergency["message"]
    tenant = call(client, "IncidentService/EscalateRequest", {"requestId": str(request_id)}, user="alice").json()
    assert tenant["code"] == "permission_denied"

    listed = call(client, "IncidentService/ListIncidents", {}, user="fm").json()
    assert listed["total"] == 1


async def _incident(session_factory, request_id):
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT status, reason_code FROM incidents WHERE request_id = :r"), {"r": request_id}
            )
        ).first()
    return tuple(row)


async def _event(session_factory, request_id):
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT attempts, last_error_type, dead_lettered_at IS NOT NULL FROM outbox_events "
                    "WHERE payload->>'request_id' = :r"
                ),
                {"r": str(request_id)},
            )
        ).first()
    return tuple(row)


async def _steps(session_factory, request_id) -> list[str]:
    async with session_factory() as session:
        rows = await session.execute(
            text("SELECT step FROM decision_steps WHERE request_id = :r ORDER BY sequence"), {"r": request_id}
        )
    return [r[0] for r in rows]
