from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequestSource(StrEnum):
    TEXT = "text"
    PHOTO = "photo"
    VOICE = "voice"
    # A scanned letter, form or notice: PDF, multi-page TIFF, or a phone scan (JPEG/PNG).
    DOCUMENT = "document"


class IntakeStatus(StrEnum):
    EMERGENCY_HANDOFF = "emergency_handoff"
    HUMAN_REVIEW = "human_review"
    PENDING_APPROVAL = "pending_approval"
    WORK_ORDER_CREATED = "work_order_created"
    # A reviewer closed the request without a work order (duplicate, not actionable...).
    DISMISSED = "dismissed"


class MaintenanceCategory(StrEnum):
    HVAC = "hvac"
    PLUMBING = "plumbing"
    ELECTRICAL = "electrical"
    SECURITY = "security"
    STRUCTURAL = "structural"
    CLEANING = "cleaning"
    OTHER = "other"


MAX_MEDIA_BYTES = 8 * 1024 * 1024
PHOTO_MEDIA_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp", "image/tiff"}
)
DOCUMENT_MEDIA_TYPES = frozenset({"application/pdf", "image/tiff", "image/jpeg", "image/png"})
# Only the first pages of a document are read; maintenance requests are short.
MAX_DOCUMENT_PAGES = 5
# Text taken from a photo or document is capped before redaction (Azure AI Language reads
# up to 5,120 characters per document). The emergency screen sees the full text first.
MAX_ATTACHMENT_CHARS = 5_000
# Stored redacted text (intake_requests.redacted_description is varchar(10000)). Placeholders
# such as [PII_0001] can be longer than the value they replace, hence the headroom.
MAX_STORED_TEXT_CHARS = 10_000
ATTACHMENT_HEADROOM_CHARS = 500
VOICE_MEDIA_TYPES = frozenset(
    {
        "audio/wav",
        "audio/x-wav",
        "audio/mpeg",
        "audio/mp4",
        "audio/aac",
        "audio/ogg",
        "audio/webm",
        "audio/flac",
    }
)


MEDIA_TYPES_BY_SOURCE = {
    RequestSource.PHOTO: PHOTO_MEDIA_TYPES,
    RequestSource.VOICE: VOICE_MEDIA_TYPES,
    RequestSource.DOCUMENT: DOCUMENT_MEDIA_TYPES,
}


class IntakeCommand(BaseModel):
    """A request as submitted. For photo, voice and document, ``text`` is an optional caption.

    Raw media is held only in memory for transcription and is never stored.
    """

    model_config = ConfigDict(extra="forbid")

    request_id: UUID = Field(default_factory=uuid4)
    source: RequestSource = RequestSource.TEXT
    text: str = Field(default="", max_length=10_000)
    # ISO 639-1 hint from the client; improves speech recognition and PII detection.
    language: Literal["en", "es", "fr"] | None = None
    media: bytes | None = Field(default=None, repr=False, max_length=MAX_MEDIA_BYTES)
    media_type: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_source_matches_content(self) -> IntakeCommand:
        if self.source is RequestSource.TEXT:
            if self.media is not None:
                raise ValueError("text requests cannot carry media")
            if not self.text.strip():
                raise ValueError("text is required")
            return self
        allowed = MEDIA_TYPES_BY_SOURCE[self.source]
        if not self.media:
            raise ValueError(f"{self.source.value} requests need media")
        if self.media_type not in allowed:
            raise ValueError(f"media_type must be one of {sorted(allowed)}")
        return self


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: MaintenanceCategory
    priority: int = Field(ge=1, le=5, description="1 is lowest priority; 5 is highest.")
    confidence: float = Field(ge=0, le=1)
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    requires_human_review: bool = False


class PiiRedaction(BaseModel):
    # What is stored and classified: the tenant's words, then any attachment text.
    redacted_text: str = Field(min_length=1, max_length=MAX_STORED_TEXT_CHARS)
    encrypted_mapping: bytes
    # The same two parts separately, set when an attachment was redacted. Prompt Shields
    # checks the tenant's words as a user prompt and attachment text as a document.
    redacted_user_text: str = ""
    redacted_attachment: str = ""


class Translation(BaseModel):
    """Machine translation of the redacted text for staff. Placeholders such as [PII_0001] are kept."""

    text: str
    # ISO 639-1 code of the language it was translated from, for example "fr".
    source_language: str = Field(min_length=2, max_length=8)


class IntakeResult(BaseModel):
    request_id: UUID
    status: IntakeStatus
    reason_code: str
    category: MaintenanceCategory | None = None
    priority: int | None = None
    confidence: float | None = None
    work_order_id: UUID | None = None
