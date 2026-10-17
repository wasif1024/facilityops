"""What the incident worker does with each event (spec 6.2, FR-27).

``intake.escalated`` starts an investigation by the incident team. Until the Magentic team is
built, ``UnavailableIncidentTeam`` fails closed: the incident goes to a facility manager with
``incident_team_unavailable``, exactly like any other part of the system that can't finish.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import IncidentRecord, OutboxEventRecord
from backend.app.domain.decision_trail import DecisionStep, StepOutcome, current_trace_id
from backend.app.domain.events import EventType
from backend.app.services.events import InProcessEventBus, OutboxDispatcher, Subscription
from backend.app.services.incidents import ESCALATED, INVESTIGATING, NEEDS_REVIEW, append_steps

INCIDENT_TEAM_CONSUMER = "incident-team"


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


class IncidentTeam(Protocol):
    async def investigate(self, session: AsyncSession, incident: IncidentRecord) -> None:
        """Investigate and leave the incident in plan_ready or needs_review, in this session."""
        ...


class UnavailableIncidentTeam:
    """Placeholder until the Magentic team exists: hand the incident to a person."""

    async def investigate(self, session: AsyncSession, incident: IncidentRecord) -> None:
        await _needs_review(session, incident, "incident_team_unavailable")


async def _needs_review(session: AsyncSession, incident: IncidentRecord, reason_code: str) -> None:
    incident.status = NEEDS_REVIEW
    incident.reason_code = reason_code
    await append_steps(
        session, incident.request_id, [_step("incident.needs_review", StepOutcome.FLAGGED, reason_code)]
    )


def subscriptions(team: IncidentTeam) -> list[Subscription]:
    async def on_escalated(session: AsyncSession, event: OutboxEventRecord) -> None:
        incident = await session.get(IncidentRecord, event.aggregate_id, with_for_update=True)
        if incident is None or incident.status != ESCALATED:
            return  # gone, or already moved on: nothing to do
        incident.status = INVESTIGATING
        await append_steps(
            session,
            incident.request_id,
            [_step("incident.investigation_started", StepOutcome.DECIDED, incident.escalation_reason)],
        )
        await team.investigate(session, incident)

    return [Subscription(INCIDENT_TEAM_CONSUMER, EventType.INTAKE_ESCALATED.value, on_escalated)]


async def on_dead_letter(session: AsyncSession, event: OutboxEventRecord) -> None:
    """An event that can't be delivered sends its incident to people (spec 6.2)."""
    incident = await session.get(IncidentRecord, event.aggregate_id)
    if incident is not None and incident.status not in (NEEDS_REVIEW, "closed"):
        await _needs_review(session, incident, "event_delivery_failed")


def build_dispatcher(
    session_factory: async_sessionmaker[AsyncSession], team: IncidentTeam | None = None
) -> OutboxDispatcher:
    bus = InProcessEventBus(session_factory, subscriptions(team or UnavailableIncidentTeam()))
    return OutboxDispatcher(session_factory, bus, on_dead_letter=on_dead_letter)
