from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    DecisionStepRecord,
    HumanReviewCase,
    IntakeAuditEvent,
    IntakeRequestRecord,
    PiiMappingRecord,
    WorkOrderRecord,
)
from backend.app.domain.decision_trail import DecisionStep
from backend.app.services.incidents import open_incident
from backend.app.domain.intake import Classification, IntakeCommand, IntakeResult, Translation


class DuplicateRequestInProgress(Exception):
    pass


class RequestIdOwnedByAnotherUser(Exception):
    """Replays only return a stored result to the user who submitted the request."""


class SqlIntakeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def begin_request(
        self, command: IntakeCommand, *, submitted_by_user_id: UUID | None = None
    ) -> IntakeResult | None:
        insert_statement = (
            insert(IntakeRequestRecord)
            .values(
                id=uuid4(),
                request_id=command.request_id,
                source=command.source.value,
                status="processing",
                submitted_by_user_id=submitted_by_user_id,
            )
            .on_conflict_do_nothing(index_elements=[IntakeRequestRecord.request_id])
            .returning(IntakeRequestRecord.request_id)
        )
        inserted_id = (
            await self._session.execute(insert_statement)
        ).scalar_one_or_none()
        await self._session.commit()
        if inserted_id is not None:
            return None

        existing = await self._session.scalar(
            select(IntakeRequestRecord).where(
                IntakeRequestRecord.request_id == command.request_id
            )
        )
        if existing is None:
            raise RuntimeError("Unable to resolve idempotent intake request")
        if existing.submitted_by_user_id != submitted_by_user_id:
            raise RequestIdOwnedByAnotherUser
        if existing.status == "processing":
            raise DuplicateRequestInProgress
        return IntakeResult(
            request_id=existing.request_id,
            status=existing.status,
            reason_code=existing.reason_code or "previously_processed",
            category=existing.category,
            priority=existing.priority,
            confidence=existing.confidence,
            work_order_id=existing.work_order_id,
        )

    async def create(
        self,
        *,
        request_id: UUID,
        redacted_text: str,
        classification: Classification,
        approval_required: bool,
        encrypted_mapping: bytes,
    ) -> UUID:
        existing_id = await self._session.scalar(
            select(WorkOrderRecord.id).where(WorkOrderRecord.request_id == request_id)
        )
        if existing_id is not None:
            return existing_id

        work_order_id = uuid4()
        record = WorkOrderRecord(
            id=work_order_id,
            request_id=request_id,
            category=classification.category.value,
            priority=classification.priority,
            confidence=classification.confidence,
            status="pending_approval" if approval_required else "open",
            approval_required=approval_required,
        )
        self._session.add(record)
        await self._session.flush()
        await self._store_mapping(request_id, encrypted_mapping)
        return work_order_id

    async def emergency_handoff(self, request_id: UUID, reason_code: str) -> None:
        self._session.add(
            HumanReviewCase(
                id=uuid4(),
                request_id=request_id,
                reason_code=reason_code,
                status="emergency_on_call_queue",
            )
        )
        await self._session.flush()

    async def record_result(
        self,
        *,
        command: IntakeCommand,
        result: IntakeResult,
        redacted_text: str | None,
        encrypted_mapping: bytes | None,
        translation: Translation | None = None,
        decision_steps: Sequence[DecisionStep] = (),
        escalation_reason: str | None = None,
    ) -> None:
        request = await self._session.scalar(
            select(IntakeRequestRecord).where(
                IntakeRequestRecord.request_id == command.request_id
            )
        )
        if request is None:
            raise RuntimeError("Intake request must be reserved before processing")

        request.redacted_description = redacted_text
        if translation is not None:
            request.translated_description = translation.text
            request.source_language = translation.source_language
        request.status = result.status.value
        request.reason_code = result.reason_code
        request.category = result.category.value if result.category else None
        request.priority = result.priority
        request.confidence = result.confidence
        request.work_order_id = result.work_order_id

        self._session.add(
            IntakeAuditEvent(
                id=uuid4(),
                request_id=command.request_id,
                event_type=result.status.value,
                reason_code=result.reason_code,
            )
        )

        for sequence, step in enumerate(decision_steps, start=1):
            self._session.add(
                DecisionStepRecord(
                    id=uuid4(),
                    request_id=command.request_id,
                    sequence=sequence,
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

        if escalation_reason is not None:
            # Same transaction as the outcome: the incident and its event exist together or not at all.
            open_incident(
                self._session,
                request_id=command.request_id,
                work_order_id=result.work_order_id,
                reason_code=escalation_reason,
            )

        if encrypted_mapping:
            await self._store_mapping(command.request_id, encrypted_mapping)

        if result.status.value in {"human_review", "emergency_handoff"}:
            existing_case = await self._session.scalar(
                select(HumanReviewCase.id).where(
                    HumanReviewCase.request_id == command.request_id
                )
            )
            if existing_case is None:
                self._session.add(
                    HumanReviewCase(
                        id=uuid4(),
                        request_id=command.request_id,
                        reason_code=result.reason_code,
                        redacted_description=redacted_text,
                        status=(
                            "emergency_on_call_queue"
                            if result.status.value == "emergency_handoff"
                            else "queued"
                        ),
                    )
                )

        await self._session.commit()

    async def _store_mapping(self, request_id: UUID, encrypted_mapping: bytes) -> None:
        statement = (
            insert(PiiMappingRecord)
            .values(request_id=request_id, encrypted_mapping=encrypted_mapping)
            .on_conflict_do_update(
                index_elements=[PiiMappingRecord.request_id],
                set_={"encrypted_mapping": encrypted_mapping},
            )
        )
        await self._session.execute(statement)
