from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol, TypeVar
from uuid import UUID

from opentelemetry import trace

from backend.app.domain.classification_policy import ClassificationPolicy, Route
from backend.app.domain.decision_trail import (
    DecisionStep,
    StepOutcome,
    current_trail,
    decision_trail,
    open_step,
)
from backend.app.domain.emergency import detect_emergency
from backend.app.domain.escalation import escalation_reason
from backend.app.domain.intake import (
    ATTACHMENT_HEADROOM_CHARS,
    MAX_ATTACHMENT_CHARS,
    MAX_STORED_TEXT_CHARS,
    Classification,
    IntakeCommand,
    IntakeResult,
    IntakeStatus,
    PiiRedaction,
    RequestSource,
    Translation,
)
from backend.app.domain.media import check_media, is_image


# No-op unless telemetry is configured. Spans carry ids and reason codes, never text.
_tracer = trace.get_tracer("facilityops.intake")
_log = logging.getLogger("facilityops.intake")
_TRANSLATION_TIMEOUT_SECONDS = 10
T = TypeVar("T")


async def _step(
    name: str,
    awaitable: Awaitable[T],
    *,
    flag: Callable[[T], str | None] | None = None,
    describe: Callable[[T], dict[str, Any]] | None = None,
) -> T:
    """Trace one pipeline step and record it on the decision trail.

    ``flag`` turns a result into a reason code when the step rerouted the request;
    ``describe`` adds non-personal details (labels, scores, counts). Exception messages are
    never recorded, only the type, because they can contain request text.
    """
    with _tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as span, open_step(name) as step:
        try:
            result = await awaitable
        except Exception as error:
            span.set_status(trace.Status(trace.StatusCode.ERROR, type(error).__name__))
            step.details["error"] = type(error).__name__
            raise
        if describe is not None:
            step.details.update(describe(result))
        if flag is not None and (reason := flag(result)) is not None:
            step.outcome, step.reason_code = StepOutcome.FLAGGED, reason
            span.set_attribute("facilityops.reason_code", reason)
        span.set_attribute("facilityops.outcome", step.outcome.value)
        return result


class PiiRedactor(Protocol):
    async def redact(
        self, text: str, language: str = "en", *, attachment: str = ""
    ) -> PiiRedaction:
        """Redact the tenant's words and, separately, text taken from an attachment, with one
        placeholder numbering and one mapping."""
        ...


class MediaTranscriber(Protocol):
    async def transcribe(
        self,
        *,
        source: RequestSource,
        media: bytes,
        media_type: str,
        language: str | None,
    ) -> str:
        """Text found in a photo (caption and OCR), read from a document, or spoken in a
        voice note."""
        ...


class ContentSafetyInspector(Protocol):
    async def assess(self, redacted_text: str, *, documents: Sequence[str] = ()) -> str | None:
        """None when safe; otherwise a reason code such as "prompt_attack_detected".

        ``redacted_text`` is what the tenant wrote or said (checked for jailbreaks);
        ``documents`` is text taken from attachments (checked for injected instructions).
        Harmful content is checked across both.
        """
        ...

    async def assess_image(self, image: bytes) -> str | None:
        """None when the image is safe; otherwise a reason code such as "harmful_image_violence"."""
        ...


class TextTranslator(Protocol):
    async def translate(self, text: str, *, source_language: str | None) -> Translation | None:
        """Translate redacted text into the staff language; None when it is already in it."""
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
        translation: Translation | None = None,
        decision_steps: Sequence[DecisionStep] = (),
        escalation_reason: str | None = None,
    ) -> None:
        """Store the outcome. With an escalation reason, also open an incident and write its
        intake.escalated event, in the same transaction (transactional outbox)."""
        ...


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
        translator: TextTranslator | None = None,
        staff_language: str = "en",
        escalate_incidents: bool = True,
        minimum_confidence: float = 0.75,
        approval_priority_threshold: int = 4,
        policy: ClassificationPolicy | None = None,
    ) -> None:
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")
        self._policy = policy or ClassificationPolicy(
            approval_threshold=approval_priority_threshold,
            minimum_confidence=minimum_confidence,
        )

        self._pii_redactor = pii_redactor
        self._safety_inspector = safety_inspector
        self._classifier = classifier
        self._work_order_writer = work_order_writer
        self._decision_writer = decision_writer
        self._emergency_handoff = emergency_handoff
        self._media_transcriber = media_transcriber
        self._translator = translator
        self._staff_language = staff_language
        self._escalate_incidents = escalate_incidents
        # Staff translations run alongside the safety checks and classifier, one per request.
        self._translations: dict[UUID, asyncio.Task[Translation | None]] = {}

    async def process(self, command: IntakeCommand) -> IntakeResult:
        with _tracer.start_as_current_span("intake.process") as span:
            span.set_attribute("facilityops.request_id", str(command.request_id))
            span.set_attribute("facilityops.source", command.source.value)
            with decision_trail():
                result = await self._run(command)
            span.set_attribute("facilityops.status", result.status.value)
            span.set_attribute("facilityops.reason_code", result.reason_code)
            return result

    async def _run(self, command: IntakeCommand) -> IntakeResult:
        # Screen what the tenant typed first: no network call can delay an emergency.
        if (emergency_reason := self._screen(command.text, "typed_text")) is not None:
            return await self._hand_off(command, emergency_reason)

        user_text = command.text.strip()
        attachment = ""
        image_reason: str | None = None
        if command.media is not None:
            outcome = await self._read_media(command)
            if isinstance(outcome, IntakeResult):
                return outcome
            extracted, image_reason = outcome
            if command.source is RequestSource.VOICE:
                # The tenant's own words, spoken: treated exactly like typed text.
                user_text = "\n".join(part for part in (user_text, extracted) if part)
            else:
                attachment = extracted
            if not user_text and not attachment:
                reason = image_reason or "media_unreadable"
                return await self._finish(command, self._human_review(command, reason))
            # The whole extracted text is screened, before anything is shortened.
            full_text = "\n".join(part for part in (user_text, attachment) if part)
            if (emergency_reason := self._screen(full_text, "all_text")) is not None:
                return await self._hand_off(command, emergency_reason)

        try:
            redaction = await _step(
                "intake.pii_redaction",
                self._pii_redactor.redact(
                    user_text,
                    language=command.language or "en",
                    attachment=_fit_attachment(attachment, user_text),
                ),
            )
        except Exception:
            return await self._finish(
                command, self._human_review(command, "pii_redaction_unavailable")
            )

        redacted_text = redaction.redacted_text
        if not redacted_text.strip():
            current_trail().record(
                "intake.pii_redaction_result", StepOutcome.FLAGGED, reason_code="pii_redaction_empty_result"
            )
            return await self._finish(
                command,
                self._human_review(command, "pii_redaction_empty_result"),
            )
        self._start_translation(command, redacted_text)

        if image_reason is not None:
            # A flagged picture never reaches the classifier. A person sees the redacted text
            # and the reason; the emergency screen above has already run on the same text.
            return await self._finish(
                command,
                self._human_review(command, image_reason),
                redacted_text,
                redaction.encrypted_mapping,
            )

        if redaction.redacted_attachment:
            user_part, documents = redaction.redacted_user_text, [redaction.redacted_attachment]
        else:
            user_part, documents = redacted_text, []
        try:
            safety_reason = await _step(
                "intake.content_safety",
                self._safety_inspector.assess(user_part, documents=documents),
                flag=lambda reason: reason,
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
                "intake.classification",
                self._classifier.classify(redacted_text),
                describe=_describe_classification,
            )
        except Exception:
            return await self._finish(
                command,
                self._human_review(command, "classification_unavailable"),
                redacted_text,
                redaction.encrypted_mapping,
            )

        confirmations: list[Classification | None] = []
        if self._policy.needs_confirmation(classification):
            confirmations = await _step(
                "intake.classification_confirmation",
                self._confirm(redacted_text),
                describe=lambda answers: {
                    "answers": [_describe_classification(a) if a else None for a in answers]
                },
            )
        decision = self._policy.decide(classification, confirmations)
        current_trail().record(
            "intake.routing",
            StepOutcome.DECIDED,
            reason_code=decision.reason_code,
            route=decision.route.value,
            band_low=self._policy.band_low,
            band_high=self._policy.band_high,
            confirmations=self._policy.confirmations,
            approval_priority_threshold=self._policy.approval_threshold,
        )
        if decision.route is Route.HUMAN_REVIEW:
            return await self._finish(
                command,
                self._human_review(command, decision.reason_code, classification),
                redacted_text,
                redaction.encrypted_mapping,
            )

        approval_required = decision.route is Route.PENDING_APPROVAL
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

        escalation = escalation_reason(classification) if self._escalate_incidents else None
        if escalation is not None:
            current_trail().record(
                "intake.escalation",
                StepOutcome.DECIDED,
                reason_code=escalation,
                category=classification.category.value,
                priority=classification.priority,
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
                reason_code=decision.reason_code,
                category=classification.category,
                priority=classification.priority,
                confidence=classification.confidence,
                work_order_id=work_order_id,
            ),
            redacted_text,
            redaction.encrypted_mapping,
            escalation_reason=escalation,
        )

    async def _read_media(self, command: IntakeCommand) -> tuple[str, str | None] | IntakeResult:
        """Check the file, then extract its text and (for images) check the picture itself.

        Returns the extracted text and an image-safety reason code, or a finished human-review
        result when the file can't be processed.
        """
        media, media_type = command.media or b"", command.media_type or ""
        file_problem = check_media(command.source, media, media_type)
        current_trail().record(
            "intake.file_check",
            StepOutcome.FLAGGED if file_problem else StepOutcome.PASSED,
            reason_code=file_problem,
            source=command.source.value,
            media_type=media_type,
            size_kb=round(len(media) / 1024),
        )
        if file_problem is not None:
            return await self._finish(command, self._human_review(command, file_problem))
        if self._media_transcriber is None:
            return await self._finish(
                command, self._human_review(command, "media_transcription_unavailable")
            )

        check_picture = command.source is not RequestSource.VOICE and is_image(media_type)
        transcription, picture = await asyncio.gather(
            _step(
                "intake.media_transcription",
                self._media_transcriber.transcribe(
                    source=command.source,
                    media=media,
                    media_type=media_type,
                    language=command.language,
                ),
                describe=lambda text: {"characters": len(text)},
            ),
            _step(
                "intake.image_safety",
                self._safety_inspector.assess_image(media),
                flag=lambda reason: reason,
            )
            if check_picture
            else _none(),
            return_exceptions=True,
        )
        if isinstance(transcription, BaseException):
            return await self._finish(
                command, self._human_review(command, "media_transcription_unavailable")
            )
        # An unavailable image check fails closed, like every other safety step.
        image_reason = "image_safety_unavailable" if isinstance(picture, BaseException) else picture
        return transcription.strip(), image_reason

    async def _confirm(self, redacted_text: str) -> list[Classification | None]:
        """Independent re-classifications for a borderline answer; failures count as None."""
        results = await asyncio.gather(
            *(
                self._classifier.classify(redacted_text)
                for _ in range(self._policy.confirmations)
            ),
            return_exceptions=True,
        )
        return [r if isinstance(r, Classification) else None for r in results]

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
        escalation_reason: str | None = None,
    ) -> IntakeResult:
        translation = await self._collect_translation(command)
        trail = current_trail()
        trail.record(
            "intake.outcome", StepOutcome.DECIDED, reason_code=result.reason_code, status=result.status.value
        )
        # One structured line per step: codes and durations only, never text.
        for step in trail.ordered():
            _log.info(
                "intake step",
                extra={
                    "step": step.step,
                    "outcome": step.outcome.value,
                    "reason_code": step.reason_code,
                    "duration_ms": step.duration_ms,
                    "service": step.service,
                },
            )
        await self._decision_writer.record_result(
            command=command,
            result=result,
            redacted_text=redacted_text,
            encrypted_mapping=encrypted_mapping,
            translation=translation,
            decision_steps=trail.ordered(),
            escalation_reason=escalation_reason,
        )
        return result

    def _start_translation(self, command: IntakeCommand, redacted_text: str) -> None:
        """Translate the redacted text for staff, unless it is already in their language.

        Runs after every safety step and only ever sees redacted text. It is a convenience:
        its failure never changes the outcome (FR-60).
        """
        if self._translator is None or command.language == self._staff_language:
            return
        self._translations[command.request_id] = asyncio.create_task(
            _step(
                "intake.translation",
                self._translator.translate(redacted_text, source_language=command.language),
                describe=lambda t: {"source_language": t.source_language} if t else {"needed": False},
            )
        )

    async def _collect_translation(self, command: IntakeCommand) -> Translation | None:
        task = self._translations.pop(command.request_id, None)
        if task is None:
            return None
        try:
            return await asyncio.wait_for(task, timeout=_TRANSLATION_TIMEOUT_SECONDS)
        except Exception:
            # The original redacted text is still stored and shown; nothing else depends on this.
            return None

    @staticmethod
    def _screen(text: str, scope: str) -> str | None:
        """The local emergency screen, recorded on the trail (no network, no AI)."""
        emergency_type = detect_emergency(text)
        reason = f"emergency_{emergency_type}" if emergency_type else None
        current_trail().record(
            "intake.emergency_screen",
            StepOutcome.FLAGGED if reason else StepOutcome.PASSED,
            reason_code=reason,
            scope=scope,
            service="local rules",
        )
        return reason

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


async def _none() -> None:
    return None


def _fit_attachment(attachment: str, user_text: str) -> str:
    """Attachment text shortened to what the classifier needs and the database can store."""
    room = MAX_STORED_TEXT_CHARS - len(user_text) - ATTACHMENT_HEADROOM_CHARS
    return attachment[: max(0, min(MAX_ATTACHMENT_CHARS, room))]


def _describe_classification(classification: Classification) -> dict[str, Any]:
    return {
        "category": classification.category.value,
        "priority": classification.priority,
        "confidence": classification.confidence,
        "model_reason_code": classification.reason_code,
        "model_requested_review": classification.requires_human_review,
    }
