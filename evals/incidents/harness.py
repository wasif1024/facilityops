"""Scenario harness for the incident team (spec 10.1, FR-29).

Each scenario creates a synthetic escalated request, scripts what the incident team returns,
runs the real worker (outbox, event bus, idempotent consumers, dead letters), applies the
facility manager's decisions through the real decision service, and checks the outcome.

Scripted mode (here) needs no model and no Azure: it tests the machinery and the gates on every
pull request. When the Magentic team exists, the same scenarios run it with a scripted chat
client, and live mode runs it with real models.

Hard gates, checked on every scenario:
- no_side_effects_before_approval: no action step precedes the approval
- emergency_never_enters: an emergency can't be escalated and the team is never called
- events_ids_only: outbox payloads hold only ids and codes
- no_stuck_incidents: every incident ends where a person or the next step takes over
- trail_complete: every incident state change has a decision-trail step
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import IncidentPlanRecord, IncidentRecord, IntakeRequestRecord, OutboxEventRecord
from backend.app.domain.escalation import HIGH_PRIORITY_EQUIPMENT
from backend.app.domain.incident_plan import IncidentPlan
from backend.app.domain.intake import (
    Classification,
    IntakeCommand,
    IntakeResult,
    IntakeStatus,
    MaintenanceCategory,
)
from backend.app.domain.rbac import IdentityProvider
from backend.app.services.incident_worker import IncidentContext, TeamOutcome, build_dispatcher
from backend.app.services.incidents import decide_plan, escalate_manually
from backend.app.services.intake_repository import SqlIntakeRepository
from backend.app.services.operations import InvalidTransition
from backend.app.services.users import UserDirectory

SCENARIOS = Path(__file__).with_name("scenarios.json")
SETTLED = {"plan_ready", "needs_review", "approved", "rejected", "closed"}
_ERRORS = {"ConnectionError": ConnectionError, "TimeoutError": TimeoutError, "RuntimeError": RuntimeError}


class ScriptedIncidentTeam:
    """Returns the scripted outcomes in order and records what it was given."""

    def __init__(self, script: list[dict[str, Any]], plans: dict[str, dict[str, Any]]) -> None:
        self._script = list(script)
        self._plans = plans
        self.contexts: list[IncidentContext] = []

    async def investigate(self, context: IncidentContext) -> TeamOutcome:
        self.contexts.append(context)
        if not self._script:
            raise AssertionError("The team was called more often than the scenario scripts")
        step = self._script.pop(0)
        if "raise" in step:
            raise _ERRORS[step["raise"]]("scripted failure")
        if "reason" in step:
            return TeamOutcome(reason_code=step["reason"])
        return TeamOutcome(plan=IncidentPlan.model_validate(self._plans[step["plan"]]))


@dataclass
class ScenarioResult:
    id: str
    family: str
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    gates: dict[str, bool] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)


def load(path: Path = SCENARIOS) -> dict[str, Any]:
    return json.loads(path.read_text())


async def _manager(session_factory: async_sessionmaker[AsyncSession]) -> UUID:
    async with session_factory() as session:
        access = await UserDirectory(session).grant_role(
            provider=IdentityProvider.DEVELOPMENT, external_id="scenario-manager", role="facility_manager"
        )
    return access.user.id


async def _escalated_request(session_factory: async_sessionmaker[AsyncSession]) -> UUID:
    """A classified HVAC P4 request, stored exactly as the intake coordinator stores it."""
    command = IntakeCommand(text="Synthetic scenario request")
    classification = Classification(
        category=MaintenanceCategory.HVAC, priority=4, confidence=0.93, reason_code="scenario"
    )
    async with session_factory() as session:
        repository = SqlIntakeRepository(session)
        await repository.begin_request(command, submitted_by_user_id=None)
        work_order_id = await repository.create(
            request_id=command.request_id,
            redacted_text="Boiler in plant room B shows E04; [PII_0001] says radiators are cold.",
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
            redacted_text="Boiler in plant room B shows E04; [PII_0001] says radiators are cold.",
            encrypted_mapping=None,
            escalation_reason=HIGH_PRIORITY_EQUIPMENT,
        )
    return command.request_id


async def _emergency_request(session_factory: async_sessionmaker[AsyncSession]) -> UUID:
    request_id = uuid4()
    async with session_factory() as session:
        session.add(
            IntakeRequestRecord(
                id=uuid4(), request_id=request_id, source="text", status="emergency_handoff", reason_code="emergency_gas"
            )
        )
        await session.commit()
    return request_id


async def _drain(dispatcher, limit: int = 10) -> None:
    for _ in range(limit):
        if await dispatcher.run_once() == 0:
            return


async def run_scenario(
    session_factory: async_sessionmaker[AsyncSession], scenario: dict[str, Any], plans: dict[str, dict[str, Any]]
) -> ScenarioResult:
    team = ScriptedIncidentTeam(scenario["team_script"], plans)
    dispatcher = build_dispatcher(session_factory, team)
    manager = await _manager(session_factory)
    expect = scenario["expect"]
    result = ScenarioResult(scenario["id"], scenario["family"], passed=False)

    if scenario.get("emergency"):
        request_id = await _emergency_request(session_factory)
        refused = False
        async with session_factory() as session:
            try:
                await escalate_manually(session, request_id, manager)
            except InvalidTransition:
                refused = True
        await _drain(dispatcher)
        result.checks["escalation_refused"] = refused == expect["escalation_refused"]
        result.gates["emergency_never_enters"] = refused and not team.contexts
        result.checks["team_calls"] = len(team.contexts) == expect["team_calls"]
        result.passed = all(result.checks.values()) and all(result.gates.values())
        return result

    request_id = await _escalated_request(session_factory)
    await _drain(dispatcher)
    errors: list[str] = []
    for decision in scenario["decisions"]:
        async with session_factory() as session:
            incident = await session.scalar(select(IncidentRecord).where(IncidentRecord.request_id == request_id))
            plan = await session.scalar(
                select(IncidentPlanRecord)
                .where(IncidentPlanRecord.incident_id == incident.id)
                .order_by(IncidentPlanRecord.version.desc())
                .limit(1)
            )
            try:
                await decide_plan(
                    session,
                    incident_id=incident.id,
                    plan_id=plan.id,
                    decision=decision["decision"],
                    actor_id=manager,
                    feedback=decision.get("feedback"),
                    rejection_reason=decision.get("rejection_reason"),
                    confirm_high_cost=decision.get("confirm_high_cost", False),
                )
                if "expect_error" in decision:
                    errors.append(f"expected an error containing {decision['expect_error']!r}")
            except InvalidTransition as error:
                if decision.get("expect_error", "\0") not in str(error):
                    errors.append(f"unexpected refusal: {error}")
        await _drain(dispatcher)

    async with session_factory() as session:
        incident = await session.scalar(select(IncidentRecord).where(IncidentRecord.request_id == request_id))
        plans_ = list(
            await session.scalars(
                select(IncidentPlanRecord).where(IncidentPlanRecord.incident_id == incident.id).order_by(IncidentPlanRecord.version)
            )
        )
        steps = [row[0] for row in await session.execute(
            text("SELECT step FROM decision_steps WHERE request_id = :r ORDER BY sequence"), {"r": request_id}
        )]
        payloads = [e.payload for e in await session.scalars(
            select(OutboxEventRecord).where(OutboxEventRecord.aggregate_id == incident.id)
        )]

    checks = result.checks
    checks["decisions"] = not errors
    checks["final_status"] = incident.status == expect["final_status"]
    checks["plan_versions"] = len(plans_) == expect["plan_versions"]
    checks["team_calls"] = len(team.contexts) == expect["team_calls"]
    if "reason_code" in expect:
        checks["reason_code"] = incident.reason_code == expect["reason_code"]
    if "flags" in expect and plans_:
        checks["flags"] = plans_[-1].flags == expect["flags"]
    if "first_flags" in expect and plans_:
        checks["first_flags"] = plans_[0].flags == expect["first_flags"]
    if "feedback_seen" in expect:
        revised = [c for c in team.contexts if c.feedback]
        checks["feedback_seen"] = bool(revised) and revised[-1].feedback == expect["feedback_seen"] and revised[-1].previous_plan is not None
    # The team only ever sees redacted text.
    checks["team_sees_redacted_text"] = all("[PII_0001]" in c.redacted_description for c in team.contexts)

    approve_at = steps.index("incident.plan_decided") if "incident.plan_decided" in steps else len(steps)
    gates = result.gates
    gates["no_side_effects_before_approval"] = not any(s.startswith("incident.action") for s in steps[:approve_at])
    gates["events_ids_only"] = all(
        isinstance(v, (int, float)) or (isinstance(v, str) and len(v) <= 64) for p in payloads for v in p.values()
    )
    gates["no_stuck_incidents"] = incident.status in SETTLED
    # A plan is always preceded by an investigation step and its own plan step; a hand-off to a
    # person always has its step. (A team that raises rolls its transaction back, so a failed
    # attempt leaves only the dead-letter hand-off.)
    has_plan = incident.status in {"plan_ready", "approved", "rejected", "revising"}
    gates["trail_complete"] = (
        not has_plan or {"incident.investigation_started", "incident.plan_ready"} <= set(steps)
    ) and (incident.status != "needs_review" or "incident.needs_review" in steps)

    result.detail = {"status": incident.status, "reason_code": incident.reason_code, "versions": len(plans_), "errors": errors}
    result.passed = all(checks.values()) and all(gates.values())
    return result


async def run_all(session_factory: async_sessionmaker[AsyncSession], path: Path = SCENARIOS) -> list[ScenarioResult]:
    data = load(path)
    return [await run_scenario(session_factory, s, data["plans"]) for s in data["scenarios"]]
