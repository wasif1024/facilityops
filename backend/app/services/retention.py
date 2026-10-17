"""Retention: delete transparency records once they are past their retention period.

- Model inputs and answers (agent_run_contents): MODEL_CONTENT_RETENTION_DAYS (default 90).
- Decision trail and audit events: AUDIT_RETENTION_DAYS (default 730, two years).

The audit tables are append-only (database triggers). This purge is the only path allowed to
delete from them: it sets ``facilityops.retention_purge`` for its own transaction, and only
rows older than the retention period are deleted.

    scripts/db.sh purge-expired
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings

_AUDIT_TABLES = ("decision_steps", "intake_audit_events", "security_audit_events")


@dataclass(frozen=True)
class PurgeReport:
    deleted: dict[str, int]


async def purge_expired(session: AsyncSession, settings: Settings, *, now: datetime | None = None) -> PurgeReport:
    now = now or datetime.now(UTC)
    content_cutoff = now - timedelta(days=settings.model_content_retention_days)
    audit_cutoff = now - timedelta(days=settings.audit_retention_days)
    deleted: dict[str, int] = {}

    await session.execute(text("SET LOCAL facilityops.retention_purge = 'on'"))
    result = await session.execute(
        text("DELETE FROM agent_run_contents WHERE created_at < :cutoff"), {"cutoff": content_cutoff}
    )
    deleted["agent_run_contents"] = result.rowcount or 0
    for table in _AUDIT_TABLES:
        result = await session.execute(
            text(f"DELETE FROM {table} WHERE created_at < :cutoff"), {"cutoff": audit_cutoff}  # noqa: S608
        )
        deleted[table] = result.rowcount or 0
    await session.commit()
    return PurgeReport(deleted)
