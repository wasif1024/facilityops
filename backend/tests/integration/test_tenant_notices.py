"""FR-61: a person approves the English notice; the application translates it for the tenant."""

from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.app.domain.intake import Translation
from backend.app.services.incident_worker import build_dispatcher
from backend.app.services.operations import InvalidTransition
from backend.app.services.tenant_notices import send_notice
from backend.app.services.world import load_world

from .conftest import call, scalar
from .test_incident_actions import approve, planned_incident
from .test_operations import make_user


class FakeTranslator:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []
        self.fail = fail

    async def translate(self, text, *, source_language, target_language=None):
        self.calls.append((text, source_language, target_language))
        if self.fail:
            raise ConnectionError("translator down")
        return Translation(text=f"[{target_language}] {text}", source_language=source_language)


@pytest_asyncio.fixture
async def approved(session_factory):
    """An approved boiler incident whose tenant wrote in French; its notify_tenant action is queued."""
    fm = await make_user(session_factory, "fm", "facility_manager")
    await make_user(session_factory, "tech", "technician")
    async with session_factory() as session:
        await load_world(session)
    request_id, incident_id, plan_id = await planned_incident(session_factory, "boiler_grounded")
    async with session_factory() as session:
        await session.execute(
            text("UPDATE intake_requests SET source_language = 'fr' WHERE request_id = :r"), {"r": request_id}
        )
        await session.commit()
    await approve(session_factory, incident_id, plan_id, fm)
    await build_dispatcher(session_factory).run_once()
    return {"fm": UUID(fm), "request_id": request_id, "incident_id": incident_id}


DRAFT = (
    "Heating: an engineer is on the way",
    "Dear resident, the boiler that serves your building is losing pressure. A qualified engineer will inspect it "
    "today and restore heating. Please keep windows closed to hold in warmth. Thank you for your patience.",
)


@pytest.mark.asyncio
async def test_approved_notice_is_translated_for_the_tenant_and_both_are_kept(client, session_factory, approved):
    translator = FakeTranslator()
    async with session_factory() as session:
        notice = await send_notice(
            session, translator, incident_id=approved["incident_id"], subject=DRAFT[0], body=DRAFT[1], actor_id=approved["fm"]
        )
    assert (notice.language, notice.edited, notice.reason_code) == ("fr", False, None)
    assert notice.translated_body == f"[fr] {DRAFT[1]}"
    assert [c[1:] for c in translator.calls] == [("en", "fr"), ("en", "fr")]

    action = await scalar(
        session_factory,
        "SELECT status || ':' || reason_code FROM incident_actions WHERE action_type = 'notify_tenant'",
    )
    assert action == "done:notice_sent"
    step = await scalar(
        session_factory,
        "SELECT details->>'language' FROM decision_steps WHERE step = 'incident.tenant_notice_sent'",
    )
    assert step == "fr"

    # The tenant who reported it reads the French, with the approved English one click away.
    alice = await make_user(session_factory, "alice", "tenant")
    async with session_factory() as session:
        await session.execute(
            text("UPDATE intake_requests SET submitted_by_user_id = :u WHERE request_id = :r"),
            {"u": alice, "r": approved["request_id"]},
        )
        await session.commit()
    detail = call(client, "IntakeService/GetRequest", {"requestId": str(approved["request_id"])}, user="alice").json()
    [shown] = detail["notices"]
    assert shown["translatedSubject"] == f"[fr] {DRAFT[0]}" and shown["subject"] == DRAFT[0]
    assert "approvedByUserId" not in shown  # staff details stay with staff
    incident = call(client, "IncidentService/GetIncident", {"incidentId": str(approved["incident_id"])}, user="fm").json()
    assert incident["tenantLanguage"] == "fr"
    assert incident["plans"][0]["tenantNotice"]["subject"] == DRAFT[0]
    assert len(incident["notices"]) == 1


@pytest.mark.asyncio
async def test_a_failed_translation_still_sends_the_approved_english(client, session_factory, approved):
    async with session_factory() as session:
        notice = await send_notice(
            session,
            FakeTranslator(fail=True),
            incident_id=approved["incident_id"],
            subject="Heating update",
            body="An engineer is coming today.",
            actor_id=approved["fm"],
        )
    assert (notice.language, notice.translated_body, notice.reason_code) == ("en", None, "translation_failed")
    assert notice.edited is True  # differs from the communicator's draft


@pytest.mark.asyncio
async def test_notices_refuse_personal_data_and_unapproved_plans(client, session_factory, approved):
    async with session_factory() as session:
        with pytest.raises(InvalidTransition, match="placeholders"):
            await send_notice(
                session, None, incident_id=approved["incident_id"], subject="Hi", body="Dear [PII_0001], ...",
                actor_id=approved["fm"],
            )
    async with session_factory() as session:
        await session.execute(text("UPDATE incidents SET status = 'plan_ready'"))
        await session.commit()
    body = {"incidentId": str(approved["incident_id"]), "subject": "Hi", "body": "Update"}
    refused = call(client, "IncidentService/SendTenantNotice", body, user="fm").json()
    assert "once the incident plan is approved" in refused["message"]
    assert call(client, "IncidentService/SendTenantNotice", body, user="alice").json()["code"] == "permission_denied"
