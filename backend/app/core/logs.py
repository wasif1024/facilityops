"""Structured application logs, correlated with traces and the decision trail.

Every log line carries the intake ``request_id`` (when there is one) and the OpenTelemetry
``trace_id`` and ``span_id``, so one request can be followed across logs, traces in
Application Insights, and the ``decision_steps`` table. LOG_FORMAT=json writes one JSON
object per line (for Azure Monitor and log tools); "text" is easier to read locally.

Log messages never contain request text, prompts, model answers or personal data: only ids,
codes, counts and durations. Exception messages are not logged either (they can contain
request text); handlers log the exception type.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from backend.app.core.config import Settings

# Extra fields that may be attached with logger.info(..., extra={...}).
_EXTRA_FIELDS = (
    "step",
    "outcome",
    "reason_code",
    "duration_ms",
    "service",
    "status",
    "rpc",
    "agent",
    "model",
    "error_type",
)


class _CorrelationFilter(logging.Filter):
    """Adds request_id, trace_id and span_id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        from opentelemetry import trace

        from backend.app.services.agent_monitoring import current_scope

        scope = current_scope()
        record.request_id = str(scope.request_id) if scope.request_id else None
        context = trace.get_current_span().get_span_context()
        record.trace_id = f"{context.trace_id:032x}" if context.is_valid else None
        record.span_id = f"{context.span_id:016x}" if context.is_valid else None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "trace_id": getattr(record, "trace_id", None),
            "span_id": getattr(record, "span_id", None),
        }
        for field in _EXTRA_FIELDS:
            if (value := getattr(record, field, None)) is not None:
                entry[field] = value
        if record.exc_info and record.exc_info[0] is not None:
            entry["error_type"] = record.exc_info[0].__name__
        return json.dumps({k: v for k, v in entry.items() if v is not None}, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ids = " ".join(
            f"{name}={value}"
            for name in ("request_id", "trace_id")
            if (value := getattr(record, name, None))
        )
        extras = " ".join(
            f"{field}={value}" for field in _EXTRA_FIELDS if (value := getattr(record, field, None)) is not None
        )
        time = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        line = f"{time} {record.levelname:<7} {record.name}: {record.getMessage()}"
        return " ".join(part for part in (line, extras, f"[{ids}]" if ids else "") if part)


_configured = False


def configure_logging(settings: Settings) -> None:
    """Install the structured handler on the root logger, once per process."""
    global _configured
    if _configured or settings.app_env == "test":
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())
    handler.addFilter(_CorrelationFilter())
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, logging.StreamHandler)] + [handler]
    root.setLevel(settings.log_level.upper())
    # Our own modules log at the configured level; noisy libraries stay at warnings.
    for noisy in ("azure", "httpx", "httpcore", "urllib3", "opentelemetry"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
