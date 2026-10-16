from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from backend.app.domain.rbac import IdentityProvider
from backend.app.services.agent_monitoring import AgentRunSample, SqlAgentRunRecorder
from backend.app.services.evaluations import EvaluationStore
from backend.app.services.users import UserDirectory

from .conftest import call, scalar


async def make_user(session_factory, name: str, role: str) -> None:
    async with session_factory() as session:
        await UserDirectory(session).grant_role(
            provider=IdentityProvider.DEVELOPMENT, external_id=name, role=role
        )


def _sample(**overrides) -> AgentRunSample:
    values = dict(
        agent_name="Maintenance Triage Classifier",
        model="gpt-test",
        prompt_sha="abc123",
        status="succeeded",
        error_type=None,
        duration_ms=800,
        input_tokens=400,
        output_tokens=50,
        total_tokens=450,
        started_at=datetime.now(UTC),
        request_id=uuid4(),
        evaluation_run_id=None,
    )
    values.update(overrides)
    return AgentRunSample(**values)


@pytest.mark.asyncio
async def test_intake_records_submitter_and_protects_replays(client, session_factory):
    request_id = str(uuid4())
    body = {"requestId": request_id, "text": "Strong gas smell in the stairwell"}

    first = call(client, "IntakeService/SubmitIntake", body)
    assert first.status_code == 200, first.text
    assert first.json()["result"]["status"] == "INTAKE_STATUS_EMERGENCY_HANDOFF"

    assert call(client, "IntakeService/SubmitIntake", body).json() == first.json()
    stolen = call(client, "IntakeService/SubmitIntake", body, user="mallory")
    assert stolen.json()["code"] == "already_exists"

    submitter = await scalar(
        session_factory,
        "select u.external_id from intake_requests r join users u on u.id = r.submitted_by_user_id"
        " where r.request_id = :rid",
        rid=request_id,
    )
    assert submitter == "alice"


@pytest.mark.asyncio
async def test_intake_report_counts_outcomes(client, session_factory):
    await make_user(session_factory, "fm", "facility_manager")
    call(client, "IntakeService/SubmitIntake", {"text": "Kitchen fire!"})
    # No Azure endpoints in tests, so a routine request fails closed to human review.
    call(client, "IntakeService/SubmitIntake", {"text": "Tap drips"})

    report = call(client, "MonitoringService/GetIntakeReport", user="fm").json()

    assert report["totalRequests"] == 2
    assert report["byStatus"] == {"emergency_handoff": 1, "human_review": 1}
    assert report["humanReviewRate"] == 0.5
    assert {"reasonCode": "pii_redaction_unavailable", "count": 1} in report["topReasonCodes"]


@pytest.mark.asyncio
async def test_agent_report_separates_production_from_evaluation_traffic(client, session_factory):
    await make_user(session_factory, "fm", "facility_manager")
    async with session_factory() as session:
        eval_run = await EvaluationStore(session).start_run(
            mode="live", dataset="golden_v1.jsonl", source="cli"
        )
    recorder = SqlAgentRunRecorder(session_factory)
    for duration in (500, 700, 900):
        await recorder.record(_sample(duration_ms=duration))
    await recorder.record(
        _sample(
            status="failed",
            error_type="TimeoutError",
            total_tokens=None,
            input_tokens=None,
            output_tokens=None,
            duration_ms=30_000,
        )
    )
    await recorder.record(_sample(evaluation_run_id=eval_run.id))
    await recorder.record(_sample(started_at=datetime.now(UTC) - timedelta(days=30)))

    report = call(client, "MonitoringService/GetAgentReport", {"days": 7}, user="fm").json()
    [agent] = report["agents"]
    assert agent["runs"] == 4
    assert agent["failures"] == 1
    assert agent["failureRate"] == 0.25
    assert agent["totalTokens"] == "1350"  # int64 is a JSON string in the protobuf JSON mapping
    assert agent["p50Ms"] == 800
    assert report["errors"] == [
        {"agentName": "Maintenance Triage Classifier", "errorType": "TimeoutError", "count": 1}
    ]

    with_evals = call(
        client,
        "MonitoringService/GetAgentReport",
        {"days": 7, "includeEvaluations": True},
        user="fm",
    ).json()
    assert with_evals["agents"][0]["runs"] == 5

    failed_runs = call(
        client, "MonitoringService/ListAgentRuns", {"status": "AGENT_RUN_STATUS_FAILED"}, user="fm"
    ).json()
    assert failed_runs["total"] == 1
    assert failed_runs["runs"][0]["errorType"] == "TimeoutError"


@pytest.mark.asyncio
async def test_admin_runs_an_evaluation_and_reads_results(client, session_factory):
    await make_user(session_factory, "root", "admin")

    started = call(
        client,
        "EvaluationService/StartEvaluationRun",
        {"mode": "EVALUATION_MODE_OFFLINE"},
        user="root",
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run"]["id"]

    [run] = call(client, "EvaluationService/ListEvaluationRuns", user="root").json()["runs"]
    assert run["id"] == run_id
    assert run["status"] == "EVALUATION_STATUS_PASSED"
    assert run["source"] == "admin"
    assert run["metrics"]["emergency_recall"] == 1.0
    assert run["gates"]["emergency_recall"]["passed"] is True

    detail = call(client, "EvaluationService/GetEvaluationRun", {"runId": run_id}, user="root")
    detail = detail.json()
    assert len(detail["cases"]) == detail["run"]["caseCount"] >= 100
    assert all("text" not in case["expected"] for case in detail["cases"])
    failed = call(
        client,
        "EvaluationService/GetEvaluationRun",
        {"runId": run_id, "failedOnly": True},
        user="root",
    ).json()
    assert failed.get("cases", []) == []

    trend = call(
        client, "EvaluationService/GetMetricTrend", {"metric": "emergency_recall"}, user="root"
    ).json()
    assert [point["value"] for point in trend["points"]] == [1.0]


@pytest.mark.asyncio
async def test_only_one_evaluation_runs_at_a_time(client, session_factory):
    await make_user(session_factory, "root", "admin")
    async with session_factory() as session:
        await EvaluationStore(session).start_run(mode="live", dataset="golden_v1.jsonl", source="cli")

    response = call(client, "EvaluationService/StartEvaluationRun", user="root")
    assert response.json()["code"] == "failed_precondition"


@pytest.mark.asyncio
async def test_facility_manager_reads_but_cannot_run_evaluations(client, session_factory):
    await make_user(session_factory, "fm", "facility_manager")

    assert call(client, "EvaluationService/ListEvaluationRuns", user="fm").status_code == 200
    assert call(client, "EvaluationService/StartEvaluationRun", user="fm").status_code == 403
