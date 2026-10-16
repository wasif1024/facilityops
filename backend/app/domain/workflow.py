from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from backend.app.domain.emergency import detect_emergency
from backend.app.domain.intake import (
    Classification,
    IntakeCommand,
    IntakeResult,
    IntakeStatus,
    PiiRedaction,
)


class PiiRedactor(Protocol):
    async def redact(self, text: str, language: str = "en") -> PiiRedaction: ...


class ContentSafetyInspector(Protocol):
    async def is_safe(self, redacted_text: str) -> bool: ...


class MaintenanceClassifier(Protocol):
    async def classify(self, redacted_text: str) -> Classification: ...


class WorkOrderWriter(Protocol):
    async def create(
        self,
        *,
        request_id: UUID,
        redacted_text: str,
        classification: Classification,
        approval_required: bool,
        encrypted_mapping: bytes,
    ) -> UUID: ...


class IntakeDecisionWriter(Protocol):
    async def record_result(
        self,
        *,
        command: IntakeCommand,
        result: IntakeResult,
        redacted_text: str | None,
        encrypted_mapping: bytes | None,
    ) -> None: ...


class IntakeCoordinator:
    """Deterministic controller; model output can classify but never route or authorize."""

    def __init__(
        self,
        *,
        pii_redactor: PiiRedactor,
        safety_inspector: ContentSafetyInspector,
        classifier: MaintenanceClassifier,
        work_order_writer: WorkOrderWriter,
        decision_writer: IntakeDecisionWriter,
        emergency_handoff: Callable[[UUID, str], Awaitable[None]],
        minimum_confidence: float = 0.75,
        approval_priority_threshold: int = 4,
    ) -> None:
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")
        if not 1 <= approval_priority_threshold <= 5:
            raise ValueError("approval_priority_threshold must be between 1 and 5")

        self._pii_redactor = pii_redactor
        self._safety_inspector = safety_inspector
        self._classifier = classifier
        self._work_order_writer = work_order_writer
        self._decision_writer = decision_writer
        self._emergency_handoff = emergency_handoff
        self._minimum_confidence = minimum_confidence
        self._approval_priority_threshold = approval_priority_threshold

    async def process(self, command: IntakeCommand) -> IntakeResult:
        emergency_reason = self._emergency_reason(command.text)
        if emergency_reason is not None:
            await self._emergency_handoff(command.request_id, emergency_reason)
            return await self._finish(
                command,
                IntakeResult(
                    request_id=command.request_id,
                    status=IntakeStatus.EMERGENCY_HANDOFF,
                    reason_code=emergency_reason,
                ),
            )

        try:
            redaction = await self._pii_redactor.redact(command.text)
        except Exception:
            return await self._finish(
                command, self._human_review(command, "pii_redaction_unavailable")
            )

        redacted_text = redaction.redacted_text
        if not redacted_text.strip():
            return await self._finish(
                command,
                self._human_review(command, "pii_redaction_empty_result"),
            )

        try:
            if not await self._safety_inspector.is_safe(redacted_text):
                return await self._finish(
                    command,
                    self._human_review(command, "content_safety_review"),
                    redacted_text,
                    redaction.encrypted_mapping,
                )
        except Exception:
            return await self._finish(
                command,
                self._human_review(command, "content_safety_unavailable"),
                redacted_text,
                redaction.encrypted_mapping,
            )

        try:
            classification = await self._classifier.classify(redacted_text)
        except Exception:
            return await self._finish(
                command,
                self._human_review(command, "classification_unavailable"),
                redacted_text,
                redaction.encrypted_mapping,
            )

        if (
            classification.requires_human_review
            or classification.confidence < self._minimum_confidence
        ):
            return await self._finish(
                command,
                self._human_review(command, "classification_uncertain", classification),
                redacted_text,
                redaction.encrypted_mapping,
            )

        approval_required = classification.priority >= self._approval_priority_threshold
        try:
            work_order_id = await self._work_order_writer.create(
                request_id=command.request_id,
                redacted_text=redacted_text,
                classification=classification,
                approval_required=approval_required,
                encrypted_mapping=redaction.encrypted_mapping,
            )
        except Exception:
            return await self._finish(
                command,
                self._human_review(
                    command, "work_order_creation_failed", classification
                ),
                redacted_text,
                redaction.encrypted_mapping,
            )

        return await self._finish(
            command,
            IntakeResult(
                request_id=command.request_id,
                status=(
                    IntakeStatus.PENDING_APPROVAL
                    if approval_required
                    else IntakeStatus.WORK_ORDER_CREATED
                ),
                reason_code=(
                    "approval_required" if approval_required else "classified"
                ),
                category=classification.category,
                priority=classification.priority,
                confidence=classification.confidence,
                work_order_id=work_order_id,
            ),
            redacted_text,
            redaction.encrypted_mapping,
        )

    async def _finish(
        self,
        command: IntakeCommand,
        result: IntakeResult,
        redacted_text: str | None = None,
        encrypted_mapping: bytes | None = None,
    ) -> IntakeResult:
        await self._decision_writer.record_result(
            command=command,
            result=result,
            redacted_text=redacted_text,
            encrypted_mapping=encrypted_mapping,
        )
        return result

    @staticmethod
    def _emergency_reason(text: str) -> str | None:
        emergency_type = detect_emergency(text)
        return f"emergency_{emergency_type}" if emergency_type else None

    @staticmethod
    def _human_review(
        command: IntakeCommand,
        reason_code: str,
        classification: Classification | None = None,
    ) -> IntakeResult:
        return IntakeResult(
            request_id=command.request_id,
            status=IntakeStatus.HUMAN_REVIEW,
            reason_code=reason_code,
            category=classification.category if classification else None,
            priority=classification.priority if classification else None,
            confidence=classification.confidence if classification else None,
        )
