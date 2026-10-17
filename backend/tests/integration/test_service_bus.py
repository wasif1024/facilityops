"""The Service Bus transport (Phase 3) against an in-memory fake with Service Bus's delivery rules."""

import pytest
from sqlalchemy import text

from backend.app.services.events import OutboxDispatcher
from backend.app.services.incident_worker import all_subscriptions, on_dead_letter
from backend.app.services.service_bus import ServiceBusConsumers, ServiceBusEventBus

from .conftest import scalar
from .test_incidents import escalated_request

MAX_DELIVERY_COUNT = 3


class FakeMessage:
    def __init__(self, body: str, subject: str, message_id: str) -> None:
        self.body, self.subject, self.message_id = body.encode(), subject, message_id
        self.delivery_count, self.dead_letter_reason = 0, None


class FakeServiceBus:
    """A topic fanned out to one subscription per consumer, with abandon -> redeliver -> dead letter."""

    # Mirrors the subscription filters in infra/modules/service-bus.bicep.
    FILTERS = {
        "incident-team": {"intake.escalated", "incident.plan_decided"},
        "action-executor": {"incident.plan_decided"},
    }

    def __init__(self) -> None:
        self.active = {name: [] for name in self.FILTERS}
        self.dead = {name: [] for name in self.FILTERS}
        self.seen_ids: set[str] = set()

    def get_topic_sender(self, topic):
        bus = self

        class Sender:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def send_messages(self, message):
                if message.message_id in bus.seen_ids:  # duplicate detection
                    return
                bus.seen_ids.add(message.message_id)
                body = next(message.body) if not isinstance(message.body, (bytes, str)) else message.body
                for name, types in bus.FILTERS.items():
                    if message.subject in types:
                        bus.active[name].append(FakeMessage(body.decode() if isinstance(body, bytes) else body, message.subject, message.message_id))

        return Sender()

    def get_subscription_receiver(self, topic, name, sub_queue=None, **kwargs):
        bus = self
        queue = bus.dead[name] if sub_queue else bus.active[name]

        class Receiver:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def receive_messages(self, max_message_count=1, max_wait_time=None):
                taken = queue[:max_message_count]
                for message in taken:
                    message.delivery_count += 1
                return taken

            async def complete_message(self, message):
                queue.remove(message)

            async def abandon_message(self, message):
                if message.delivery_count >= MAX_DELIVERY_COUNT:
                    queue.remove(message)
                    message.dead_letter_reason = "MaxDeliveryCountExceeded"
                    bus.dead[name].append(message)

        return Receiver()


class BrokenTeam:
    async def investigate(self, context):
        raise ConnectionError("model unavailable")


def transport(session_factory, team=None, bus=None):
    bus = bus or FakeServiceBus()
    publisher = OutboxDispatcher(session_factory, ServiceBusEventBus(bus, "facilityops-events"), on_dead_letter=on_dead_letter)
    consumers = ServiceBusConsumers(bus, "facilityops-events", session_factory, all_subscriptions(team), on_dead_letter=on_dead_letter)
    return bus, publisher, consumers


@pytest.fixture(autouse=True)
def _no_lock_renewer(monkeypatch):
    # The fake has no locks to renew.
    import azure.servicebus.aio as aio

    class NoRenewer:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(aio, "AutoLockRenewer", NoRenewer)


@pytest.mark.asyncio
async def test_events_go_through_the_topic_to_each_consumer_once(client, session_factory):
    request_id = await escalated_request(session_factory)
    bus, publisher, consumers = transport(session_factory)

    assert await publisher.run_once() == 1
    assert len(bus.active["incident-team"]) == 1 and bus.active["action-executor"] == []
    await consumers.run_once()
    status = await scalar(session_factory, "SELECT status FROM incidents WHERE request_id = :r", r=request_id)
    assert status == "needs_review"  # no team configured: fails closed to a person, as locally
    assert bus.active["incident-team"] == []

    # Re-publishing the same event is dropped by duplicate detection; nothing runs twice.
    async with session_factory() as session:
        await session.execute(text("UPDATE outbox_events SET published_at = NULL"))
        await session.commit()
    await publisher.run_once()
    assert bus.active["incident-team"] == []
    assert await scalar(session_factory, "SELECT count(*) FROM processed_events") == 1


@pytest.mark.asyncio
async def test_a_failing_consumer_is_redelivered_then_dead_lettered_to_a_person(client, session_factory):
    request_id = await escalated_request(session_factory)
    bus, publisher, consumers = transport(session_factory, team=BrokenTeam())
    await publisher.run_once()

    for _ in range(MAX_DELIVERY_COUNT):
        await consumers.run_once()
    # After the third failure the message is in the dead-letter queue; the next pass reads it.
    await consumers.run_once()

    incident = await scalar(
        session_factory, "SELECT status || ':' || reason_code FROM incidents WHERE request_id = :r", r=request_id
    )
    assert incident == "needs_review:event_delivery_failed"
    dead = await scalar(session_factory, "SELECT last_error_type FROM outbox_events WHERE dead_lettered_at IS NOT NULL")
    assert dead == "MaxDeliveryCountExceeded"
    assert bus.dead["incident-team"] == []
