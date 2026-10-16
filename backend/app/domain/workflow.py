from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar
from uuid import UUID

from opentelemetry import trace

from backend.app.domain.emergency import detect_emergency
from backend.app.domain.intake import (
    Classification,
    IntakeCommand,
    IntakeResult,
    IntakeStatus,
    PiiRedaction,
    RequestSource,
)


# No-op unless telemetry is configured. Spans carry ids and reason codes, never text.
_tracer = trace.get_tracer("facilityops.intake")
T = TypeVar("T")


async def _step(name: str, awaitable: Awaitable[T]) -> T:
    """Trace one pipeline step. Exception messages are not recorded, only the type,
    because they can contain request text."""
    with _tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            return await awaitable
        except Exception as error:
            span.set_status(trace.Status(trace.StatusCode.ERROR, type(error).__name__))
            raise


class PiiRedactor(Protocol):
    async def redact(self, text: str, language: str = "en") -> PiiRedaction: ...


class MediaTranscriber(Protocol):
    async def transcribe(
        self,
        *,
        source: RequestSource,
        media: bytes,
        media_type: str,
        language: str | None,
    ) -> str:
        """Text found in a photo (caption and OCR) or spoken in a voice note."""
        ...


class ContentSafetyInspector(Protocol):
    async def assess(self, redacted_text: str) -> str | None:
        """None when safe; otherwise a reason code such as "prompt_attack_detected"."""
        ...


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
        media_transcriber: MediaTranscriber | None = None,
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
        self._media_transcriber = media_transcriber
        self._minimum_confidence = minimum_confidence
        self._approval_priority_threshold = approval_priority_threshold

    async def process(self, command: IntakeCommand) -> IntakeResult:
        with _tracer.start_as_current_span("intake.process") as span:
            span.set_attribute("facilityops.request_id", str(command.request_id))
            span.set_attribute("facilityops.source", command.source.value)
            result = await self._run(command)
            span.set_attribute("facilityops.status", result.status.value)
            span.set_attribute("facilityops.reason_code", result.reason_code)
            return result

    async def _run(self, command: IntakeCommand) -> IntakeResult:
        # Screen what the tenant typed first: no network call can delay an emergency.
        if (emergency_reason := self._emergency_reason(command.text)) is not None:
            return await self._hand_off(command, emergency_reason)

        text = command.text
        if command.media is not None:
            if self._media_transcriber is None:
                return await self._finish(
                    command, self._human_review(command, "media_transcription_unavailable")
                )
            try:
                transcript = await _step(
                    "intake.media_transcription",
                    self._media_transcriber.transcribe(
                        source=command.source,
                        media=command.media,
                        media_type=command.media_type or "",
                        language=command.language,
                    ),
                )
            except Exception:
                return await self._finish(
                    command, self._human_review(command, "media_transcription_unavailable")
                )
            text = "\n".join(part for part in (command.text.strip(), transcript.strip()) if part)
            if not text:
                return await self._finish(command, self._human_review(command, "media_unreadable"))
            if (emergency_reason := self._emergency_reason(text)) is not None:
                return await self._hand_off(command, emergency_reason)

        try:
            redaction = await _step(
                "intake.pii_redaction",
                self._pii_redactor.redact(text, language=command.language or "en"),
            )
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
            safety_reason = await _step(
                "intake.content_safety", self._safety_inspector.assess(redacted_text)
            )
        except Exception:
            return await self._finish(
                command,
                self._human_review(command, "content_safety_unavailable"),
                redacted_text,
                redaction.encrypted_mapping,
            )
        if safety_reason is not None:
            return await self._finish(
                command,
                self._human_review(command, safety_reason),
                redacted_text,
                redaction.encrypted_mapping,
            )

        try:
            classification = await _step(
                "intake.classification", self._classifier.classify(redacted_text)
            )
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

    async def _hand_off(self, command: IntakeCommand, reason_code: str) -> IntakeResult:
        await self._emergency_handoff(command.request_id, reason_code)
        return await self._finish(
            command,
            IntakeResult(
                request_id=command.request_id,
                status=IntakeStatus.EMERGENCY_HANDOFF,
                reason_code=reason_code,
            ),
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
