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
    IncidentPlanRecord,
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
PLAN_READY = "plan_ready"
REVISING = "revising"
NEEDS_REVIEW = "needs_review"
APPROVED = "approved"
REJECTED = "rejected"

# Fixed reasons a manager can give for rejecting a plan: codes, never free text, in the audit.
REJECTION_REASONS = frozenset({"not_needed", "unsafe", "too_costly", "wrong_diagnosis", "duplicate"})
_DECISION_EVENTS = {
    "approve": "incident_plan_approved",
    "revise": "incident_plan_revision_requested",
    "reject": "incident_plan_rejected",
}


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
    session: AsyncSession, *, status: str | None, limit: int, offset: int, request_id: UUID | None = None
) -> tuple[list[IncidentRecord], int]:
    query = select(IncidentRecord)
    if status:
        query = query.where(IncidentRecord.status == status)
    if request_id:
        query = query.where(IncidentRecord.request_id == request_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(query.order_by(IncidentRecord.created_at.desc()).limit(limit).offset(offset))
    return list(rows), total or 0


async def get_incident(session: AsyncSession, incident_id: UUID) -> IncidentRecord:
    incident = await session.get(IncidentRecord, incident_id)
    if incident is None:
        raise NotFound("Incident not found")
    return incident


async def latest_plans(session: AsyncSession, incident_id: UUID) -> list[IncidentPlanRecord]:
    """All plan versions, newest first."""
    return list(
        await session.scalars(
            select(IncidentPlanRecord)
            .where(IncidentPlanRecord.incident_id == incident_id)
            .order_by(IncidentPlanRecord.version.desc())
        )
    )


async def decide_plan(
    session: AsyncSession,
    *,
    incident_id: UUID,
    plan_id: UUID,
    decision: str,
    actor_id: UUID,
    feedback: str | None = None,
    rejection_reason: str | None = None,
    confirm_high_cost: bool = False,
) -> IncidentRecord:
    """A facility manager approves, revises or rejects the latest plan (FR-24).

    The approval gate is code, not a model: a plan with a safety block can't be approved, and a
    high-cost plan needs ``confirm_high_cost``. Approval never acts by itself; the application
    carries out the approved actions afterwards (FR-25).
    """
    incident = await session.get(IncidentRecord, incident_id, with_for_update=True)
    if incident is None:
        raise NotFound("Incident not found")
    plan = await session.get(IncidentPlanRecord, plan_id, with_for_update=True)
    if plan is None or plan.incident_id != incident_id:
        raise NotFound("Plan not found")
    if incident.status != PLAN_READY or plan.status != "proposed":
        raise InvalidTransition("Only the latest proposed plan of an incident awaiting a decision can be decided")

    if decision == "approve":
        if "safety_block" in plan.flags:
            raise InvalidTransition("This plan has a safety block: revise or reject it")
        if "high_cost" in plan.flags and not confirm_high_cost:
            raise InvalidTransition("This plan is above the cost threshold: confirm the cost to approve it")
        plan.status, incident.status = "approved", APPROVED
    elif decision == "revise":
        if not feedback or not feedback.strip():
            raise InvalidTransition("Say what should change in the plan")
        plan.status, plan.feedback, incident.status = "revision_requested", feedback.strip()[:2_000], REVISING
    elif decision == "reject":
        if rejection_reason not in REJECTION_REASONS:
            raise InvalidTransition(f"Rejection reason must be one of {sorted(REJECTION_REASONS)}")
        plan.status, plan.decision_reason, incident.status = "rejected", rejection_reason, REJECTED
    else:
        raise InvalidTransition("Decision must be approve, revise or reject")

    plan.decided_by_user_id, plan.decided_at = actor_id, datetime.now(UTC)
    incident.reason_code = rejection_reason if decision == "reject" else None
    write_event(
        session,
        Event(
            type=EventType.INCIDENT_PLAN_DECIDED,
            aggregate_id=incident.id,
            payload={"request_id": str(incident.request_id), "plan_id": str(plan.id), "decision": decision},
        ),
    )
    session.add(
        IntakeAuditEvent(
            id=uuid4(),
            request_id=incident.request_id,
            event_type=_DECISION_EVENTS[decision],
            reason_code=rejection_reason or ("high_cost_confirmed" if confirm_high_cost and decision == "approve" else decision),
            actor_id=str(actor_id),
        )
    )
    await append_steps(
        session,
        incident.request_id,
        [
            DecisionStep(
                step="incident.plan_decided",
                outcome=StepOutcome.DECIDED,
                started_at=datetime.now(UTC),
                reason_code=rejection_reason or decision,
                service="facility manager",
                details={"version": plan.version, "decision": decision, "flags": list(plan.flags)},
                trace_id=current_trace_id(),
            )
        ],
    )
    await session.commit()
    return incident
