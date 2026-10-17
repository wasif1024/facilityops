"""What the incident worker does with each event (spec 6.2, FR-27).

- ``intake.escalated``: the incident team investigates and proposes a plan.
- ``incident.plan_decided`` with a revision: the team investigates again, with the manager's
  feedback and its previous plan.

The team only *returns* a plan (or a reason it couldn't make one); this worker stores it, moves
the incident on, writes the next event and the decision-trail steps. The team never writes to
the database and never acts, so it can be tested with scripted plans (evals/incidents) and
swapped for the Magentic team without touching any of this.

Until the Magentic team exists, ``UnavailableIncidentTeam`` fails closed: the incident goes to
a facility manager with ``incident_team_unavailable``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import IncidentPlanRecord, IncidentRecord, IntakeRequestRecord, OutboxEventRecord
from backend.app.domain.decision_trail import DecisionStep, StepOutcome, current_trace_id
from backend.app.domain.events import Event, EventType
from backend.app.domain.incident_plan import IncidentPlan
from backend.app.services.events import InProcessEventBus, OutboxDispatcher, Subscription
from backend.app.services.incidents import (
    ESCALATED,
    INVESTIGATING,
    NEEDS_REVIEW,
    PLAN_READY,
    REVISING,
    append_steps,
    write_event,
)

INCIDENT_TEAM_CONSUMER = "incident-team"


@dataclass(frozen=True)
class IncidentContext:
    """Everything the team may use. Redacted text only; the PII mapping is never exposed."""

    incident_id: UUID
    request_id: UUID
    escalation_reason: str
    redacted_description: str
    translated_description: str | None
    category: str | None
    priority: int | None
    # Set when a manager asked for a revision.
    feedback: str | None = None
    previous_plan: IncidentPlan | None = None


@dataclass(frozen=True)
class TeamOutcome:
    plan: IncidentPlan | None = None
    # Why there is no plan, for example "budget_exhausted" or "incident_team_unavailable".
    reason_code: str | None = None
    # What the team did on the way (tool calls, specialist turns), for the decision trail.
    steps: list[DecisionStep] = field(default_factory=list)


class IncidentTeam(Protocol):
    async def investigate(self, context: IncidentContext) -> TeamOutcome: ...


class UnavailableIncidentTeam:
    """Placeholder until the Magentic team exists: hand the incident to a person."""

    async def investigate(self, context: IncidentContext) -> TeamOutcome:
        return TeamOutcome(reason_code="incident_team_unavailable")


def _step(name: str, outcome: StepOutcome, reason_code: str | None = None, **details) -> DecisionStep:
    return DecisionStep(
        step=name,
        outcome=outcome,
        started_at=datetime.now(UTC),
        reason_code=reason_code,
        service="incident worker",
        details=details,
        trace_id=current_trace_id(),
    )


async def _needs_review(session: AsyncSession, incident: IncidentRecord, reason_code: str) -> None:
    incident.status = NEEDS_REVIEW
    incident.reason_code = reason_code
    await append_steps(
        session, incident.request_id, [_step("incident.needs_review", StepOutcome.FLAGGED, reason_code)]
    )


async def _context(session: AsyncSession, incident: IncidentRecord) -> IncidentContext:
    request = await session.scalar(
        select(IntakeRequestRecord).where(IntakeRequestRecord.request_id == incident.request_id)
    )
    latest = await session.scalar(
        select(IncidentPlanRecord)
        .where(IncidentPlanRecord.incident_id == incident.id)
        .order_by(IncidentPlanRecord.version.desc())
        .limit(1)
    )
    revising = latest is not None and latest.status == "revision_requested"
    return IncidentContext(
        incident_id=incident.id,
        request_id=incident.request_id,
        escalation_reason=incident.escalation_reason,
        redacted_description=(request.redacted_description if request else None) or "",
        translated_description=request.translated_description if request else None,
        category=request.category if request else None,
        priority=request.priority if request else None,
        feedback=latest.feedback if revising else None,
        previous_plan=IncidentPlan.from_stored(latest.plan) if revising else None,
    )


async def _investigate(session: AsyncSession, incident: IncidentRecord, team: IncidentTeam) -> None:
    incident.status = INVESTIGATING
    context = await _context(session, incident)
    await append_steps(
        session,
        incident.request_id,
        [
            _step(
                "incident.investigation_started",
                StepOutcome.DECIDED,
                incident.escalation_reason,
                revision=context.feedback is not None,
            )
        ],
    )
    outcome = await team.investigate(context)
    await append_steps(session, incident.request_id, outcome.steps)
    if outcome.plan is None:
        await _needs_review(session, incident, outcome.reason_code or "incident_team_no_plan")
        return

    plan = outcome.plan
    version = await _latest_version(session, incident.id) + 1
    record = IncidentPlanRecord(
        incident_id=incident.id,
        version=version,
        plan=plan.model_dump(mode="json"),
        flags=plan.flags(),
        status="proposed",
    )
    session.add(record)
    await session.flush()
    incident.status = PLAN_READY
    incident.reason_code = None
    write_event(
        session,
        Event(
            type=EventType.INCIDENT_PLAN_READY,
            aggregate_id=incident.id,
            payload={"request_id": str(incident.request_id), "plan_id": str(record.id), "version": version},
        ),
    )
    await append_steps(
        session,
        incident.request_id,
        [
            _step(
                "incident.plan_ready",
                StepOutcome.DECIDED,
                version=version,
                claims=len(plan.claims),
                unverified_claims=plan.unverified_claims,
                actions=[a.type.value for a in plan.actions],
                estimated_total_cost=plan.estimated_total_cost,
                flags=plan.flags(),
            )
        ],
    )


async def _latest_version(session: AsyncSession, incident_id: UUID) -> int:
    return (
        await session.scalar(
            select(func.max(IncidentPlanRecord.version)).where(IncidentPlanRecord.incident_id == incident_id)
        )
        or 0
    )


def subscriptions(team: IncidentTeam) -> list[Subscription]:
    async def on_escalated(session: AsyncSession, event: OutboxEventRecord) -> None:
        incident = await session.get(IncidentRecord, event.aggregate_id, with_for_update=True)
        if incident is None or incident.status != ESCALATED:
            return  # gone, or already moved on: nothing to do
        await _investigate(session, incident, team)

    async def on_plan_decided(session: AsyncSession, event: OutboxEventRecord) -> None:
        if event.payload.get("decision") != "revise":
            return
        incident = await session.get(IncidentRecord, event.aggregate_id, with_for_update=True)
        if incident is None or incident.status != REVISING:
            return
        await _investigate(session, incident, team)

    return [
        Subscription(INCIDENT_TEAM_CONSUMER, EventType.INTAKE_ESCALATED.value, on_escalated),
        Subscription(INCIDENT_TEAM_CONSUMER, EventType.INCIDENT_PLAN_DECIDED.value, on_plan_decided),
    ]


async def on_dead_letter(session: AsyncSession, event: OutboxEventRecord) -> None:
    """An event that can't be delivered sends its incident to people (spec 6.2)."""
    incident = await session.get(IncidentRecord, event.aggregate_id)
    if incident is not None and incident.status in (ESCALATED, INVESTIGATING, REVISING):
        await _needs_review(session, incident, "event_delivery_failed")


def build_dispatcher(
    session_factory: async_sessionmaker[AsyncSession], team: IncidentTeam | None = None
) -> OutboxDispatcher:
    bus = InProcessEventBus(session_factory, subscriptions(team or UnavailableIncidentTeam()))
    return OutboxDispatcher(session_factory, bus, on_dead_letter=on_dead_letter)
