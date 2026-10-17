"""Azure Service Bus transport for events (spec 6.2, Phase 3).

The transactional outbox stays the source of truth: the dispatcher publishes each committed
event to the topic, and messages carry only the event id and type. Each consumer
(``incident-team``, ``action-executor``) has its own topic subscription, filtered by event type
in Bicep (infra/modules/service-bus.bicep), and runs exactly the handlers it runs locally, with the
same ``processed_events`` idempotency.

Failures are retried by Service Bus: a handler that raises abandons the message, and after the
subscription's max delivery count (3, as locally) it moves to the dead-letter queue. The
dead-letter reader then hands the incident to a person (``on_dead_letter``), like the outbox
does locally. Message locks are renewed while the incident team works (minutes, not seconds).
Signed in with the worker's managed identity (Azure Service Bus Data Sender/Receiver roles).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import OutboxEventRecord
from backend.app.services.events import DeadLetterHandler, Subscription, consume

_log = logging.getLogger("facilityops.events")
# Longer than the incident team's time budget (240 s), with room for the plan to be stored.
LOCK_RENEWAL_SECONDS = 600


class ServiceBusEventBus:
    """The EventBus port for the outbox dispatcher: publish a committed event to the topic."""

    def __init__(self, client: Any, topic: str) -> None:
        self._client, self._topic = client, topic

    async def publish(self, event: OutboxEventRecord) -> None:
        from azure.servicebus import ServiceBusMessage

        message = ServiceBusMessage(
            json.dumps({"event_id": str(event.id)}),
            message_id=str(event.id),  # duplicate detection on the topic drops a re-publish
            subject=event.event_type,  # subscription filters match on this
            content_type="application/json",
            application_properties={"trace_id": event.trace_id or ""},
        )
        async with self._client.get_topic_sender(self._topic) as sender:
            await sender.send_messages(message)


class ServiceBusConsumers:
    """Receives each consumer's subscription and its dead-letter queue, one batch per call."""

    def __init__(
        self,
        client: Any,
        topic: str,
        session_factory: async_sessionmaker[AsyncSession],
        subscriptions: Sequence[Subscription],
        *,
        on_dead_letter: DeadLetterHandler | None = None,
        max_wait_seconds: float = 2,
        batch_size: int = 10,
    ) -> None:
        self._client, self._topic, self._session_factory = client, topic, session_factory
        self._on_dead_letter = on_dead_letter
        self._max_wait, self._batch = max_wait_seconds, batch_size
        self._by_consumer: dict[str, list[Subscription]] = defaultdict(list)
        for subscription in subscriptions:
            self._by_consumer[subscription.consumer].append(subscription)

    async def run_once(self) -> int:
        handled = 0
        for consumer, subscriptions in self._by_consumer.items():
            handled += await self._receive(consumer, subscriptions)
            handled += await self._receive_dead_letters(consumer)
        return handled

    async def _receive(self, consumer: str, subscriptions: list[Subscription]) -> int:
        from azure.servicebus.aio import AutoLockRenewer

        async with AutoLockRenewer(max_lock_renewal_duration=LOCK_RENEWAL_SECONDS) as renewer:
            async with self._client.get_subscription_receiver(
                self._topic, consumer, auto_lock_renewer=renewer, max_wait_time=self._max_wait
            ) as receiver:
                messages = await receiver.receive_messages(max_message_count=self._batch, max_wait_time=self._max_wait)
                for message in messages:
                    try:
                        event = await self._event(message)
                        for subscription in subscriptions:
                            if event is not None and subscription.event_type == event.event_type:
                                await consume(self._session_factory, subscription, event)
                    except Exception as error:  # noqa: BLE001 - Service Bus retries, then dead-letters
                        _log.warning(
                            "event consumer failed; Service Bus will redeliver",
                            extra={"step": str(message.subject), "status": "retry", "error_type": type(error).__name__},
                        )
                        await receiver.abandon_message(message)
                        continue
                    await receiver.complete_message(message)
        return len(messages)

    async def _receive_dead_letters(self, consumer: str) -> int:
        from azure.servicebus import ServiceBusSubQueue

        async with self._client.get_subscription_receiver(
            self._topic, consumer, sub_queue=ServiceBusSubQueue.DEAD_LETTER, max_wait_time=self._max_wait
        ) as receiver:
            messages = await receiver.receive_messages(max_message_count=self._batch, max_wait_time=self._max_wait)
            for message in messages:
                event_id = _event_id(message)
                async with self._session_factory() as session:
                    event = await session.get(OutboxEventRecord, event_id) if event_id else None
                    if event is not None:
                        event.dead_lettered_at = datetime.now(UTC)
                        event.last_error_type = (str(message.dead_letter_reason or "dead_lettered"))[:64]
                        _log.error("event dead-lettered", extra={"step": event.event_type, "status": "dead_letter"})
                        if self._on_dead_letter is not None:
                            await self._on_dead_letter(session, event)
                    await session.commit()
                await receiver.complete_message(message)
        return len(messages)

    async def _event(self, message: Any) -> OutboxEventRecord | None:
        event_id = _event_id(message)
        if event_id is None:
            return None
        async with self._session_factory() as session:
            return await session.get(OutboxEventRecord, event_id)


def _event_id(message: Any) -> UUID | None:
    body = message.body
    raw = body if isinstance(body, (bytes, str)) else b"".join(body)
    try:
        return UUID(json.loads(raw)["event_id"])
    except (ValueError, KeyError, TypeError):
        return None
