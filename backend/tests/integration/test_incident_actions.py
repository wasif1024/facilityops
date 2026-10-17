"""FR-25: an approved plan is carried out by the application, with its own rules, exactly once."""

from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.app.services.incident_worker import build_dispatcher
from backend.app.services.incidents import decide_plan
from backend.app.services.world import load_world
from evals.incidents.harness import ScriptedIncidentTeam, load

from .conftest import call, scalar
from .test_incidents import escalated_request
from .test_operations import make_user


async def planned_incident(session_factory, plan: str) -> tuple[UUID, UUID, UUID]:
    """An escalated request whose incident has a proposed plan; returns request, incident and plan ids."""
    request_id = await escalated_request(session_factory)
    team = ScriptedIncidentTeam([{"plan": plan}], load()["plans"])
    await build_dispatcher(session_factory, team).run_once()
    async with session_factory() as session:
        incident_id, plan_id = (
            await session.execute(
                text(
                    "SELECT i.id, p.id FROM incidents i JOIN incident_plans p ON p.incident_id = i.id "
                    "WHERE i.request_id = :r"
                ),
                {"r": request_id},
            )
        ).one()
    return request_id, incident_id, plan_id


async def approve(session_factory, incident_id, plan_id, actor_id, **kwargs) -> None:
    async with session_factory() as session:
        await decide_plan(
            session, incident_id=incident_id, plan_id=plan_id, decision="approve", actor_id=UUID(actor_id), **kwargs
        )


async def actions(session_factory, incident_id) -> list[tuple]:
    async with session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT action_type, status, reason_code, details FROM incident_actions "
                "WHERE incident_id = :i ORDER BY position"
            ),
            {"i": incident_id},
        )
    return [tuple(r) for r in rows]


@pytest_asyncio.fixture
async def world(session_factory):
    fm = await make_user(session_factory, "fm", "facility_manager")
    tech = await make_user(session_factory, "tech", "technician")
    async with session_factory() as session:
        await load_world(session)
    return {"fm": fm, "tech": tech}


@pytest.mark.asyncio
async def test_approval_approves_the_work_order_assigns_and_requests_parts(client, session_factory, world):
    request_id, incident_id, plan_id = await planned_incident(session_factory, "boiler_grounded")
    await approve(session_factory, incident_id, plan_id, world["fm"])
    dispatcher = build_dispatcher(session_factory)
    await dispatcher.run_once()

    dispatch, part, notice = await actions(session_factory, incident_id)
    assert dispatch[:3] == ("dispatch_technician", "done", "assigned")
    assert part[:3] == ("order_part", "requested", "purchase_requested")
    # Priced from the parts table, not the model's estimate.
    assert (part[3]["part_sku"], part[3]["quantity"], part[3]["cost"]) == ("exp-vessel-24l", 1, 60.0)
    assert notice[:3] == ("notify_tenant", "queued", "notice_pending")

    async with session_factory() as session:
        status, assignee, decided_by = (
            await session.execute(
                text("SELECT status, assigned_technician_id, decided_by_user_id FROM work_orders WHERE request_id = :r"),
                {"r": request_id},
            )
        ).one()
    assert (status, str(assignee), str(decided_by)) == ("open", world["tech"], world["fm"])
    assert await scalar(session_factory, "SELECT status FROM intake_requests WHERE request_id = :r", r=request_id) == (
        "work_order_created"
    )
    assert await scalar(session_factory, "SELECT reason_code FROM incidents WHERE id = :i", i=incident_id) == (
        "actions_carried_out"
    )

    steps = await _steps(session_factory, request_id)
    assert steps[-4:] == [
        "incident.work_order_approved",
        "incident.action_dispatch_technician",
        "incident.action_order_part",
        "incident.action_notify_tenant",
    ]

    # Redelivery does nothing twice.
    async with session_factory() as session:
        await session.execute(text("UPDATE outbox_events SET published_at = NULL"))
        await session.commit()
    await dispatcher.run_once()
    assert len(await actions(session_factory, incident_id)) == 3
    assert await _steps(session_factory, request_id) == steps

    # Staff see what was done; the request shows the incident status.
    detail = call(client, "IncidentService/GetIncident", {"incidentId": str(incident_id)}, user="fm").json()
    assert [a["status"] for a in detail["actions"]] == ["done", "requested", "queued"]
    assert detail["actions"][0]["technicianId"] == "tech-001"
    assert detail["plans"][0]["actions"][1]["partSku"] == "exp-vessel-24l"
    found = call(client, "IncidentService/ListIncidents", {"requestId": str(request_id)}, user="fm").json()
    assert [i["id"] for i in found["incidents"]] == [str(incident_id)]
    summary = call(client, "IntakeService/GetRequest", {"requestId": str(request_id)}, user="fm").json()
    assert summary["request"]["incidentStatus"] == "approved"


@pytest.mark.asyncio
async def test_an_action_that_cant_be_done_is_recorded_for_a_person(client, session_factory, world):
    # tech-003 has no user account: the dispatch can't happen, and nobody is guessed instead.
    request_id, incident_id, plan_id = await planned_incident(session_factory, "leak_unverified")
    await approve(session_factory, incident_id, plan_id, world["fm"])
    await build_dispatcher(session_factory).run_once()

    [(kind, status, reason, details)] = await actions(session_factory, incident_id)
    assert (kind, status, reason, details["technician_id"]) == (
        "dispatch_technician",
        "failed",
        "technician_not_linked",
        "tech-003",
    )
    assert await scalar(session_factory, "SELECT reason_code FROM incidents WHERE id = :i", i=incident_id) == (
        "actions_need_attention"
    )
    assignee = await scalar(
        session_factory, "SELECT assigned_technician_id FROM work_orders WHERE request_id = :r", r=request_id
    )
    assert assignee is None


@pytest.mark.asyncio
async def test_a_safety_blocked_plan_is_never_carried_out(client, session_factory, world):
    request_id, incident_id, plan_id = await planned_incident(session_factory, "electrical_blocked")
    # decide_plan refuses; force the approval in the database to prove the executor refuses too.
    async with session_factory() as session:
        await session.execute(text("UPDATE incident_plans SET status = 'approved', decided_by_user_id = :u"), {"u": world["fm"]})
        await session.execute(text("UPDATE incidents SET status = 'approved'"))
        await session.execute(
            text(
                "INSERT INTO outbox_events (id, event_type, aggregate_id, payload, attempts, created_at) "
                "VALUES (gen_random_uuid(), 'incident.plan_decided', :i, CAST(:p AS jsonb), 0, now())"
            ),
            {"i": incident_id, "p": f'{{"plan_id": "{plan_id}", "decision": "approve"}}'},
        )
        await session.commit()
    await build_dispatcher(session_factory).run_once()

    assert [a[:3] for a in await actions(session_factory, incident_id)] == [
        ("dispatch_technician", "failed", "safety_block")
    ]
    status = await scalar(session_factory, "SELECT status FROM work_orders WHERE request_id = :r", r=request_id)
    assert status == "pending_approval"


async def _steps(session_factory, request_id) -> list[str]:
    async with session_factory() as session:
        rows = await session.execute(
            text("SELECT step FROM decision_steps WHERE request_id = :r ORDER BY sequence"), {"r": request_id}
        )
    return [r[0] for r in rows]
