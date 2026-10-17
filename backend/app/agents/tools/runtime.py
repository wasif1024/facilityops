"""How every specialist tool runs: read-only, budgeted, traced and recorded (spec 5.4, 6.1).

- **Read-only by construction.** Each call runs in a ``SET TRANSACTION READ ONLY`` transaction,
  so PostgreSQL refuses any write, whatever the tool's code does.
- **Budgeted.** Each specialist gets ``max_calls_per_specialist`` calls per incident run (default
  5); beyond that the tool returns a refusal the model can read, instead of running.
- **Recorded.** Every call adds a decision-trail step: tool, specialist, outcome, duration, the
  argument names and short code values (never free text), and how many results came back.
- **Errors stay inside.** A failing tool returns ``{"error": "<type>"}`` to the model; the
  exception message (which could echo text) is never returned or recorded.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from opentelemetry import trace
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.domain.decision_trail import DecisionStep, StepOutcome, current_trace_id

DEFAULT_MAX_CALLS = 5
_tracer = trace.get_tracer("facilityops.tools")
_SAFE_ARG = 48

ToolFunction = Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class ToolContext:
    """One incident run: who is calling, the budget left, and the steps recorded so far."""

    session_factory: async_sessionmaker[AsyncSession]
    request_id: UUID
    max_calls_per_specialist: int = DEFAULT_MAX_CALLS
    # The knowledge base search (KnowledgeSearch); None when knowledge isn't configured.
    knowledge: Any = None
    calls: dict[str, int] = field(default_factory=dict)
    steps: list[DecisionStep] = field(default_factory=list)
    # Every citation id a tool actually returned in this run: the plan may cite only these.
    returned_sources: set[str] = field(default_factory=set)
    # Evaluation only (evals/incidents/live.py): full arguments and results, kept in memory for the
    # groundedness and tool-call evaluators. Never set in production, where results aren't stored.
    transcript: list[dict[str, Any]] | None = None

    def remaining(self, specialist: str) -> int:
        return self.max_calls_per_specialist - self.calls.get(specialist, 0)


def _safe_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Argument values short enough to be codes; anything longer is recorded as its length only."""
    safe: dict[str, Any] = {}
    for key, value in kwargs.items():
        if isinstance(value, (int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, str) and len(value) <= _SAFE_ARG:
            safe[key] = value
        else:
            safe[key] = f"<{len(str(value))} chars>"
    return safe


async def run_tool(
    ctx: ToolContext,
    specialist: str,
    name: str,
    function: ToolFunction,
    *,
    uses_knowledge: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    start = time.perf_counter()
    if ctx.remaining(specialist) <= 0:
        result: dict[str, Any] = {
            "error": "tool_budget_exhausted",
            "detail": f"{specialist} has used its {ctx.max_calls_per_specialist} tool calls for this incident",
        }
        _record(ctx, specialist, name, kwargs, StepOutcome.FLAGGED, "tool_budget_exhausted", started, start, None)
        _transcribe(ctx, specialist, name, kwargs, result)
        return result

    ctx.calls[specialist] = ctx.calls.get(specialist, 0) + 1
    with _tracer.start_as_current_span(f"tool {name}", record_exception=False, set_status_on_exception=False) as span:
        span.set_attribute("facilityops.specialist", specialist)
        try:
            if uses_knowledge:
                if ctx.knowledge is None:
                    raise RuntimeError("knowledge base not configured")
                result = await function(ctx.knowledge, **kwargs)
            else:
                async with ctx.session_factory() as session:
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                    result = await function(session, **kwargs)
                    await session.rollback()
        except Exception as error:  # noqa: BLE001 - the model gets the type only
            span.set_status(trace.Status(trace.StatusCode.ERROR, type(error).__name__))
            _record(ctx, specialist, name, kwargs, StepOutcome.FAILED, None, started, start, None, type(error).__name__)
            _transcribe(ctx, specialist, name, kwargs, {"error": type(error).__name__})
            return {"error": type(error).__name__}
    _collect_sources(result, ctx.returned_sources)
    _record(ctx, specialist, name, kwargs, StepOutcome.PASSED, None, started, start, _count(result))
    _transcribe(ctx, specialist, name, kwargs, result)
    return result


def _transcribe(ctx: ToolContext, specialist: str, name: str, kwargs: dict[str, Any], result: dict[str, Any]) -> None:
    if ctx.transcript is not None:
        ctx.transcript.append({"specialist": specialist, "name": name, "arguments": dict(kwargs), "result": result})


def _collect_sources(value: Any, into: set[str]) -> None:
    if isinstance(value, dict):
        citation = value.get("citation")
        if isinstance(citation, dict) and isinstance(citation.get("source_id"), str):
            into.add(citation["source_id"])
        for item in value.values():
            _collect_sources(item, into)
    elif isinstance(value, list):
        for item in value:
            _collect_sources(item, into)


def _count(result: dict[str, Any]) -> int:
    lists = [v for v in result.values() if isinstance(v, list)]
    return len(lists[0]) if lists else (0 if result.get("found") is False else 1)


def _record(
    ctx: ToolContext,
    specialist: str,
    name: str,
    kwargs: dict[str, Any],
    outcome: StepOutcome,
    reason_code: str | None,
    started: datetime,
    start: float,
    results: int | None,
    error: str | None = None,
) -> None:
    details: dict[str, Any] = {"specialist": specialist, "arguments": _safe_args(kwargs)}
    if results is not None:
        details["results"] = results
    if error:
        details["error"] = error
    ctx.steps.append(
        DecisionStep(
            step=f"tool.{name}",
            outcome=outcome,
            started_at=started,
            duration_ms=int((time.perf_counter() - start) * 1000),
            reason_code=reason_code,
            service="incident team tool",
            details=details,
            trace_id=current_trace_id(),
        )
    )
