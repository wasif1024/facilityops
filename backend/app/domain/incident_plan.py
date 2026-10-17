"""The incident plan: what the incident team proposes, for a facility manager to decide (FR-23, FR-24).

A plan is a set of claims (each backed by citations to manuals, records or procedures), proposed
actions (dispatch, order parts, notify the tenant...) and safety flags. It is only a proposal:
nothing in it happens until a manager approves, and then the application, not an agent,
carries out the actions (FR-25).

Plans are produced by models from redacted text, are schema-validated like the classifier's
answer, and are readable only by roles with incidents:read.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

# Above this estimated cost the plan is marked high cost (spec 7: cost > threshold).
DEFAULT_COST_APPROVAL_THRESHOLD = 500.0
# Notice limits (FR-61): what the communicator is asked for, with room for a manager's edits.
NOTICE_SUBJECT_MAX = 120
NOTICE_BODY_MAX = 1_500

_PLACEHOLDER = re.compile(r"\[PII_\d{4}\]")


def has_placeholder(text: str) -> bool:
    """True when text holds a [PII_nnnn] placeholder: it must never reach a tenant notice."""
    return bool(_PLACEHOLDER.search(text))


class SourceType(StrEnum):
    MANUAL = "manual"  # equipment manual section (knowledge base)
    PROCEDURE = "procedure"  # safety or lockout/tagout procedure
    RECORD = "record"  # maintenance history, asset or work-order record
    CONTRACT = "contract"  # vendor terms, SLA


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: SourceType
    # Document or record id, for example "manual:boiler-hx200" or "work_order:<uuid>".
    source_id: str = Field(min_length=1, max_length=128)
    # Section, page or field, for example "6.2 Error codes".
    section: str | None = Field(default=None, max_length=128)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=500)
    # Which specialist made the claim, for example "diagnostician".
    specialist: str = Field(min_length=1, max_length=48)
    citations: list[Citation] = Field(default_factory=list, max_length=8)

    @computed_field
    @property
    def verified(self) -> bool:
        """A claim without a citation is shown to the reviewer as unverified (spec 5.2)."""
        return bool(self.citations)


class ActionType(StrEnum):
    DISPATCH_TECHNICIAN = "dispatch_technician"
    ORDER_PART = "order_part"
    SCHEDULE_VISIT = "schedule_visit"
    NOTIFY_TENANT = "notify_tenant"
    APPLY_LOCKOUT = "apply_lockout"


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionType
    description: str = Field(min_length=1, max_length=300)
    estimated_cost: float | None = Field(default=None, ge=0, le=1_000_000)
    # What the application needs to carry the action out after approval (FR-25).
    technician_id: str | None = Field(default=None, pattern=r"^tech-[0-9]{3}$")  # dispatch_technician
    part_sku: str | None = Field(default=None, max_length=64)  # order_part
    quantity: int | None = Field(default=None, ge=1, le=100)  # order_part


class NoticeDraft(BaseModel):
    """The communicator's notice to the resident, in English (FR-61). A person approves it first."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=NOTICE_SUBJECT_MAX)
    body: str = Field(min_length=1, max_length=NOTICE_BODY_MAX)


class IncidentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1_000)
    claims: list[Claim] = Field(default_factory=list, max_length=30)
    actions: list[ProposedAction] = Field(default_factory=list, max_length=15)
    # Safety & compliance can block a plan: it must not be approved until the issue is resolved.
    safety_block: bool = False
    safety_notes: str | None = Field(default=None, max_length=500)
    open_questions: list[str] = Field(default_factory=list, max_length=10)
    tenant_notice: NoticeDraft | None = None

    @model_validator(mode="after")
    def _no_personal_data_in_the_notice(self) -> IncidentPlan:
        # A draft that refers to personal data is dropped, not shown: the reviewer is told why.
        notice = self.tenant_notice
        if notice is not None and (has_placeholder(notice.subject) or has_placeholder(notice.body)):
            self.tenant_notice = None
            if len(self.open_questions) < 10:
                self.open_questions.append("The tenant notice draft referred to personal details and was removed.")
        return self

    @computed_field
    @property
    def estimated_total_cost(self) -> float:
        return round(sum(a.estimated_cost or 0 for a in self.actions), 2)

    @computed_field
    @property
    def unverified_claims(self) -> int:
        return sum(1 for claim in self.claims if not claim.citations)

    @classmethod
    def from_stored(cls, data: dict) -> IncidentPlan:
        """Load a stored plan. Stored JSON includes the computed fields (for readers of the API);
        they are derived again here, never trusted from storage."""
        clean = {k: v for k, v in data.items() if k not in {"estimated_total_cost", "unverified_claims"}}
        clean["claims"] = [{k: v for k, v in c.items() if k != "verified"} for c in clean.get("claims", [])]
        return cls.model_validate(clean)

    def flags(self, cost_threshold: float = DEFAULT_COST_APPROVAL_THRESHOLD) -> list[str]:
        """Reasons the reviewer must look closely; shown with the plan and recorded on the trail."""
        flags = []
        if self.safety_block:
            flags.append("safety_block")
        if self.estimated_total_cost > cost_threshold:
            flags.append("high_cost")
        if self.unverified_claims:
            flags.append("unverified_claims")
        return flags
