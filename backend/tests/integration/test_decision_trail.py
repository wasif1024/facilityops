"""Transparency: decision trail through the API, gated model content, append-only audit, retention."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app.core.config import Settings
from backend.app.services.agent_monitoring import AgentRunSample, SqlAgentRunRecorder
from backend.app.services.retention import purge_expired

from .conftest import call, scalar
from .test_operations import make_user


def submit(client) -> str:
    # No Azure in tests: the request fails closed at redaction, which the trail must show.
    return call(client, "IntakeService/SubmitIntake", {"text": "Tap drips"}).json()["result"]["requestId"]


@pytest.mark.asyncio
async def test_trail_shows_each_step_and_is_staff_only(client, session_factory):
    await make_user(session_factory, "fm", "facility_manager")
    request_id = submit(client)

    trail = call(client, "IntakeService/GetDecisionTrail", {"requestId": request_id}, user="fm").json()
    steps = [(s["step"], s["outcome"]) for s in trail["steps"]]
    assert steps == [
        ("intake.emergency_screen", "passed"),
        ("intake.pii_redaction", "failed"),
        ("intake.outcome", "decided"),
    ]
    assert trail["steps"][2]["reasonCode"] == "pii_redaction_unavailable"

    tenant = call(client, "IntakeService/GetDecisionTrail", {"requestId": request_id}, user="alice")
    assert tenant.json()["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_model_content_is_shown_only_to_roles_that_read_requests(client, session_factory):
    await make_user(session_factory, "fm", "facility_manager")
    await make_user(session_factory, "root", "admin")
    request_id = submit(client)
    await SqlAgentRunRecorder(session_factory).record(
        AgentRunSample(
            agent_name="Maintenance Triage Classifier",
            model="gpt-test",
            prompt_sha="abc123",
            status="succeeded",
            error_type=None,
            duration_ms=812,
            input_tokens=120,
            output_tokens=30,
            total_tokens=150,
            started_at=datetime.now(UTC),
            request_id=UUID(request_id),
            evaluation_run_id=None,
            trace_id="0" * 31 + "1",
            input_text="[user] [PII_0001] says the tap drips",
            output_text='{"category": "plumbing", "priority": 2}',
            instructions="You classify maintenance requests.",
        )
    )

    manager = call(client, "IntakeService/GetDecisionTrail", {"requestId": request_id}, user="fm").json()
    [model_call] = manager["modelCalls"]
    assert manager["contentIncluded"] is True
    assert model_call["inputText"] == "[user] [PII_0001] says the tap drips"
    assert model_call["outputText"] == '{"category": "plumbing", "priority": 2}'
    assert model_call["instructions"] == "You classify maintenance requests."
    assert model_call["traceId"].endswith("1")
    viewed = await scalar(
        session_factory, "SELECT count(*) FROM security_audit_events WHERE action = 'decision_trail.content_viewed'"
    )
    assert viewed == 1

    # Separation of duties: the admin sees the metadata (model, tokens, timing), not the text.
    admin = call(client, "IntakeService/GetDecisionTrail", {"requestId": request_id}, user="root").json()
    assert admin.get("contentIncluded", False) is False
    assert admin["modelCalls"][0]["model"] == "gpt-test"
    assert "inputText" not in admin["modelCalls"][0] and "instructions" not in admin["modelCalls"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE decision_steps SET outcome = 'passed'",
        "DELETE FROM decision_steps",
        "UPDATE intake_audit_events SET reason_code = 'x'",
        "DELETE FROM security_audit_events",
    ],
)
async def test_audit_records_cannot_be_changed_or_deleted(client, session_factory, statement):
    await make_user(session_factory, "fm", "facility_manager")  # writes a security audit event
    submit(client)
    async with session_factory() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text(statement))


@pytest.mark.asyncio
async def test_retention_purge_deletes_only_expired_records(client, session_factory):
    submit(client)
    before = await scalar(session_factory, "SELECT count(*) FROM decision_steps")
    settings = Settings(_env_file=None, app_env="test")

    async with session_factory() as session:
        report = await purge_expired(session, settings)
    assert report.deleted["decision_steps"] == 0

    async with session_factory() as session:
        report = await purge_expired(session, settings, now=datetime.now(UTC) + timedelta(days=731))
    assert report.deleted["decision_steps"] == before > 0
