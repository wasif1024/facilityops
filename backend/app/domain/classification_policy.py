"""How a classification becomes a route. Code decides; the model only classifies.

The model's self-reported confidence clusters on a few habitual values (0.74, 0.78,
0.86), so a single threshold at 0.75 made borderline requests flip between automation
and human review on identical input. Within a confirmation band the policy therefore
asks again: if independent classifications agree, agreement is the evidence; if they
don't, a person decides. Measured in docs/specification.md section 10.

The production coordinator and the eval runner both use this policy, so evaluations
measure exactly what production does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from backend.app.domain.intake import Classification


class Route(StrEnum):
    HUMAN_REVIEW = "human_review"
    PENDING_APPROVAL = "pending_approval"
    WORK_ORDER = "work_order"


@dataclass(frozen=True)
class RoutingDecision:
    route: Route
    reason_code: str


@dataclass(frozen=True)
class ClassificationPolicy:
    approval_threshold: int = 4
    # Confidence at or above band_high is trusted; below band_low goes to a person.
    band_low: float = 0.70
    band_high: float = 0.80
    # Extra classifications inside the band. 0 disables confirmation and falls back to
    # a single minimum_confidence threshold.
    confirmations: int = 2
    minimum_confidence: float = 0.75

    def __post_init__(self) -> None:
        if not 1 <= self.approval_threshold <= 5:
            raise ValueError("approval_threshold must be between 1 and 5")
        if not 0 <= self.band_low <= self.band_high <= 1:
            raise ValueError("confirmation band must satisfy 0 <= low <= high <= 1")
        if self.confirmations < 0:
            raise ValueError("confirmations must not be negative")

    def needs_confirmation(self, first: Classification) -> bool:
        return (
            self.confirmations > 0
            and not first.requires_human_review
            and self.band_low <= first.confidence < self.band_high
        )

    def decide(
        self,
        first: Classification,
        confirmations: Sequence[Classification | None] = (),
    ) -> RoutingDecision:
        """Route a classification. ``None`` in confirmations is a failed call."""
        if first.requires_human_review:
            return RoutingDecision(Route.HUMAN_REVIEW, "classification_uncertain")

        if self.confirmations == 0:
            if first.confidence < self.minimum_confidence:
                return RoutingDecision(Route.HUMAN_REVIEW, "classification_uncertain")
            return self._automated(first, "classified")

        if first.confidence < self.band_low:
            return RoutingDecision(Route.HUMAN_REVIEW, "classification_uncertain")
        if first.confidence >= self.band_high:
            return self._automated(first, "classified")

        if len(confirmations) < self.confirmations:
            raise ValueError("a borderline classification needs its confirmations")
        agreed = all(
            other is not None
            and not other.requires_human_review
            and (other.category, other.priority) == (first.category, first.priority)
            for other in confirmations
        )
        if not agreed:
            return RoutingDecision(Route.HUMAN_REVIEW, "classification_unconfirmed")
        return self._automated(first, "classified_by_agreement")

    def _automated(self, classification: Classification, reason_code: str) -> RoutingDecision:
        if classification.priority >= self.approval_threshold:
            return RoutingDecision(Route.PENDING_APPROVAL, "approval_required")
        return RoutingDecision(Route.WORK_ORDER, reason_code)
