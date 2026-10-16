"""Per-run agent monitoring as Agent Framework middleware.

Attach ``AgentMonitoringMiddleware`` to any agent (the intake classifier today, the
Magentic specialists in Phase 2) to record one row per run in ``agent_runs``: agent,
model, prompt version, duration, token usage and outcome. Prompts and responses are
never recorded. Correlation ids come from ``monitoring_scope`` via a context variable,
so agents do not need to know which request or evaluation they serve.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from agent_framework import AgentContext, AgentMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import AgentRunRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitoringScope:
    request_id: UUID | None = None
    evaluation_run_id: UUID | None = None


_scope: ContextVar[MonitoringScope] = ContextVar("agent_monitoring_scope", default=MonitoringScope())


@contextmanager
def monitoring_scope(
    *, request_id: UUID | None = None, evaluation_run_id: UUID | None = None
) -> Iterator[None]:
    token = _scope.set(MonitoringScope(request_id=request_id, evaluation_run_id=evaluation_run_id))
    try:
        yield
    finally:
        _scope.reset(token)


@dataclass(frozen=True)
class AgentRunSample:
    agent_name: str
    model: str | None
    prompt_sha: str | None
    status: str
    error_type: str | None
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    started_at: datetime
    request_id: UUID | None
    evaluation_run_id: UUID | None


class AgentRunRecorder(Protocol):
    async def record(self, sample: AgentRunSample) -> None: ...


class SqlAgentRunRecorder:
    """Writes samples in their own transaction, so they survive a failed request."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, sample: AgentRunSample) -> None:
        try:
            async with self._session_factory() as session:
                session.add(AgentRunRecord(id=uuid4(), **sample.__dict__))
                await session.commit()
        except Exception:  # noqa: BLE001 - monitoring must never break the agent call
            logger.warning("Failed to record agent run for %s", sample.agent_name, exc_info=True)


class AgentMonitoringMiddleware(AgentMiddleware):
    def __init__(
        self, recorder: AgentRunRecorder, *, model: str | None, prompt_sha: str | None
    ) -> None:
        self._recorder = recorder
        self._model = model
        self._prompt_sha = prompt_sha

    async def process(
        self, context: AgentContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        started_at = datetime.now(UTC)
        start = time.perf_counter()
        try:
            await call_next()
        except Exception as error:
            await self._record(context, started_at, start, "failed", type(error).__name__)
            raise
        await self._record(context, started_at, start, "succeeded", None)

    async def _record(
        self,
        context: AgentContext,
        started_at: datetime,
        start: float,
        status: str,
        error_type: str | None,
    ) -> None:
        usage: dict[str, Any] = dict(getattr(context.result, "usage_details", None) or {})
        scope = _scope.get()
        await self._recorder.record(
            AgentRunSample(
                agent_name=(getattr(context.agent, "name", None) or "unknown")[:64],
                model=self._model,
                prompt_sha=self._prompt_sha,
                status=status,
                error_type=error_type[:64] if error_type else None,
                duration_ms=int((time.perf_counter() - start) * 1000),
                input_tokens=usage.get("input_token_count"),
                output_tokens=usage.get("output_token_count"),
                total_tokens=usage.get("total_token_count"),
                started_at=started_at,
                request_id=scope.request_id,
                evaluation_run_id=scope.evaluation_run_id,
            )
        )
