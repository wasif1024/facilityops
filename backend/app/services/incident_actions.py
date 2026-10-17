"""Carry out an approved incident plan (FR-25).

The incident team only proposes. When a facility manager approves a plan, the
``incident.plan_decided`` event reaches the action executor, which does each action with the
application's own rules, not the model's word:

- ``dispatch_technician``: approves the incident's work order (the plan approval is that
  approval) and assigns it to the technician the plan names, if that profile belongs to an
  active technician account.
- ``order_part``: raises a purchase request for a part that exists, priced from the parts table.
- ``schedule_visit``: raises a vendor visit request.
- ``apply_lockout``: records that lockout/tagout is required before work starts.
- ``notify_tenant``: queues a notice for the communicator step (FR-61).

Each result is a row in ``incident_actions`` and an ``incident.action_*`` decision-trail step.
An action that can't be done is recorded as ``failed`` with a reason code, so a person picks it
up; nothing is retried with different targets. Everything happens in the consumer's
transaction, so the work and the processed-event marker commit together.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    IncidentActionRecord,
    IncidentPlanRecord,
    IncidentRecord,
    IntakeAuditEvent,
    IntakeRequestRecord,
    OutboxEventRecord,
    PartRecord,
    TechnicianProfileRecord,
    UserRecord,
    UserRoleRecord,
    WorkOrderRecord,
)
from backend.app.domain.decision_trail import DecisionStep, StepOutcome, current_trace_id
from backend.app.domain.events import EventType
from backend.app.domain.incident_plan import ActionType, IncidentPlan, ProposedAction
from backend.app.domain.intake import IntakeStatus
from backend.app.domain.operations import WorkOrderStatus
from backend.app.domain.rbac import SystemRole, UserStatus
from backend.app.services.events import Subscription
from backend.app.services.incidents import APPROVED, append_steps

ACTION_EXECUTOR_CONSUMER = "action-executor"
# Why an approval comes from the incident plan, in the work order's audit events.
PLAN_APPROVAL = "incident_plan_approved"

DONE, REQUESTED, QUEUED, RECORDED, FAILED = "done", "requested", "queued", "recorded", "failed"
_ASSIGNABLE = {WorkOrderStatus.OPEN.value, WorkOrderStatus.PENDING_APPROVAL.value}


class _Result:
    def __init__(self, status: str, reason_code: str, **details: Any) -> None:
        self.status, self.reason_code, self.details = status, reason_code, details


async def execute_plan(session: AsyncSession, incident: IncidentRecord, plan_record: IncidentPlanRecord) -> None:
    approver = plan_record.decided_by_user_id
    plan = IncidentPlan.from_stored(plan_record.plan)
    work_order = (
        await session.get(WorkOrderRecord, incident.work_order_id, with_for_update=True)
        if incident.work_order_id
        else None
    )
    steps: list[DecisionStep] = []

    if "safety_block" in plan_record.flags:
        # decide_plan never approves one; refuse again here in case anything else ever does.
        results = [_Result(FAILED, "safety_block") for _ in plan.actions]
    else:
        if work_order is not None and work_order.status == WorkOrderStatus.PENDING_APPROVAL.value:
            await _approve_work_order(session, work_order, approver)
            steps.append(_step("incident.work_order_approved", StepOutcome.DECIDED, PLAN_APPROVAL))
        results = [await _execute(session, action, work_order, approver) for action in plan.actions]

    for position, (action, result) in enumerate(zip(plan.actions, results, strict=True)):
        session.add(
            IncidentActionRecord(
                id=uuid4(),
                incident_id=incident.id,
                plan_id=plan_record.id,
                position=position,
                action_type=action.type.value,
                status=result.status,
                reason_code=result.reason_code,
                details=result.details,
                approved_by_user_id=approver,
            )
        )
        steps.append(
            _step(
                f"incident.action_{action.type.value}",
                StepOutcome.FAILED if result.status == FAILED else StepOutcome.PASSED,
                result.reason_code,
                position=position,
                status=result.status,
                **result.details,
            )
        )

    failed = sum(r.status == FAILED for r in results)
    incident.reason_code = "actions_need_attention" if failed else "actions_carried_out"
    session.add(
        IntakeAuditEvent(
            id=uuid4(),
            request_id=incident.request_id,
            event_type="incident_actions_executed",
            reason_code=incident.reason_code,
            actor_id=str(approver) if approver else None,
        )
    )
    await append_steps(session, incident.request_id, steps)


async def _execute(
    session: AsyncSession, action: ProposedAction, work_order: WorkOrderRecord | None, approver: UUID | None
) -> _Result:
    match action.type:
        case ActionType.DISPATCH_TECHNICIAN:
            return await _dispatch(session, action, work_order, approver)
        case ActionType.ORDER_PART:
            return await _order_part(session, action)
        case ActionType.SCHEDULE_VISIT:
            return _Result(REQUESTED, "vendor_visit_requested", estimated_cost=action.estimated_cost)
        case ActionType.APPLY_LOCKOUT:
            return _Result(
                RECORDED,
                "lockout_required",
                work_order_id=str(work_order.id) if work_order else None,
            )
        case ActionType.NOTIFY_TENANT:
            return _Result(QUEUED, "notice_pending")
    return _Result(FAILED, "unsupported_action")


async def _dispatch(
    session: AsyncSession, action: ProposedAction, work_order: WorkOrderRecord | None, approver: UUID | None
) -> _Result:
    technician_id = action.technician_id
    if technician_id is None:
        return _Result(FAILED, "technician_not_named")
    if work_order is None:
        return _Result(FAILED, "no_work_order", technician_id=technician_id)
    if work_order.status not in _ASSIGNABLE:
        return _Result(FAILED, "work_order_closed", technician_id=technician_id)
    profile = await session.get(TechnicianProfileRecord, technician_id)
    if profile is None:
        return _Result(FAILED, "technician_unknown", technician_id=technician_id)
    if profile.user_id is None:
        return _Result(FAILED, "technician_not_linked", technician_id=technician_id)
    active = await session.scalar(
        select(func.count())
        .select_from(UserRoleRecord)
        .join(UserRecord, UserRecord.id == UserRoleRecord.user_id)
        .where(
            UserRoleRecord.user_id == profile.user_id,
            UserRoleRecord.role_id == SystemRole.TECHNICIAN.value,
            UserRecord.status == UserStatus.ACTIVE.value,
        )
    )
    if not active:
        return _Result(FAILED, "technician_inactive", technician_id=technician_id)
    work_order.assigned_technician_id = profile.user_id
    _audit(session, work_order.request_id, "work_order_assigned", PLAN_APPROVAL, approver)
    return _Result(DONE, "assigned", technician_id=technician_id, work_order_id=str(work_order.id))


async def _order_part(session: AsyncSession, action: ProposedAction) -> _Result:
    if action.part_sku is None:
        return _Result(FAILED, "part_not_named")
    part = await session.get(PartRecord, action.part_sku)
    if part is None:
        return _Result(FAILED, "part_unknown", part_sku=action.part_sku)
    quantity = action.quantity or 1
    # The price comes from the parts table, not from the model's estimate.
    return _Result(
        REQUESTED,
        "purchase_requested",
        part_sku=part.sku,
        quantity=quantity,
        unit_cost=part.unit_cost,
        cost=round(part.unit_cost * quantity, 2),
        estimated_cost=action.estimated_cost,
        in_stock=part.stock >= quantity,
        lead_time_days=0 if part.stock >= quantity else part.lead_time_days,
        vendor_id=part.vendor_id,
    )


async def _approve_work_order(session: AsyncSession, work_order: WorkOrderRecord, approver: UUID | None) -> None:
    work_order.status = WorkOrderStatus.OPEN.value
    work_order.decided_by_user_id = approver
    work_order.decided_at = datetime.now(UTC)
    request = await session.scalar(
        select(IntakeRequestRecord).where(IntakeRequestRecord.request_id == work_order.request_id)
    )
    if request is not None:
        request.status = IntakeStatus.WORK_ORDER_CREATED.value
    _audit(session, work_order.request_id, "work_order_approved", PLAN_APPROVAL, approver)


def _audit(session: AsyncSession, request_id: UUID, event_type: str, reason_code: str, actor: UUID | None) -> None:
    session.add(
        IntakeAuditEvent(
            id=uuid4(),
            request_id=request_id,
            event_type=event_type,
            reason_code=reason_code,
            actor_id=str(actor) if actor else None,
        )
    )


def _step(name: str, outcome: StepOutcome, reason_code: str | None = None, **details: Any) -> DecisionStep:
    return DecisionStep(
        step=name,
        outcome=outcome,
        started_at=datetime.now(UTC),
        reason_code=reason_code,
        service="action executor",
        details={k: v for k, v in details.items() if v is not None},
        trace_id=current_trace_id(),
    )


async def list_actions(session: AsyncSession, incident_id: UUID) -> list[IncidentActionRecord]:
    return list(
        await session.scalars(
            select(IncidentActionRecord)
            .where(IncidentActionRecord.incident_id == incident_id)
            .order_by(IncidentActionRecord.created_at, IncidentActionRecord.position)
        )
    )


def subscriptions() -> list[Subscription]:
    async def on_plan_decided(session: AsyncSession, event: OutboxEventRecord) -> None:
        if event.payload.get("decision") != "approve":
            return
        incident = await session.get(IncidentRecord, event.aggregate_id, with_for_update=True)
        plan = await session.get(IncidentPlanRecord, UUID(event.payload["plan_id"]))
        if incident is None or plan is None or incident.status != APPROVED or plan.status != "approved":
            return
        already = await session.scalar(
            select(func.count()).select_from(IncidentActionRecord).where(IncidentActionRecord.plan_id == plan.id)
        )
        if already:
            return
        await execute_plan(session, incident, plan)

    return [Subscription(ACTION_EXECUTOR_CONSUMER, EventType.INCIDENT_PLAN_DECIDED.value, on_plan_decided)]
