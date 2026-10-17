"""Per-run agent monitoring as Agent Framework middleware.

Attach ``AgentMonitoringMiddleware`` to any agent (the intake classifier today, the
Magentic specialists in Phase 2) to record one row per run in ``agent_runs``: agent,
model, prompt version, duration, token usage, outcome and OpenTelemetry trace id.
Correlation ids come from ``monitoring_scope`` via a context variable, so agents do not
need to know which request or evaluation they serve.

With ``capture_content`` (CAPTURE_MODEL_CONTENT, on by default) it also stores what was sent
to the model and what it answered in ``agent_run_contents``, and the exact system
instructions behind each prompt hash in ``prompt_versions``. Models in this project only ever
receive redacted text, so this content holds no personal data; it is readable only by roles
that can read requests, and purged after MODEL_CONTENT_RETENTION_DAYS. Traces sent to
Application Insights stay content-free and link here through the trace id.
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

from agent_framework import AgentContext, AgentMiddleware, ResponseStream
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import AgentRunContentRecord, AgentRunRecord, PromptVersionRecord
from backend.app.domain.decision_trail import current_trace_id

_CONTENT_FIELDS = {"input_text", "output_text", "instructions"}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitoringScope:
    request_id: UUID | None = None
    evaluation_run_id: UUID | None = None


_scope: ContextVar[MonitoringScope] = ContextVar("agent_monitoring_scope", default=MonitoringScope())


def current_scope() -> MonitoringScope:
    """The request or evaluation the current code is serving (used to correlate logs)."""
    return _scope.get()


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
    trace_id: str | None = None
    # Captured content (redacted input, model output) and the instructions it ran with.
    input_text: str | None = None
    output_text: str | None = None
    instructions: str | None = None


class AgentRunRecorder(Protocol):
    async def record(self, sample: AgentRunSample) -> None: ...


class SqlAgentRunRecorder:
    """Writes samples in their own transaction, so they survive a failed request."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._known_prompts: set[str] = set()

    async def record(self, sample: AgentRunSample) -> None:
        try:
            async with self._session_factory() as session:
                run_id = uuid4()
                fields = {k: v for k, v in sample.__dict__.items() if k not in _CONTENT_FIELDS}
                session.add(AgentRunRecord(id=run_id, **fields))
                if sample.input_text is not None:
                    await session.flush()
                    session.add(
                        AgentRunContentRecord(
                            agent_run_id=run_id,
                            input_text=sample.input_text,
                            output_text=sample.output_text or "",
                        )
                    )
                if sample.instructions and sample.prompt_sha and sample.prompt_sha not in self._known_prompts:
                    await session.execute(
                        insert(PromptVersionRecord)
                        .values(
                            prompt_sha=sample.prompt_sha,
                            agent_name=sample.agent_name,
                            instructions=sample.instructions,
                        )
                        .on_conflict_do_nothing(index_elements=["prompt_sha"])
                    )
                await session.commit()
                if sample.prompt_sha:
                    self._known_prompts.add(sample.prompt_sha)
        except Exception:  # noqa: BLE001 - monitoring must never break the agent call
            logger.warning("Failed to record agent run for %s", sample.agent_name, exc_info=True)


class AgentMonitoringMiddleware(AgentMiddleware):
    def __init__(
        self,
        recorder: AgentRunRecorder,
        *,
        model: str | None,
        prompt_sha: str | None,
        instructions: str | None = None,
        capture_content: bool = False,
    ) -> None:
        self._recorder = recorder
        self._model = model
        self._prompt_sha = prompt_sha
        self._instructions = instructions
        self._capture_content = capture_content

    async def process(
        self, context: AgentContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        started_at = datetime.now(UTC)
        start = time.perf_counter()
        try:
            await call_next()
        except Exception as error:
            await self._record(context, None, started_at, start, "failed", type(error).__name__)
            raise
        result = context.result
        if isinstance(result, ResponseStream):
            # Streamed runs (every Magentic participant): record once the stream has finished,
            # when the full answer and its token usage are known.
            async def record_final(final: Any) -> Any:
                await self._record(context, final, started_at, start, "succeeded", None)
                return final

            context.result = result.with_result_hook(record_final)
            return
        await self._record(context, result, started_at, start, "succeeded", None)

    async def _record(
        self,
        context: AgentContext,
        result: Any,
        started_at: datetime,
        start: float,
        status: str,
        error_type: str | None,
    ) -> None:
        usage: dict[str, Any] = dict(getattr(result, "usage_details", None) or {})
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
                trace_id=current_trace_id(),
                **(self._content(context, result) if self._capture_content else {}),
            )
        )

    def _content(self, context: AgentContext, result: Any) -> dict[str, str | None]:
        """The messages sent to the model and its answer, as plain text."""
        sent = "\n\n".join(
            f"[{getattr(message, 'role', 'user')}] {getattr(message, 'text', '') or ''}"
            for message in (context.messages or [])
        )
        answer = getattr(result, "text", None) if result is not None else None
        return {"input_text": sent, "output_text": answer or "", "instructions": self._instructions}
