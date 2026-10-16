from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class IntakeRequestRecord(Base):
    __tablename__ = "intake_requests"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(
        Uuid, unique=True, index=True, default=uuid4
    )
    source: Mapped[str] = mapped_column(String(16))
    redacted_description: Mapped[str | None] = mapped_column(
        String(10_000), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    work_order_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WorkOrderRecord(Base):
    __tablename__ = "work_orders"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("intake_requests.request_id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class HumanReviewCase(Base):
    __tablename__ = "human_review_cases"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("intake_requests.request_id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    reason_code: Mapped[str] = mapped_column(String(64))
    redacted_description: Mapped[str | None] = mapped_column(
        String(10_000), nullable=True
    )
    encrypted_source: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PiiMappingRecord(Base):
    __tablename__ = "pii_mappings"

    request_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("intake_requests.request_id", ondelete="CASCADE"),
        primary_key=True,
    )
    encrypted_mapping: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IntakeAuditEvent(Base):
    __tablename__ = "intake_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    event_type: Mapped[str] = mapped_column(String(48))
    reason_code: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RateLimitCounter(Base):
    __tablename__ = "rate_limit_counters"

    bucket_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
