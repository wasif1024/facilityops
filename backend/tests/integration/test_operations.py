"""The human loop: review queue, work-order lifecycle, request reads and PII reveal."""

import json
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from backend.app.db.models import IntakeRequestRecord, PiiMappingRecord, WorkOrderRecord
from backend.app.domain.rbac import IdentityProvider
from backend.app.services.users import UserDirectory

from .conftest import TEST_PII_KEY, call, scalar


async def make_user(session_factory, name: str, role: str) -> str:
    async with session_factory() as session:
        access = await UserDirectory(session).grant_role(
            provider=IdentityProvider.DEVELOPMENT, external_id=name, role=role
        )
    return str(access.user.id)


async def pending_work_order(session_factory, *, mapping: dict[str, str] | None = None):
    """A classified request awaiting approval, as the workflow would leave it."""
    request_id, work_order_id = uuid4(), uuid4()
    async with session_factory() as session:
        session.add(
            IntakeRequestRecord(
                id=uuid4(),
                request_id=request_id,
                source="text",
                status="pending_approval",
                reason_code="approval_required",
                category="hvac",
                priority=4,
                confidence=0.9,
                work_order_id=work_order_id,
                redacted_description="[PII_0001] in unit 3A: heating stopped",
            )
        )
        await session.flush()
        session.add(
            WorkOrderRecord(
                id=work_order_id,
                request_id=request_id,
                category="hvac",
                priority=4,
                confidence=0.9,
                status="pending_approval",
                approval_required=True,
            )
        )
        if mapping is not None:
            session.add(
                PiiMappingRecord(
                    request_id=request_id,
                    encrypted_mapping=Fernet(TEST_PII_KEY.encode()).encrypt(
                        json.dumps(mapping).encode()
                    ),
                )
            )
        await session.commit()
    return str(request_id), str(work_order_id)


@pytest.mark.asyncio
async def test_review_case_becomes_a_work_order_the_tenant_can_follow(client, session_factory):
    await make_user(session_factory, "fm", "facility_manager")
    # No Azure services in tests: a routine request fails closed into the review queue.
    submitted = call(client, "IntakeService/SubmitIntake", {"text": "Tap drips"}).json()["result"]
    assert submitted["status"] == "INTAKE_STATUS_HUMAN_REVIEW"

    [case] = call(client, "ReviewQueueService/ListReviewCases", user="fm").json()["cases"]
    assert case["requestId"] == submitted["requestId"]
    assert case["reasonCode"] == "pii_redaction_unavailable"

    resolved = call(
        client,
        "ReviewQueueService/ResolveReviewCase",
        {
            "caseId": case["id"],
            "resolution": "REVIEW_RESOLUTION_WORK_ORDER_CREATED",
            "category": "MAINTENANCE_CATEGORY_PLUMBING",
            "priority": 2,
        },
        user="fm",
    ).json()["reviewCase"]
    assert resolved["status"] == "REVIEW_CASE_STATUS_RESOLVED"
    assert resolved["workOrderId"]

    again = call(
        client,
        "ReviewQueueService/ResolveReviewCase",
        {"caseId": case["id"], "resolution": "REVIEW_RESOLUTION_DISMISSED",
         "dismissalReason": "DISMISSAL_REASON_DUPLICATE"},
        user="fm",
    )
    assert again.json()["code"] == "failed_precondition"
    assert call(client, "ReviewQueueService/ListReviewCases", user="fm").json().get("cases") is None

    [mine] = call(client, "IntakeService/ListMyRequests").json()["requests"]
    assert mine["status"] == "INTAKE_STATUS_WORK_ORDER_CREATED"
    assert mine["workOrderStatus"] == "open"
    assert mine["category"] == "MAINTENANCE_CATEGORY_PLUMBING"

    actor = await scalar(
        session_factory,
        "select actor_id from intake_audit_events where event_type = 'review_resolved'",
    )
    assert actor == call(client, "AccountService/GetMe", user="fm").json()["userId"]


@pytest.mark.asyncio
async def test_emergencies_lead_the_queue_and_dismissal_needs_a_reason(client, session_factory):
    await make_user(session_factory, "fm", "facility_manager")
    call(client, "IntakeService/SubmitIntake", {"text": "Tap drips"})
    call(client, "IntakeService/SubmitIntake", {"text": "There is a gas smell in the lobby"})

    cases = call(client, "ReviewQueueService/ListReviewCases", user="fm").json()["cases"]
    assert [c["status"] for c in cases] == [
        "REVIEW_CASE_STATUS_EMERGENCY_ON_CALL_QUEUE",
        "REVIEW_CASE_STATUS_QUEUED",
    ]

    no_reason = call(
        client,
        "ReviewQueueService/ResolveReviewCase",
        {"caseId": cases[0]["id"], "resolution": "REVIEW_RESOLUTION_DISMISSED"},
        user="fm",
    )
    assert no_reason.json()["code"] == "failed_precondition"

    dismissed = call(
        client,
        "ReviewQueueService/ResolveReviewCase",
        {"caseId": cases[0]["id"], "resolution": "REVIEW_RESOLUTION_DISMISSED",
         "dismissalReason": "DISMISSAL_REASON_HANDLED_BY_ON_CALL"},
        user="fm",
    ).json()["reviewCase"]
    assert dismissed["dismissalReason"] == "DISMISSAL_REASON_HANDLED_BY_ON_CALL"
    statuses = [r["status"] for r in call(client, "IntakeService/ListMyRequests").json()["requests"]]
    assert "INTAKE_STATUS_DISMISSED" in statuses


@pytest.mark.asyncio
async def test_work_order_lifecycle_and_technician_scope(client, session_factory):
    await make_user(session_factory, "fm", "facility_manager")
    tech_id = await make_user(session_factory, "tech", "technician")
    await make_user(session_factory, "other-tech", "technician")
    _, wo_id = await pending_work_order(session_factory)
    body = {"workOrderId": wo_id}

    assert call(client, "WorkOrderService/ApproveWorkOrder", body).status_code == 403  # tenant

    approved = call(client, "WorkOrderService/ApproveWorkOrder", body, user="fm").json()
    assert approved["workOrder"]["status"] == "WORK_ORDER_STATUS_OPEN"
    reject_open = call(
        client, "WorkOrderService/RejectWorkOrder",
        {**body, "reason": "REJECTION_REASON_DUPLICATE"}, user="fm",
    )
    assert reject_open.json()["code"] == "failed_precondition"

    tenant_id = call(client, "AccountService/GetMe").json()["userId"]
    not_a_tech = call(
        client, "WorkOrderService/AssignWorkOrder",
        {**body, "technicianUserId": tenant_id}, user="fm",
    )
    assert not_a_tech.json()["message"] == "Assignee must be an active technician"
    assigned = call(
        client, "WorkOrderService/AssignWorkOrder",
        {**body, "technicianUserId": tech_id}, user="fm",
    ).json()["workOrder"]
    assert assigned["assignedTechnicianId"] == tech_id

    assert call(client, "WorkOrderService/ListWorkOrders", user="tech").json()["total"] == 1
    # Without read_all, assigned_to_me is forced on: another technician sees nothing.
    # Protobuf JSON omits zero values, so an empty result has no "total" key.
    assert call(client, "WorkOrderService/ListWorkOrders", user="other-tech").json().get("total", 0) == 0
    assert call(
        client, "WorkOrderService/GetWorkOrder", body, user="other-tech"
    ).json()["code"] == "not_found"
    assert call(
        client, "WorkOrderService/CompleteWorkOrder", body, user="other-tech"
    ).json()["code"] == "not_found"

    done = call(client, "WorkOrderService/CompleteWorkOrder", body, user="tech").json()
    assert done["workOrder"]["status"] == "WORK_ORDER_STATUS_COMPLETED"
    assert done["workOrder"]["completedAt"]


@pytest.mark.asyncio
async def test_rejection_dismisses_the_request(client, session_factory):
    await make_user(session_factory, "fm", "facility_manager")
    request_id, wo_id = await pending_work_order(session_factory)

    missing_reason = call(client, "WorkOrderService/RejectWorkOrder", {"workOrderId": wo_id}, user="fm")
    assert missing_reason.json()["code"] == "invalid_argument"

    rejected = call(
        client, "WorkOrderService/RejectWorkOrder",
        {"workOrderId": wo_id, "reason": "REJECTION_REASON_COST_NOT_APPROVED"}, user="fm",
    ).json()["workOrder"]
    assert rejected["status"] == "WORK_ORDER_STATUS_REJECTED"
    assert rejected["rejectionReason"] == "REJECTION_REASON_COST_NOT_APPROVED"

    request = call(client, "IntakeService/GetRequest", {"requestId": request_id}, user="fm").json()
    assert request["request"]["status"] == "INTAKE_STATUS_DISMISSED"


@pytest.mark.asyncio
async def test_pii_reveal_is_role_scoped_and_audited(client, session_factory):
    await make_user(session_factory, "fm", "facility_manager")
    tech_id = await make_user(session_factory, "tech", "technician")
    await make_user(session_factory, "other-tech", "technician")
    request_id, wo_id = await pending_work_order(
        session_factory, mapping={"[PII_0001]": "Avery Synthetic"}
    )
    body = {"requestId": request_id}

    # Tenants hold neither reveal permission.
    assert call(client, "IntakeService/RevealRequestDetails", body).status_code == 403

    revealed = call(client, "IntakeService/RevealRequestDetails", body, user="fm")
    assert revealed.json()["values"] == {"[PII_0001]": "Avery Synthetic"}
    assert revealed.json()["redactedDescription"].startswith("[PII_0001]")
    assert revealed.headers["cache-control"] == "no-store"

    # A technician may reveal only for work orders assigned to them.
    denied = call(client, "IntakeService/RevealRequestDetails", body, user="tech")
    assert denied.json()["code"] == "permission_denied"
    call(client, "WorkOrderService/AssignWorkOrder",
         {"workOrderId": wo_id, "technicianUserId": tech_id}, user="fm")
    assert call(
        client, "IntakeService/RevealRequestDetails", body, user="tech"
    ).json()["values"] == {"[PII_0001]": "Avery Synthetic"}
    assert call(
        client, "IntakeService/RevealRequestDetails", body, user="other-tech"
    ).json()["code"] == "permission_denied"

    reveals = await scalar(
        session_factory, "select count(*) from security_audit_events where action = 'pii.revealed'"
    )
    assert reveals == 2  # only successful reveals are recorded
    leaked = await scalar(
        session_factory,
        "select count(*) from security_audit_events where details::text like '%Avery%'",
    )
    assert leaked == 0


@pytest.mark.asyncio
async def test_tenants_only_see_their_own_requests(client, session_factory):
    mine = call(client, "IntakeService/SubmitIntake", {"text": "Tap drips"}).json()["result"]
    call(client, "IntakeService/SubmitIntake", {"text": "Door squeaks"}, user="bob")

    assert call(client, "IntakeService/ListMyRequests").json()["total"] == 1
    assert call(client, "IntakeService/ListRequests").status_code == 403
    own = call(client, "IntakeService/GetRequest", {"requestId": mine["requestId"]}, user="alice")
    assert own.status_code == 200
    other = call(client, "IntakeService/GetRequest", {"requestId": mine["requestId"]}, user="bob")
    assert other.json()["code"] == "not_found"
