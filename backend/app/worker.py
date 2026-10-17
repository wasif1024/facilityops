"""The incident worker: delivers outbox events to their consumers, off the request path.

    python -m backend.app.worker            # run until Ctrl+C (scripts/dev.sh starts it for you)
    python -m backend.app.worker --once     # deliver what is pending, then exit

Locally the event bus is PostgreSQL itself (the outbox table); in Azure an Azure Service Bus
topic takes its place (spec 6.2). Several workers can run side by side: each event is claimed
by exactly one of them.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.core.logs import configure_logging
from backend.app.core.telemetry import configure_telemetry
from backend.app.services.incident_worker import build_dispatcher

_log = logging.getLogger("facilityops.worker")


async def run(*, once: bool) -> None:
    settings = get_settings()
    configure_logging(settings)
    configure_telemetry(settings)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    dispatcher = build_dispatcher(async_sessionmaker(engine, expire_on_commit=False))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    _log.info("incident worker started")
    try:
        while not stop.is_set():
            try:
                handled = await dispatcher.run_once()
            except Exception as error:  # noqa: BLE001 - keep the worker alive; log the type only
                _log.error("delivery pass failed", extra={"error_type": type(error).__name__})
                handled = 0
            if once and handled == 0:
                break
            if handled == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        await engine.dispose()
        _log.info("incident worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m backend.app.worker", description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="Deliver pending events, then exit")
    asyncio.run(run(once=parser.parse_args().once))


if __name__ == "__main__":
    main()
