"""Human-in-the-loop operations: the review queue and the work-order lifecycle.

Decisions are recorded as fixed reason codes, never free text, so the audit trail and
reports cannot accumulate personal data.
"""

from __future__ import annotations

from enum import StrEnum


class ReviewCaseStatus(StrEnum):
    QUEUED = "queued"
    EMERGENCY_ON_CALL_QUEUE = "emergency_on_call_queue"
    RESOLVED = "resolved"


class ReviewResolution(StrEnum):
    WORK_ORDER_CREATED = "work_order_created"
    DISMISSED = "dismissed"


class DismissalReason(StrEnum):
    DUPLICATE = "duplicate"
    NOT_ACTIONABLE = "not_actionable"
    OUT_OF_SCOPE = "out_of_scope"
    HANDLED_BY_ON_CALL = "handled_by_on_call"
    RESOLVED_BY_TENANT = "resolved_by_tenant"
    ABUSIVE_OR_SPAM = "abusive_or_spam"


class WorkOrderStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    OPEN = "open"
    REJECTED = "rejected"
    COMPLETED = "completed"


class RejectionReason(StrEnum):
    DUPLICATE = "duplicate"
    NOT_ACTIONABLE = "not_actionable"
    COST_NOT_APPROVED = "cost_not_approved"
    SAFETY_REVIEW_NEEDED = "safety_review_needed"
    OUT_OF_SCOPE = "out_of_scope"


# Which work-order transitions are allowed, and by which action.
WORK_ORDER_TRANSITIONS: dict[str, tuple[frozenset[WorkOrderStatus], WorkOrderStatus]] = {
    "approve": (frozenset({WorkOrderStatus.PENDING_APPROVAL}), WorkOrderStatus.OPEN),
    "reject": (frozenset({WorkOrderStatus.PENDING_APPROVAL}), WorkOrderStatus.REJECTED),
    "complete": (frozenset({WorkOrderStatus.OPEN}), WorkOrderStatus.COMPLETED),
}
