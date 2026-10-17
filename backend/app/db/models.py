from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    # Staff translation of redacted_description (FR-60): machine-translated, same placeholders.
    # Text, not a bounded string: a translation can be longer than its source.
    translated_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Language the translation was made from (ISO 639-1); empty when no translation was needed.
    source_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    work_order_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    submitted_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
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
    status: Mapped[str] = mapped_column(String(32), index=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    assigned_technician_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Fixed reason code when rejected; never free text.
    decision_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Fixed reason code when dismissed; never free text.
    resolution_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("identity_provider", "external_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    identity_provider: Mapped[str] = mapped_column(String(16))
    # Entra object id (oid), stable across apps; never the email address.
    external_id: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RoleRecord(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(256))
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)


class PermissionRecord(Base):
    __tablename__ = "permissions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(String(256))


class RolePermissionRecord(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (PrimaryKeyConstraint("role_id", "permission_id"),)

    role_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("roles.id", ondelete="CASCADE")
    )
    permission_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("permissions.id", ondelete="CASCADE")
    )


class UserRoleRecord(Base):
    __tablename__ = "user_roles"
    __table_args__ = (PrimaryKeyConstraint("user_id", "role_id"),)

    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    role_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("roles.id", ondelete="RESTRICT"), index=True
    )
    granted_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SecurityAuditEvent(Base):
    """Who changed access or viewed protected data. Details never hold PII."""

    __tablename__ = "security_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class EvaluationRunRecord(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    mode: Mapped[str] = mapped_column(String(16))
    # running -> passed | failed (a gate failed) | error (the run itself crashed)
    status: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(16))
    dataset: Mapped[str] = mapped_column(String(128))
    dataset_sha: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_sha: Mapped[str | None] = mapped_column(String(16), nullable=True)
    git_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    case_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    gates: Mapped[dict] = mapped_column(JSONB, default=dict)
    failures: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    triggered_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EvaluationCaseResultRecord(Base):
    """Per-case outcome by golden-set id. Request text stays in the dataset file."""

    __tablename__ = "evaluation_case_results"
    __table_args__ = (PrimaryKeyConstraint("run_id", "case_id"),)

    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("evaluation_runs.id", ondelete="CASCADE")
    )
    case_id: Mapped[str] = mapped_column(String(32))
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    expected: Mapped[dict] = mapped_column(JSONB, default=dict)
    predicted: Mapped[dict] = mapped_column(JSONB, default=dict)
    failures: Mapped[list] = mapped_column(JSONB, default=list)
    passed: Mapped[bool] = mapped_column(Boolean)


class AgentRunRecord(Base):
    """One agent invocation: timing, tokens, outcome. Content is in agent_run_contents."""

    __tablename__ = "agent_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    agent_name: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_sha: Mapped[str | None] = mapped_column(String(16), nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    evaluation_run_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("evaluation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16))
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # OpenTelemetry trace id: find this call in Application Insights.
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class DecisionStepRecord(Base):
    """One step of the decision trail for a request: what ran, what it found, how long it took.

    Append-only (a database trigger refuses updates and deletes outside the retention purge).
    Details hold labels, scores and counts only; never request text or personal data.
    """

    __tablename__ = "decision_steps"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    step: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    service: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AgentRunContentRecord(Base):
    """What was sent to a model and what it answered, for one agent run.

    Models only ever receive redacted text, so this holds no personal data. Readable only by
    roles that can read requests, and purged after MODEL_CONTENT_RETENTION_DAYS.
    """

    __tablename__ = "agent_run_contents"

    agent_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True
    )
    input_text: Mapped[str] = mapped_column(Text)
    output_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class PromptVersionRecord(Base):
    """The exact system instructions behind each prompt hash, so any run can be explained."""

    __tablename__ = "prompt_versions"

    prompt_sha: Mapped[str] = mapped_column(String(16), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(64))
    instructions: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IncidentRecord(Base):
    """A request the incident team investigates (Phase 2). One per request.

    Lifecycle: escalated -> investigating -> plan_ready -> (approved | rejected) -> closed, with
    needs_review whenever the team can't finish (budget, error, team unavailable, dead letter).
    """

    __tablename__ = "incidents"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("intake_requests.request_id", ondelete="CASCADE"), unique=True
    )
    work_order_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    # Why it was escalated, for example "high_priority_equipment" or "manual_escalation".
    escalation_reason: Mapped[str] = mapped_column(String(64))
    # Why it last changed state, for example "incident_team_unavailable".
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    escalated_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutboxEventRecord(Base):
    """Transactional outbox (spec 6.2): written with the state change, delivered afterwards.

    Payloads hold ids and codes only. Delivery is at-least-once; after MAX_DELIVERY_ATTEMPTS
    failures an event is dead-lettered and its incident goes to human review.
    """

    __tablename__ = "outbox_events"
    # Delivery polls only undelivered events: a partial index keeps that cheap as the table grows.
    __table_args__ = (
        Index(
            "ix_outbox_events_pending",
            "created_at",
            postgresql_where=text("published_at IS NULL AND dead_lettered_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ProcessedEventRecord(Base):
    """Which consumer handled which event: a redelivered event is skipped (idempotency)."""

    __tablename__ = "processed_events"
    __table_args__ = (PrimaryKeyConstraint("consumer", "event_id"),)

    consumer: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[UUID] = mapped_column(Uuid)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IncidentPlanRecord(Base):
    """One version of an incident plan (FR-23) and the facility manager's decision on it (FR-24).

    Each revision is a new version; decided versions are never edited. The plan is the
    schema-validated JSON of backend.app.domain.incident_plan.IncidentPlan.
    """

    __tablename__ = "incident_plans"
    __table_args__ = (UniqueConstraint("incident_id", "version"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    plan: Mapped[dict] = mapped_column(JSONB)
    # Review flags at creation, for example ["safety_block", "high_cost"].
    flags: Mapped[list] = mapped_column(JSONB, default=list)
    # proposed -> approved | revision_requested | rejected
    status: Mapped[str] = mapped_column(String(24))
    # The manager's instructions for a revision (staff-written, shown to the team).
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---- Facility data used by the incident team's read-only tools (Phase 2) ----
# Filled from backend/app/fixtures/synthetic_world.json in development and evals; every row is invented.


class BuildingRecord(Base):
    __tablename__ = "buildings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    units: Mapped[int] = mapped_column(Integer, default=0)


class AssetRecord(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    building_id: Mapped[str] = mapped_column(String(64), ForeignKey("buildings.id", ondelete="CASCADE"), index=True)
    # Same values as the intake categories: hvac, plumbing, electrical...
    category: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    manufacturer: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    location: Mapped[str] = mapped_column(String(128))
    installed_on: Mapped[date] = mapped_column(Date)
    # Knowledge-base document for this model, for example "manual:boiler-hx200".
    manual_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    criticality: Mapped[str] = mapped_column(String(16), default="normal")


class MaintenanceRecord(Base):
    __tablename__ = "maintenance_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(64), ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    # repair, service or inspection
    kind: Mapped[str] = mapped_column(String(16))
    fault_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str] = mapped_column(String(240))
    cost: Mapped[float] = mapped_column(Float, default=0)


class VendorRecord(Base):
    __tablename__ = "vendors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    trades: Mapped[list] = mapped_column(JSONB, default=list)
    sla_hours: Mapped[int] = mapped_column(Integer)
    callout_fee: Mapped[float] = mapped_column(Float)
    contract_id: Mapped[str] = mapped_column(String(64))
    contract_ends: Mapped[date] = mapped_column(Date)


class PartRecord(Base):
    __tablename__ = "parts"

    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    fits_models: Mapped[list] = mapped_column(JSONB, default=list)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    unit_cost: Mapped[float] = mapped_column(Float)
    vendor_id: Mapped[str] = mapped_column(String(64), ForeignKey("vendors.id", ondelete="RESTRICT"))
    lead_time_days: Mapped[int] = mapped_column(Integer, default=0)


class TechnicianProfileRecord(Base):
    """Skills and shift of a technician. Tools return the profile id and skills, never names."""

    __tablename__ = "technician_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    skills: Mapped[list] = mapped_column(JSONB, default=list)
    # Working hours (0-23) and weekdays (0 = Monday).
    shift_start_hour: Mapped[int] = mapped_column(Integer)
    shift_end_hour: Mapped[int] = mapped_column(Integer)
    weekdays: Mapped[list] = mapped_column(JSONB, default=list)
    on_call: Mapped[bool] = mapped_column(Boolean, default=False)


class PermitRecord(Base):
    __tablename__ = "permits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(64), ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    # For example "electrical_isolation" or "hot_work".
    kind: Mapped[str] = mapped_column(String(48))
    valid_until: Mapped[date] = mapped_column(Date)
