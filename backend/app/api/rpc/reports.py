"""Evaluation and monitoring RPCs for the admin console."""

from __future__ import annotations

from typing import Any

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from backend.app.api.auth import current_principal
from backend.app.api.container import Container
from backend.app.api.rpc.convert import (
    from_proto_enum,
    page,
    parse_uuid,
    text,
    timestamp,
    to_proto_enum,
    window_days,
)
from backend.app.db.models import EvaluationRunRecord
from backend.app.gen.facilityops.v1 import evaluations_pb as epb
from backend.app.gen.facilityops.v1 import monitoring_pb as mpb
from backend.app.services.evaluations import EvaluationStore
from backend.app.services.monitoring import MonitoringReports

_METRIC_PATTERN_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def to_proto_run(run: EvaluationRunRecord) -> epb.EvaluationRun:
    return epb.EvaluationRun(
        id=str(run.id),
        mode=to_proto_enum(epb.EvaluationMode, run.mode),
        status=to_proto_enum(epb.EvaluationStatus, run.status),
        source=run.source,
        dataset=run.dataset,
        dataset_sha=text(run.dataset_sha),
        model=text(run.model),
        prompt_sha=text(run.prompt_sha),
        git_sha=text(run.git_sha),
        case_count=run.case_count or 0,
        metrics={
            name: float(value)
            for name, value in (run.metrics or {}).items()
            if isinstance(value, (int, float))
        },
        gates={
            name: epb.Gate(
                threshold=float(gate["threshold"]),
                value=gate.get("value"),
                passed=bool(gate["passed"]),
            )
            for name, gate in (run.gates or {}).items()
        },
        error_type=text(run.error_type),
        triggered_by_user_id=text(run.triggered_by_user_id),
        started_at=timestamp(run.started_at),
        completed_at=timestamp(run.completed_at),
    )


def _mode(member: epb.EvaluationMode) -> str | None:
    return from_proto_enum(member)


class EvaluationRpc:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def list_evaluation_runs(
        self, request: epb.ListEvaluationRunsRequest, ctx: RequestContext
    ) -> epb.ListEvaluationRunsResponse:
        limit, offset = page(request.limit, request.offset, default=20, maximum=100)
        async with self._c.session_factory() as session:
            runs, total = await EvaluationStore(session).list_runs(
                mode=_mode(request.mode),
                status=from_proto_enum(request.status),
                limit=limit,
                offset=offset,
            )
        return epb.ListEvaluationRunsResponse(runs=[to_proto_run(r) for r in runs], total=total)

    async def get_evaluation_run(
        self, request: epb.GetEvaluationRunRequest, ctx: RequestContext
    ) -> epb.GetEvaluationRunResponse:
        run_id = parse_uuid(request.run_id, "run_id")
        async with self._c.session_factory() as session:
            try:
                run, cases = await EvaluationStore(session).get_run(
                    run_id, failed_only=request.failed_only
                )
            except LookupError as error:
                raise ConnectError(Code.NOT_FOUND, str(error)) from error
        return epb.GetEvaluationRunResponse(
            run=to_proto_run(run),
            cases=[
                epb.EvaluationCase(
                    case_id=case.case_id,
                    tags=list(case.tags or []),
                    expected=epb.CaseExpectation(
                        emergency=bool(case.expected.get("emergency")),
                        emergency_type=text(case.expected.get("emergency_type")),
                        category=text(case.expected.get("category")),
                        priority=case.expected.get("priority"),
                        human_review=bool(case.expected.get("human_review")),
                        fairness_group=text(case.expected.get("fairness_group")),
                    ),
                    predicted=epb.CasePrediction(
                        emergency=text(case.predicted.get("emergency")),
                        category=text(case.predicted.get("category")),
                        priority=case.predicted.get("priority"),
                        confidence=case.predicted.get("confidence"),
                        requires_human_review=case.predicted.get("requires_human_review"),
                        error=text(case.predicted.get("error")),
                    ),
                    failures=list(case.failures or []),
                    passed=case.passed,
                )
                for case in cases
            ],
            failures={
                name: epb.CaseIdList(ids=list(ids)) for name, ids in (run.failures or {}).items()
            },
        )

    async def start_evaluation_run(
        self, request: epb.StartEvaluationRunRequest, ctx: RequestContext
    ) -> epb.StartEvaluationRunResponse:
        mode = _mode(request.mode) or "offline"
        async with self._c.session_factory() as session:
            store = EvaluationStore(session)
            if await store.has_running_run():
                raise ConnectError(
                    Code.FAILED_PRECONDITION, "An evaluation run is already in progress"
                )
            run = await store.start_run(
                mode=mode,
                dataset="golden_v1.jsonl",
                source="admin",
                triggered_by=current_principal().user_id,
            )
        await self._c.launch_evaluation(run.id, mode)
        return epb.StartEvaluationRunResponse(run=to_proto_run(run))

    async def get_metric_trend(
        self, request: epb.GetMetricTrendRequest, ctx: RequestContext
    ) -> epb.GetMetricTrendResponse:
        metric = request.metric or "emergency_recall"
        if len(metric) > 64 or not set(metric) <= _METRIC_PATTERN_CHARS:
            raise ConnectError(Code.INVALID_ARGUMENT, "metric must be lowercase snake_case")
        limit, _ = page(request.limit, 0, default=30, maximum=200)
        async with self._c.session_factory() as session:
            rows = await EvaluationStore(session).trend(
                metric=metric, mode=_mode(request.mode), limit=limit
            )
        return epb.GetMetricTrendResponse(
            points=[
                epb.TrendPoint(
                    run_id=str(row["id"]),
                    started_at=timestamp(row["started_at"]),
                    mode=to_proto_enum(epb.EvaluationMode, row["mode"]),
                    model=text(row["model"]),
                    prompt_sha=text(row["prompt_sha"]),
                    dataset_sha=text(row["dataset_sha"]),
                    value=row["value"],
                )
                for row in rows
            ]
        )


class MonitoringRpc:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def get_agent_report(
        self, request: mpb.GetAgentReportRequest, ctx: RequestContext
    ) -> mpb.GetAgentReportResponse:
        days = window_days(request.days)
        async with self._c.session_factory() as session:
            report: dict[str, Any] = await MonitoringReports(session).agent_report(
                days=days, include_evaluations=request.include_evaluations
            )
        return mpb.GetAgentReportResponse(
            window_days=days,
            since=timestamp(report["since"]),
            agents=[
                mpb.AgentSummary(
                    **{k: v for k, v in agent.items() if k not in {"last_run_at", "model"}},
                    model=text(agent["model"]),
                    last_run_at=timestamp(agent["last_run_at"]),
                )
                for agent in report["agents"]
            ],
            daily=[mpb.AgentDailyPoint(**point) for point in report["daily"]],
            errors=[
                mpb.AgentErrorCount(
                    agent_name=e["agent_name"], error_type=text(e["error_type"]), count=e["count"]
                )
                for e in report["errors"]
            ],
        )

    async def list_agent_runs(
        self, request: mpb.ListAgentRunsRequest, ctx: RequestContext
    ) -> mpb.ListAgentRunsResponse:
        limit, offset = page(request.limit, request.offset, default=50, maximum=100)
        async with self._c.session_factory() as session:
            runs, total = await MonitoringReports(session).agent_runs(
                agent_name=request.agent_name[:64] or None,
                status=from_proto_enum(request.status),
                evaluation_run_id=parse_uuid(
                    request.evaluation_run_id, "evaluation_run_id", required=False
                ),
                limit=limit,
                offset=offset,
            )
        return mpb.ListAgentRunsResponse(
            runs=[
                mpb.AgentRun(
                    id=str(run.id),
                    agent_name=run.agent_name,
                    model=text(run.model),
                    prompt_sha=text(run.prompt_sha),
                    request_id=text(run.request_id),
                    evaluation_run_id=text(run.evaluation_run_id),
                    status=to_proto_enum(mpb.AgentRunStatus, run.status),
                    error_type=text(run.error_type),
                    duration_ms=run.duration_ms,
                    input_tokens=run.input_tokens,
                    output_tokens=run.output_tokens,
                    total_tokens=run.total_tokens,
                    started_at=timestamp(run.started_at),
                )
                for run in runs
            ],
            total=total,
        )

    async def get_intake_report(
        self, request: mpb.GetIntakeReportRequest, ctx: RequestContext
    ) -> mpb.GetIntakeReportResponse:
        days = window_days(request.days)
        async with self._c.session_factory() as session:
            report = await MonitoringReports(session).intake_report(days=days)
        return mpb.GetIntakeReportResponse(
            window_days=days,
            since=timestamp(report["since"]),
            total_requests=report["total_requests"],
            by_status=report["by_status"],
            emergency_handoffs=report["emergency_handoffs"],
            human_review_rate=report["human_review_rate"],
            automation_rate=report["automation_rate"],
            average_confidence=report["average_confidence"],
            pending_approvals=report["pending_approvals"],
            by_category=report["by_category"],
            by_priority=report["by_priority"],
            top_reason_codes=[
                mpb.ReasonCount(reason_code=text(r["reason_code"]), count=r["count"])
                for r in report["top_reason_codes"]
            ],
            daily=[mpb.IntakeDailyPoint(**point) for point in report["daily"]],
        )
