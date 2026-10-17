"""The decision trail: every step the intake pipeline took for one request, and what it found.

Transparency means being able to answer "why did the AI do that?" for any request, long after
traces have expired. Each step records its outcome, a reason code, the service and version
that ran it, its duration and *non-personal* details (safety severities, the classifier's
answer and confidence, policy thresholds). Request text and personal data are never put in
a step: details are counts, labels, scores and codes only.

Adapters add details to the step in progress with ``note(...)``, so the pipeline's ports stay
unchanged. The trail lives in a context variable for the duration of one request.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class StepOutcome(StrEnum):
    PASSED = "passed"  # the check ran and found nothing
    FLAGGED = "flagged"  # the check found something; the request was rerouted
    FAILED = "failed"  # the step could not run; the request failed closed
    SKIPPED = "skipped"  # not applicable (for example no attachment)
    DECIDED = "decided"  # a decision step (routing, emergency handoff, work order)


@dataclass
class DecisionStep:
    step: str
    outcome: StepOutcome
    started_at: datetime
    duration_ms: int = 0
    reason_code: str | None = None
    service: str | None = None
    version: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None


@dataclass
class DecisionTrail:
    steps: list[DecisionStep] = field(default_factory=list)

    def add(self, step: DecisionStep) -> DecisionStep:
        self.steps.append(step)
        return step

    def record(
        self,
        step: str,
        outcome: StepOutcome,
        *,
        reason_code: str | None = None,
        service: str | None = None,
        **details: Any,
    ) -> DecisionStep:
        """A step that does no I/O (emergency screen, file checks, routing)."""
        return self.add(
            DecisionStep(
                step=step,
                outcome=outcome,
                started_at=datetime.now(UTC),
                reason_code=reason_code,
                service=service,
                details=_clean(details),
                trace_id=current_trace_id(),
            )
        )

    def ordered(self) -> list[DecisionStep]:
        return sorted(self.steps, key=lambda s: s.started_at)


_trail: ContextVar[DecisionTrail | None] = ContextVar("decision_trail", default=None)
_open_step: ContextVar[DecisionStep | None] = ContextVar("decision_step", default=None)


def current_trail() -> DecisionTrail:
    """The trail of the request being processed (a detached one outside a request)."""
    return _trail.get() or DecisionTrail()


@contextmanager
def decision_trail() -> Iterator[DecisionTrail]:
    """The trail for one request; steps opened inside it are collected here."""
    trail = DecisionTrail()
    token = _trail.set(trail)
    try:
        yield trail
    finally:
        _trail.reset(token)


@contextmanager
def open_step(name: str) -> Iterator[DecisionStep]:
    """Time a step and collect what adapters ``note()`` while it runs.

    The caller sets the outcome; it defaults to PASSED, or FAILED if an exception escapes.
    """
    step = DecisionStep(
        step=name, outcome=StepOutcome.PASSED, started_at=datetime.now(UTC), trace_id=current_trace_id()
    )
    token = _open_step.set(step)
    start = time.perf_counter()
    try:
        yield step
    except BaseException:
        step.outcome = StepOutcome.FAILED
        raise
    finally:
        step.duration_ms = int((time.perf_counter() - start) * 1000)
        _open_step.reset(token)
        trail = _trail.get()
        if trail is not None:
            trail.add(step)


def note(*, service: str | None = None, version: str | None = None, **details: Any) -> None:
    """Add non-personal details to the step in progress. Never pass request text or PII."""
    step = _open_step.get()
    if step is None:
        return
    if service:
        step.service = service
    if version:
        step.version = version
    step.details.update(_clean(details))


def current_trace_id() -> str | None:
    """The OpenTelemetry trace id, so a step can be found in Application Insights."""
    from opentelemetry import trace

    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None


_MAX_DETAIL_STRING = 64


def _clean(details: dict[str, Any]) -> dict[str, Any]:
    """Keep details small and structural: long strings are a sign of text leaking in."""
    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        if value is None:
            continue
        if isinstance(value, str) and len(value) > _MAX_DETAIL_STRING:
            raise ValueError(f"Decision detail {key!r} looks like free text; record codes and counts only")
        cleaned[key] = value
    return cleaned
