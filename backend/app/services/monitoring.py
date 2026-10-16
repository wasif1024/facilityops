"""Aggregate reports for the admin console: agent health and intake outcomes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AgentRunRecord, IntakeRequestRecord, WorkOrderRecord


def window_start(days: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) - timedelta(days=days)


def _percentile(fraction: float, column):
    return func.percentile_cont(fraction).within_group(column)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


class MonitoringReports:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _agent_filter(self, since: datetime, include_evaluations: bool):
        conditions = [AgentRunRecord.started_at >= since]
        if not include_evaluations:
            conditions.append(AgentRunRecord.evaluation_run_id.is_(None))
        return conditions

    async def agent_report(self, *, days: int, include_evaluations: bool) -> dict[str, Any]:
        since = window_start(days)
        conditions = self._agent_filter(since, include_evaluations)
        failed = func.count().filter(AgentRunRecord.status == "failed")

        summary_rows = await self._session.execute(
            select(
                AgentRunRecord.agent_name,
                AgentRunRecord.model,
                func.count().label("runs"),
                failed.label("failures"),
                _percentile(0.5, AgentRunRecord.duration_ms).label("p50_ms"),
                _percentile(0.95, AgentRunRecord.duration_ms).label("p95_ms"),
                func.coalesce(func.sum(AgentRunRecord.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(AgentRunRecord.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(AgentRunRecord.total_tokens), 0).label("total_tokens"),
                func.max(AgentRunRecord.started_at).label("last_run_at"),
            )
            .where(*conditions)
            .group_by(AgentRunRecord.agent_name, AgentRunRecord.model)
            .order_by(func.count().desc())
        )
        agents = [
            {
                "agent_name": row.agent_name,
                "model": row.model,
                "runs": row.runs,
                "failures": row.failures,
                "failure_rate": _rate(row.failures, row.runs),
                "p50_ms": round(row.p50_ms) if row.p50_ms is not None else None,
                "p95_ms": round(row.p95_ms) if row.p95_ms is not None else None,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "total_tokens": int(row.total_tokens),
                "avg_tokens_per_run": round(int(row.total_tokens) / row.runs) if row.runs else None,
                "last_run_at": row.last_run_at,
            }
            for row in summary_rows
        ]

        day = func.date_trunc("day", AgentRunRecord.started_at).label("day")
        daily_rows = await self._session.execute(
            select(
                day,
                AgentRunRecord.agent_name,
                func.count().label("runs"),
                failed.label("failures"),
                _percentile(0.95, AgentRunRecord.duration_ms).label("p95_ms"),
                func.coalesce(func.sum(AgentRunRecord.total_tokens), 0).label("total_tokens"),
            )
            .where(*conditions)
            .group_by(day, AgentRunRecord.agent_name)
            .order_by(day)
        )
        daily = [
            {
                "day": row.day.date().isoformat(),
                "agent_name": row.agent_name,
                "runs": row.runs,
                "failures": row.failures,
                "p95_ms": round(row.p95_ms) if row.p95_ms is not None else None,
                "total_tokens": int(row.total_tokens),
            }
            for row in daily_rows
        ]

        error_rows = await self._session.execute(
            select(AgentRunRecord.agent_name, AgentRunRecord.error_type, func.count().label("count"))
            .where(*conditions, AgentRunRecord.status == "failed")
            .group_by(AgentRunRecord.agent_name, AgentRunRecord.error_type)
            .order_by(func.count().desc())
        )
        errors = [
            {"agent_name": row.agent_name, "error_type": row.error_type, "count": row.count}
            for row in error_rows
        ]

        return {
            "window_days": days,
            "since": since,
            "include_evaluations": include_evaluations,
            "agents": agents,
            "daily": daily,
            "errors": errors,
        }

    async def agent_runs(
        self,
        *,
        agent_name: str | None,
        status: str | None,
        evaluation_run_id: UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AgentRunRecord], int]:
        query = select(AgentRunRecord)
        if agent_name:
            query = query.where(AgentRunRecord.agent_name == agent_name)
        if status:
            query = query.where(AgentRunRecord.status == status)
        if evaluation_run_id:
            query = query.where(AgentRunRecord.evaluation_run_id == evaluation_run_id)
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self._session.scalars(
            query.order_by(AgentRunRecord.started_at.desc()).limit(limit).offset(offset)
        )
        return list(rows), int(total or 0)

    async def intake_report(self, *, days: int) -> dict[str, Any]:
        since = window_start(days)
        in_window = IntakeRequestRecord.created_at >= since

        status_rows = await self._session.execute(
            select(IntakeRequestRecord.status, func.count())
            .where(in_window)
            .group_by(IntakeRequestRecord.status)
        )
        by_status = {status: count for status, count in status_rows}
        total = sum(by_status.values())

        category_rows = await self._session.execute(
            select(IntakeRequestRecord.category, func.count())
            .where(in_window, IntakeRequestRecord.category.is_not(None))
            .group_by(IntakeRequestRecord.category)
            .order_by(func.count().desc())
        )
        reason_rows = await self._session.execute(
            select(IntakeRequestRecord.reason_code, func.count())
            .where(in_window)
            .group_by(IntakeRequestRecord.reason_code)
            .order_by(func.count().desc())
            .limit(15)
        )
        priority_rows = await self._session.execute(
            select(IntakeRequestRecord.priority, func.count())
            .where(in_window, IntakeRequestRecord.priority.is_not(None))
            .group_by(IntakeRequestRecord.priority)
            .order_by(IntakeRequestRecord.priority)
        )

        day = func.date_trunc("day", IntakeRequestRecord.created_at).label("day")
        daily_rows = await self._session.execute(
            select(day, IntakeRequestRecord.status, func.count().label("count"))
            .where(in_window)
            .group_by(day, IntakeRequestRecord.status)
            .order_by(day)
        )
        avg_confidence = await self._session.scalar(
            select(cast(func.avg(IntakeRequestRecord.confidence), Float)).where(
                in_window, IntakeRequestRecord.confidence.is_not(None)
            )
        )
        pending_approvals = await self._session.scalar(
            select(func.count())
            .select_from(WorkOrderRecord)
            .where(WorkOrderRecord.status == "pending_approval")
        )

        return {
            "window_days": days,
            "since": since,
            "total_requests": total,
            "by_status": by_status,
            "emergency_handoffs": by_status.get("emergency_handoff", 0),
            "human_review_rate": _rate(by_status.get("human_review", 0), total),
            "automation_rate": _rate(
                by_status.get("work_order_created", 0) + by_status.get("pending_approval", 0),
                total,
            ),
            "average_confidence": round(avg_confidence, 4) if avg_confidence is not None else None,
            "pending_approvals": int(pending_approvals or 0),
            "by_category": {category: count for category, count in category_rows},
            "by_priority": {str(priority): count for priority, count in priority_rows},
            "top_reason_codes": [
                {"reason_code": reason, "count": count} for reason, count in reason_rows
            ],
            "daily": [
                {"day": row.day.date().isoformat(), "status": row.status, "count": row.count}
                for row in daily_rows
            ],
        }
