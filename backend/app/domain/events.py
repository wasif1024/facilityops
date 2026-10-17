"""Domain events (spec 6.2): facts that other parts of the system react to.

Events carry ids, types and reason codes only, never request text or personal data; a consumer
reads what it needs from the database with its own permissions. They are written to the
transactional outbox in the same transaction as the state change they describe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class EventType(StrEnum):
    INTAKE_ESCALATED = "intake.escalated"
    INCIDENT_PLAN_READY = "incident.plan_ready"
    INCIDENT_PLAN_DECIDED = "incident.plan_decided"
    INCIDENT_CLOSED = "incident.closed"


@dataclass(frozen=True)
class Event:
    type: EventType
    # The incident (or other aggregate) the event is about.
    aggregate_id: UUID
    # Ids and codes only, for example {"request_id": "...", "reason_code": "..."}.
    payload: dict[str, Any] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        for key, value in self.payload.items():
            if isinstance(value, str) and len(value) > 64:
                raise ValueError(f"Event payload {key!r} looks like free text; events carry ids and codes only")
