"""Reading the decision trail of one request: steps, model calls and human actions.

The steps and model-call metadata (model, prompt version, tokens, timing, outcome) are
readable with decisions:read. What was sent to the model and what it answered is redacted
request text, so it is included only for viewers who may read requests (intake:read_all),
and every such view is recorded in the security audit log.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AgentRunContentRecord,
    AgentRunRecord,
    DecisionStepRecord,
    IntakeAuditEvent,
    IntakeRequestRecord,
    PromptVersionRecord,
    SecurityAuditEvent,
)
from backend.app.domain.rbac import Permission
from backend.app.services.operations import NotFound, Viewer


@dataclass(frozen=True)
class ModelCallView:
    run: AgentRunRecord
    content: AgentRunContentRecord | None
    instructions: str | None


@dataclass(frozen=True)
class DecisionTrailView:
    request_id: UUID
    steps: list[DecisionStepRecord]
    model_calls: list[ModelCallView]
    human_actions: list[IntakeAuditEvent]
    content_included: bool


async def read_decision_trail(session: AsyncSession, request_id: UUID, viewer: Viewer) -> DecisionTrailView:
    exists = await session.scalar(
        select(IntakeRequestRecord.id).where(IntakeRequestRecord.request_id == request_id)
    )
    if exists is None:
        raise NotFound("Request not found")

    steps = list(
        await session.scalars(
            select(DecisionStepRecord)
            .where(DecisionStepRecord.request_id == request_id)
            .order_by(DecisionStepRecord.sequence)
        )
    )
    include_content = viewer.can(Permission.INTAKE_READ_ALL)
    rows = await session.execute(
        select(AgentRunRecord, AgentRunContentRecord, PromptVersionRecord.instructions)
        .outerjoin(AgentRunContentRecord, AgentRunContentRecord.agent_run_id == AgentRunRecord.id)
        .outerjoin(PromptVersionRecord, PromptVersionRecord.prompt_sha == AgentRunRecord.prompt_sha)
        .where(AgentRunRecord.request_id == request_id)
        .order_by(AgentRunRecord.started_at)
    )
    model_calls = [
        ModelCallView(run, content if include_content else None, instructions if include_content else None)
        for run, content, instructions in rows
    ]
    human_actions = list(
        await session.scalars(
            select(IntakeAuditEvent)
            .where(IntakeAuditEvent.request_id == request_id, IntakeAuditEvent.actor_id.is_not(None))
            .order_by(IntakeAuditEvent.created_at)
        )
    )
    if include_content and any(call.content for call in model_calls):
        session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=viewer.user_id,
                action="decision_trail.content_viewed",
                target_type="intake_request",
                target_id=str(request_id),
                details={"model_calls": len(model_calls)},
            )
        )
        await session.commit()
    return DecisionTrailView(request_id, steps, model_calls, human_actions, include_content)
