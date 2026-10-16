"""Persist evaluation runs so the admin console can show history, drill-downs and trends."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import EvaluationCaseResultRecord, EvaluationRunRecord

logger = logging.getLogger(__name__)

# A run still "running" after this long is assumed dead and no longer blocks new runs.
_STALE_RUN_AFTER = timedelta(minutes=30)
LIVE_MIN_CATEGORY_ACCURACY = 0.85


class EvaluationStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start_run(
        self,
        *,
        mode: str,
        dataset: str,
        source: str,
        triggered_by: UUID | None = None,
    ) -> EvaluationRunRecord:
        run = EvaluationRunRecord(
            id=uuid4(),
            mode=mode,
            status="running",
            source=source,
            dataset=dataset,
            git_sha=os.environ.get("GITHUB_SHA"),
            triggered_by_user_id=triggered_by,
            metrics={},
            gates={},
            failures={},
        )
        self._session.add(run)
        await self._session.commit()
        return run

    async def complete_run(self, run_id: UUID, report: Any) -> EvaluationRunRecord:
        run = await self._require(run_id)
        run.status = "passed" if report.passed else "failed"
        run.metrics = report.metrics
        run.gates = report.gates
        run.failures = {name: ids for name, ids in report.failures.items() if ids}
        run.dataset_sha = report.metadata.get("dataset_sha")
        run.model = report.metadata.get("model")
        run.prompt_sha = report.metadata.get("prompt_sha")
        run.case_count = report.metadata.get("cases")
        run.completed_at = datetime.now(UTC)
        if report.case_results:
            await self._session.execute(
                insert(EvaluationCaseResultRecord).values(
                    [{"run_id": run_id, **case} for case in report.case_results]
                )
            )
        await self._session.commit()
        return run

    async def fail_run(self, run_id: UUID, error_type: str) -> None:
        run = await self._require(run_id)
        run.status = "error"
        run.error_type = error_type[:64]
        run.completed_at = datetime.now(UTC)
        await self._session.commit()

    async def has_running_run(self) -> bool:
        cutoff = datetime.now(UTC) - _STALE_RUN_AFTER
        count = await self._session.scalar(
            select(func.count())
            .select_from(EvaluationRunRecord)
            .where(EvaluationRunRecord.status == "running", EvaluationRunRecord.started_at > cutoff)
        )
        return bool(count)

    async def list_runs(
        self, *, mode: str | None, status: str | None, limit: int, offset: int
    ) -> tuple[list[EvaluationRunRecord], int]:
        query = select(EvaluationRunRecord)
        if mode:
            query = query.where(EvaluationRunRecord.mode == mode)
        if status:
            query = query.where(EvaluationRunRecord.status == status)
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        runs = await self._session.scalars(
            query.order_by(EvaluationRunRecord.started_at.desc()).limit(limit).offset(offset)
        )
        return list(runs), int(total or 0)

    async def get_run(
        self, run_id: UUID, *, failed_only: bool = False
    ) -> tuple[EvaluationRunRecord, list[EvaluationCaseResultRecord]]:
        run = await self._require(run_id)
        query = select(EvaluationCaseResultRecord).where(EvaluationCaseResultRecord.run_id == run_id)
        if failed_only:
            query = query.where(EvaluationCaseResultRecord.passed.is_(False))
        cases = await self._session.scalars(query.order_by(EvaluationCaseResultRecord.case_id))
        return run, list(cases)

    async def trend(self, *, metric: str, mode: str | None, limit: int) -> list[dict[str, Any]]:
        value = EvaluationRunRecord.metrics[metric].as_float()
        query = select(
            EvaluationRunRecord.id,
            EvaluationRunRecord.started_at,
            EvaluationRunRecord.mode,
            EvaluationRunRecord.model,
            EvaluationRunRecord.prompt_sha,
            EvaluationRunRecord.dataset_sha,
            value.label("value"),
        ).where(
            EvaluationRunRecord.status.in_(["passed", "failed"]),
            EvaluationRunRecord.metrics.has_key(metric),
        )
        if mode:
            query = query.where(EvaluationRunRecord.mode == mode)
        rows = await self._session.execute(
            query.order_by(EvaluationRunRecord.started_at.desc()).limit(limit)
        )
        return [dict(row._mapping) for row in reversed(rows.all())]

    async def _require(self, run_id: UUID) -> EvaluationRunRecord:
        run = await self._session.get(EvaluationRunRecord, run_id)
        if run is None:
            raise LookupError("Evaluation run not found")
        return run


def eval_arguments(mode: str) -> argparse.Namespace:
    from evals.run_eval import parse_args

    gates = ["--min-category-accuracy", str(LIVE_MIN_CATEGORY_ACCURACY)] if mode == "live" else []
    return parse_args(["--mode", mode, *gates])


async def run_and_store(
    args: argparse.Namespace,
    *,
    source: str,
    triggered_by: UUID | None = None,
    run_id: UUID | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
):
    """Run an evaluation and persist it. Agent calls are linked to the run for cost tracking."""
    from evals.run_eval import evaluate

    from backend.app.services.agent_monitoring import SqlAgentRunRecorder, monitoring_scope

    if session_factory is None:
        from backend.app.db.session import SessionLocal

        session_factory = SessionLocal

    if run_id is None:
        async with session_factory() as session:
            run = await EvaluationStore(session).start_run(
                mode=args.mode, dataset=args.dataset.name, source=source, triggered_by=triggered_by
            )
            run_id = run.id

    try:
        with monitoring_scope(evaluation_run_id=run_id):
            report = await evaluate(args, recorder=SqlAgentRunRecorder(session_factory))
    except Exception as error:
        async with session_factory() as session:
            await EvaluationStore(session).fail_run(run_id, type(error).__name__)
        raise

    async with session_factory() as session:
        await EvaluationStore(session).complete_run(run_id, report)
    report.metadata["evaluation_run_id"] = str(run_id)
    return report


async def execute_admin_run(
    run_id: UUID,
    mode: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Background task for runs started from the admin console. Errors are stored on the run."""
    try:
        await run_and_store(
            eval_arguments(mode), source="admin", run_id=run_id, session_factory=session_factory
        )
    except Exception:  # noqa: BLE001 - already recorded as status=error
        logger.exception("Admin-triggered evaluation run %s failed", run_id)
