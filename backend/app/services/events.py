"""Event delivery (spec 6.2): transactional outbox -> event bus -> idempotent consumers.

- ``OutboxDispatcher`` claims undelivered events with ``FOR UPDATE SKIP LOCKED`` (several
  workers never take the same event), publishes each to the bus and marks it delivered. A
  failure is retried on the next pass; after ``MAX_DELIVERY_ATTEMPTS`` the event is
  dead-lettered and ``on_dead_letter`` sends its incident to people.
- ``EventBus`` is the port. ``InProcessEventBus`` delivers to subscribers in this process (local
  development, tests, a single worker). In Azure, ``services/service_bus.py`` publishes to a
  Service Bus topic instead, with one subscription per consumer, and runs the same consumers.
- Consumers are idempotent: a consumer records the event id in ``processed_events`` in the same
  transaction as its work, so a redelivered event changes nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import OutboxEventRecord, ProcessedEventRecord

MAX_DELIVERY_ATTEMPTS = 3

_log = logging.getLogger("facilityops.events")
_tracer = trace.get_tracer("facilityops.events")

Handler = Callable[[AsyncSession, OutboxEventRecord], Awaitable[None]]
DeadLetterHandler = Callable[[AsyncSession, OutboxEventRecord], Awaitable[None]]


@dataclass(frozen=True)
class Subscription:
    # A stable consumer name: it is the idempotency key, with the event id.
    consumer: str
    event_type: str
    handler: Handler


class EventBus(Protocol):
    async def publish(self, event: OutboxEventRecord) -> None: ...


class InProcessEventBus:
    """Delivers each event to its subscribers in this process, one transaction per consumer."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], subscriptions: Sequence[Subscription]
    ) -> None:
        self._session_factory = session_factory
        self._subscriptions = list(subscriptions)

    async def publish(self, event: OutboxEventRecord) -> None:
        for subscription in self._subscriptions:
            if subscription.event_type == event.event_type:
                await consume(self._session_factory, subscription, event)


async def consume(
    session_factory: async_sessionmaker[AsyncSession], subscription: Subscription, event: OutboxEventRecord
) -> None:
    """Run one consumer on one event, at most once: the work and its processed marker commit together."""
    with _tracer.start_as_current_span(
        f"event.consume {event.event_type}", record_exception=False, set_status_on_exception=False
    ) as span:
        span.set_attribute("facilityops.event_id", str(event.id))
        span.set_attribute("facilityops.consumer", subscription.consumer)
        async with session_factory() as session:
            claimed = await session.execute(
                insert(ProcessedEventRecord)
                .values(consumer=subscription.consumer, event_id=event.id)
                .on_conflict_do_nothing()
                .returning(ProcessedEventRecord.event_id)
            )
            if claimed.first() is None:
                _log.info("event already handled", extra={"step": event.event_type, "status": "duplicate"})
                return
            await subscription.handler(session, event)
            await session.commit()


class OutboxDispatcher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        *,
        on_dead_letter: DeadLetterHandler | None = None,
        batch_size: int = 20,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._on_dead_letter = on_dead_letter
        self._batch_size = batch_size

    async def run_once(self) -> int:
        """Deliver one batch of pending events. Returns how many were handled (any outcome)."""
        async with self._session_factory() as session:
            events = list(
                await session.scalars(
                    select(OutboxEventRecord)
                    .where(OutboxEventRecord.published_at.is_(None), OutboxEventRecord.dead_lettered_at.is_(None))
                    .order_by(OutboxEventRecord.created_at)
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            for event in events:
                await self._deliver(session, event)
            await session.commit()
        return len(events)

    async def _deliver(self, session: AsyncSession, event: OutboxEventRecord) -> None:
        with _tracer.start_as_current_span(
            f"event.deliver {event.event_type}", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("facilityops.event_id", str(event.id))
            try:
                await self._bus.publish(event)
            except Exception as error:
                event.attempts += 1
                event.last_error_type = type(error).__name__[:64]
                span.set_status(trace.Status(trace.StatusCode.ERROR, event.last_error_type))
                if event.attempts >= MAX_DELIVERY_ATTEMPTS:
                    event.dead_lettered_at = datetime.now(UTC)
                    _log.error(
                        "event dead-lettered",
                        extra={"step": event.event_type, "status": "dead_letter", "error_type": event.last_error_type},
                    )
                    if self._on_dead_letter is not None:
                        await self._on_dead_letter(session, event)
                else:
                    _log.warning(
                        "event delivery failed; will retry",
                        extra={"step": event.event_type, "status": "retry", "error_type": event.last_error_type},
                    )
                return
            event.published_at = datetime.now(UTC)
            _log.info("event delivered", extra={"step": event.event_type, "status": "delivered"})
