"""When a classified request needs the incident team (spec 4.3, FR-20).

Pure rules, no I/O. A request that meets them still gets its work order (it already needs a
manager's approval at these priorities); escalation additionally opens an incident so the team
can investigate and attach a plan before that approval. Emergencies never reach this point:
they are handed to on-call before any classification.
"""

from __future__ import annotations

from backend.app.domain.intake import Classification, MaintenanceCategory

# Equipment-centred trades, where diagnosis, parts and safety procedures matter.
EQUIPMENT_CATEGORIES = frozenset(
    {MaintenanceCategory.HVAC, MaintenanceCategory.ELECTRICAL, MaintenanceCategory.PLUMBING}
)
ESCALATION_MIN_PRIORITY = 4

HIGH_PRIORITY_EQUIPMENT = "high_priority_equipment"
MANUAL_ESCALATION = "manual_escalation"


def escalation_reason(classification: Classification) -> str | None:
    """A reason code when the request should go to the incident team, otherwise None.

    The repeat-failure criterion (2 work orders on the same asset in 30 days) arrives with the
    asset history tools; a manager can always escalate manually.
    """
    if (
        classification.category in EQUIPMENT_CATEGORIES
        and classification.priority >= ESCALATION_MIN_PRIORITY
    ):
        return HIGH_PRIORITY_EQUIPMENT
    return None
