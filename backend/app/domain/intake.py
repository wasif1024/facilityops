from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class RequestSource(StrEnum):
    TEXT = "text"
    PHOTO = "photo"
    VOICE = "voice"


class IntakeStatus(StrEnum):
    EMERGENCY_HANDOFF = "emergency_handoff"
    HUMAN_REVIEW = "human_review"
    PENDING_APPROVAL = "pending_approval"
    WORK_ORDER_CREATED = "work_order_created"


class MaintenanceCategory(StrEnum):
    HVAC = "hvac"
    PLUMBING = "plumbing"
    ELECTRICAL = "electrical"
    SECURITY = "security"
    STRUCTURAL = "structural"
    CLEANING = "cleaning"
    OTHER = "other"


class IntakeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID = Field(default_factory=uuid4)
    source: RequestSource = RequestSource.TEXT
    text: str = Field(min_length=1, max_length=10_000)


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: MaintenanceCategory
    priority: int = Field(ge=1, le=5, description="1 is lowest priority; 5 is highest.")
    confidence: float = Field(ge=0, le=1)
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    requires_human_review: bool = False


class PiiRedaction(BaseModel):
    redacted_text: str = Field(min_length=1, max_length=10_000)
    encrypted_mapping: bytes


class IntakeResult(BaseModel):
    request_id: UUID
    status: IntakeStatus
    reason_code: str
    category: MaintenanceCategory | None = None
    priority: int | None = None
    confidence: float | None = None
    work_order_id: UUID | None = None
