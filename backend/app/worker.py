"""The incident worker: delivers outbox events to their consumers, off the request path.

    python -m backend.app.worker            # run until Ctrl+C (scripts/dev.sh starts it for you)
    python -m backend.app.worker --once     # deliver what is pending, then exit

Locally the event bus is PostgreSQL itself (the outbox table). With SERVICE_BUS_NAMESPACE set (in
Azure), the dispatcher publishes the outbox to a Service Bus topic and this worker receives each
consumer's subscription (spec 6.2). Several workers can run side by side either way: each event
is claimed, or each message locked, by exactly one of them.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.core.config import get_settings
from backend.app.core.logs import configure_logging
from backend.app.core.telemetry import configure_telemetry
from backend.app.db.engine import create_engine
from backend.app.services.events import OutboxDispatcher
from backend.app.services.incident_worker import all_subscriptions, build_dispatcher, on_dead_letter

_log = logging.getLogger("facilityops.worker")


def incident_team(settings, session_factory):
    """The Magentic team when it is enabled and Foundry is configured; otherwise None, so the worker
    falls back to UnavailableIncidentTeam and incidents go to a person (fail closed)."""
    if not (settings.incident_team_enabled and settings.foundry_endpoint):
        _log.warning("incident team disabled or Foundry not configured: incidents go to a person")
        return None
    from backend.app.agents.knowledge import azure_knowledge
    from backend.app.agents.team import MagenticIncidentTeam, foundry_clients
    from backend.app.services.agent_monitoring import SqlAgentRunRecorder

    return MagenticIncidentTeam(
        settings=settings,
        session_factory=session_factory,
        client_for=foundry_clients(settings),
        recorder=SqlAgentRunRecorder(session_factory),
        knowledge=azure_knowledge(settings) if settings.azure_search_endpoint else None,
    )


async def run(*, once: bool) -> None:
    settings = get_settings()
    configure_logging(settings)
    configure_telemetry(settings)
    engine = create_engine(settings)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    team = incident_team(settings, session_factory)
    service_bus = None
    if settings.service_bus_namespace:
        from azure.identity.aio import DefaultAzureCredential
        from azure.servicebus.aio import ServiceBusClient

        from backend.app.services.service_bus import ServiceBusConsumers, ServiceBusEventBus

        service_bus = ServiceBusClient(settings.service_bus_namespace, DefaultAzureCredential())
        topic = settings.service_bus_topic
        passes = [
            OutboxDispatcher(session_factory, ServiceBusEventBus(service_bus, topic), on_dead_letter=on_dead_letter),
            ServiceBusConsumers(service_bus, topic, session_factory, all_subscriptions(team), on_dead_letter=on_dead_letter),
        ]
        _log.info("events via Azure Service Bus", extra={"step": topic})
    else:
        passes = [build_dispatcher(session_factory, team)]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    _log.info("incident worker started")
    try:
        while not stop.is_set():
            handled = 0
            for delivery in passes:
                try:
                    handled += await delivery.run_once()
                except Exception as error:  # noqa: BLE001 - keep the worker alive; log the type only
                    _log.error("delivery pass failed", extra={"error_type": type(error).__name__})
            if once and handled == 0:
                break
            if handled == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        if service_bus is not None:
            await service_bus.close()
        await engine.dispose()
        _log.info("incident worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m backend.app.worker", description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="Deliver pending events, then exit")
    asyncio.run(run(once=parser.parse_args().once))


if __name__ == "__main__":
    main()
