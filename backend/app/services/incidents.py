"""Incidents: opening one (automatically or by a manager) and reading them.

Opening an incident writes, in the caller's transaction: the incident, its ``intake.escalated``
event in the outbox (spec 6.2), an intake audit event and a decision-trail step. Nothing is
committed here; the caller's single commit makes the state change and its event atomic.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    DecisionStepRecord,
    IncidentRecord,
    IntakeAuditEvent,
    IntakeRequestRecord,
    OutboxEventRecord,
)
from backend.app.domain.decision_trail import DecisionStep, StepOutcome, current_trace_id
from backend.app.domain.escalation import MANUAL_ESCALATION
from backend.app.domain.events import Event, EventType
from backend.app.services.operations import InvalidTransition, NotFound

ESCALATED = "escalated"
INVESTIGATING = "investigating"
NEEDS_REVIEW = "needs_review"


def write_event(session: AsyncSession, event: Event) -> None:
    """Add an event to the outbox, in the caller's transaction."""
    session.add(
        OutboxEventRecord(
            id=event.id,
            event_type=event.type.value,
            aggregate_id=event.aggregate_id,
            payload=event.payload,
            trace_id=current_trace_id(),
        )
    )


async def append_steps(session: AsyncSession, request_id: UUID, steps: Sequence[DecisionStep]) -> None:
    """Continue a request's decision trail (for steps after intake: escalation, the team...)."""
    last = await session.scalar(
        select(func.max(DecisionStepRecord.sequence)).where(DecisionStepRecord.request_id == request_id)
    )
    for offset, step in enumerate(steps, start=1):
        session.add(
            DecisionStepRecord(
                id=uuid4(),
                request_id=request_id,
                sequence=(last or 0) + offset,
                step=step.step,
                outcome=step.outcome.value,
                reason_code=step.reason_code,
                service=step.service,
                version=step.version,
                duration_ms=step.duration_ms,
                details=step.details,
                trace_id=step.trace_id,
                started_at=step.started_at,
            )
        )


def open_incident(
    session: AsyncSession,
    *,
    request_id: UUID,
    work_order_id: UUID | None,
    reason_code: str,
    actor_id: UUID | None = None,
) -> IncidentRecord:
    incident = IncidentRecord(
        id=uuid4(),
        request_id=request_id,
        work_order_id=work_order_id,
        status=ESCALATED,
        escalation_reason=reason_code,
        escalated_by_user_id=actor_id,
    )
    session.add(incident)
    write_event(
        session,
        Event(
            type=EventType.INTAKE_ESCALATED,
            aggregate_id=incident.id,
            payload={"request_id": str(request_id), "reason_code": reason_code},
        ),
    )
    session.add(
        IntakeAuditEvent(
            id=uuid4(),
            request_id=request_id,
            event_type="incident_escalated",
            reason_code=reason_code,
            actor_id=str(actor_id) if actor_id else None,
        )
    )
    return incident


async def escalate_manually(session: AsyncSession, request_id: UUID, actor_id: UUID) -> IncidentRecord:
    """A facility manager sends a request to the incident team (spec 4.3)."""
    request = await session.scalar(select(IntakeRequestRecord).where(IntakeRequestRecord.request_id == request_id))
    if request is None:
        raise NotFound("Request not found")
    if request.status == "emergency_handoff":
        # Emergencies never enter the team, before or after the on-call handoff.
        raise InvalidTransition("Emergencies are handled by on-call and can't be escalated to the incident team")
    if await session.scalar(select(IncidentRecord.id).where(IncidentRecord.request_id == request_id)):
        raise InvalidTransition("This request already has an incident")
    incident = open_incident(
        session,
        request_id=request_id,
        work_order_id=request.work_order_id,
        reason_code=MANUAL_ESCALATION,
        actor_id=actor_id,
    )
    await append_steps(
        session,
        request_id,
        [
            DecisionStep(
                step="incident.escalated",
                outcome=StepOutcome.DECIDED,
                started_at=datetime.now(UTC),
                reason_code=MANUAL_ESCALATION,
                service="facility manager",
                trace_id=current_trace_id(),
            )
        ],
    )
    await session.commit()
    return incident


async def list_incidents(
    session: AsyncSession, *, status: str | None, limit: int, offset: int
) -> tuple[list[IncidentRecord], int]:
    query = select(IncidentRecord)
    if status:
        query = query.where(IncidentRecord.status == status)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(query.order_by(IncidentRecord.created_at.desc()).limit(limit).offset(offset))
    return list(rows), total or 0


async def get_incident(session: AsyncSession, incident_id: UUID) -> IncidentRecord:
    incident = await session.get(IncidentRecord, incident_id)
    if incident is None:
        raise NotFound("Incident not found")
    return incident
