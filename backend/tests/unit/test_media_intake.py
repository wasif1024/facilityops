import pytest
from pydantic import ValidationError

from backend.app.domain.intake import IntakeCommand, IntakeStatus, RequestSource
from backend.app.domain.workflow import IntakeCoordinator

from .test_intake_workflow import (
    FakeClassifier,
    FakeDecisionWriter,
    FakePiiRedactor,
    FakeSafetyInspector,
    FakeWorkOrderWriter,
)

PNG = b"\x89PNG synthetic image bytes"
WAV = b"RIFF synthetic audio bytes"


class FakeTranscriber:
    def __init__(self, transcript: str = "", error: Exception | None = None) -> None:
        self.transcript = transcript
        self.error = error
        self.calls: list[dict] = []

    async def transcribe(self, *, source, media, media_type, language) -> str:
        self.calls.append(
            {"source": source, "media": media, "media_type": media_type, "language": language}
        )
        if self.error:
            raise self.error
        return self.transcript


class LanguageRecordingRedactor(FakePiiRedactor):
    def __init__(self) -> None:
        super().__init__()
        self.languages: list[str] = []

    async def redact(self, text: str, language: str = "en"):
        self.languages.append(language)
        return await super().redact(text, language)


def build(transcriber: FakeTranscriber | None):
    redactor = LanguageRecordingRedactor()
    classifier = FakeClassifier()
    handoffs: list[tuple[object, str]] = []

    async def emergency_handoff(request_id, reason_code):
        handoffs.append((request_id, reason_code))

    coordinator = IntakeCoordinator(
        pii_redactor=redactor,
        safety_inspector=FakeSafetyInspector(),
        classifier=classifier,
        work_order_writer=FakeWorkOrderWriter(),
        decision_writer=FakeDecisionWriter(),
        emergency_handoff=emergency_handoff,
        media_transcriber=transcriber,
    )
    return coordinator, redactor, classifier, handoffs


def photo(text: str = "", **extra) -> IntakeCommand:
    return IntakeCommand(
        source=RequestSource.PHOTO, text=text, media=PNG, media_type="image/png", **extra
    )


@pytest.mark.asyncio
async def test_photo_transcript_and_caption_are_redacted_together_in_the_given_language():
    transcriber = FakeTranscriber("Photo shows: a sink. Text in photo: leak in unit 4B")
    coordinator, redactor, classifier, _ = build(transcriber)

    result = await coordinator.process(photo("Mira esto", language="es"))

    assert result.status is IntakeStatus.WORK_ORDER_CREATED
    assert redactor.calls == ["Mira esto\nPhoto shows: a sink. Text in photo: leak in unit 4B"]
    assert redactor.languages == ["es"]
    assert transcriber.calls[0]["language"] == "es"
    assert transcriber.calls[0]["media_type"] == "image/png"


@pytest.mark.asyncio
async def test_emergency_in_caption_skips_transcription():
    transcriber = FakeTranscriber("anything")
    coordinator, redactor, _, handoffs = build(transcriber)

    result = await coordinator.process(photo("There is smoke everywhere!"))

    assert result.status is IntakeStatus.EMERGENCY_HANDOFF
    assert transcriber.calls == []
    assert redactor.calls == []
    assert handoffs[0][1] == "emergency_fire"


@pytest.mark.asyncio
async def test_emergency_in_voice_transcript_bypasses_ai():
    transcriber = FakeTranscriber("I can smell gas in the kitchen")
    coordinator, redactor, classifier, handoffs = build(transcriber)

    result = await coordinator.process(
        IntakeCommand(source=RequestSource.VOICE, media=WAV, media_type="audio/wav")
    )

    assert result.status is IntakeStatus.EMERGENCY_HANDOFF
    assert result.reason_code == "emergency_gas"
    assert redactor.calls == [] and classifier.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transcriber", "reason"),
    [
        (FakeTranscriber(error=TimeoutError()), "media_transcription_unavailable"),
        (None, "media_transcription_unavailable"),
        (FakeTranscriber(""), "media_unreadable"),
    ],
)
async def test_media_problems_fail_closed_to_human_review(transcriber, reason):
    coordinator, redactor, classifier, _ = build(transcriber)

    result = await coordinator.process(photo())

    assert result.status is IntakeStatus.HUMAN_REVIEW
    assert result.reason_code == reason
    assert redactor.calls == [] and classifier.calls == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source": RequestSource.TEXT, "text": "hi", "media": PNG}, "cannot carry media"),
        ({"source": RequestSource.TEXT, "text": "  "}, "text is required"),
        ({"source": RequestSource.PHOTO}, "need media"),
        (
            {"source": RequestSource.PHOTO, "media": PNG, "media_type": "audio/wav"},
            "media_type must be one of",
        ),
        (
            {"source": RequestSource.VOICE, "media": WAV, "media_type": "application/pdf"},
            "media_type must be one of",
        ),
    ],
)
def test_command_rejects_mismatched_content(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        IntakeCommand(**kwargs)


def test_media_is_hidden_from_repr():
    assert "synthetic image" not in repr(photo())
