"""Review queue and work-order RPCs for facility managers and technicians."""

from __future__ import annotations

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from backend.app.api.auth import current_principal
from backend.app.api.container import Container
from backend.app.api.rpc.convert import (
    from_proto_enum,
    operation_errors,
    page,
    parse_uuid,
    text,
    timestamp,
    to_proto_enum,
    viewer,
)
from backend.app.domain.intake import MaintenanceCategory
from backend.app.domain.operations import (
    DismissalReason,
    RejectionReason,
    ReviewResolution,
    WorkOrderStatus,
)
from backend.app.gen.facilityops.v1 import intake_pb
from backend.app.gen.facilityops.v1 import operations_pb as pb
from backend.app.services.operations import OperationsService, ReviewCaseView, WorkOrderView


def to_proto_case(view: ReviewCaseView) -> pb.ReviewCase:
    case, request = view.case, view.request
    return pb.ReviewCase(
        id=str(case.id),
        request_id=str(case.request_id),
        status=to_proto_enum(pb.ReviewCaseStatus, case.status),
        reason_code=case.reason_code,
        redacted_description=text(request.redacted_description),
        suggested_category=to_proto_enum(intake_pb.MaintenanceCategory, request.category),
        suggested_priority=request.priority,
        suggested_confidence=request.confidence,
        resolution=to_proto_enum(pb.ReviewResolution, case.resolution),
        dismissal_reason=to_proto_enum(pb.DismissalReason, case.resolution_reason),
        resolved_by_user_id=text(case.resolved_by_user_id),
        resolved_at=timestamp(case.resolved_at),
        created_at=timestamp(case.created_at),
        work_order_id=text(request.work_order_id),
    )


def to_proto_work_order(view: WorkOrderView) -> pb.WorkOrder:
    wo = view.work_order
    return pb.WorkOrder(
        id=str(wo.id),
        request_id=str(wo.request_id),
        category=to_proto_enum(intake_pb.MaintenanceCategory, wo.category),
        priority=wo.priority,
        confidence=wo.confidence,
        status=to_proto_enum(pb.WorkOrderStatus, wo.status),
        approval_required=wo.approval_required,
        assigned_technician_id=text(wo.assigned_technician_id),
        decided_by_user_id=text(wo.decided_by_user_id),
        decided_at=timestamp(wo.decided_at),
        rejection_reason=to_proto_enum(pb.RejectionReason, wo.decision_reason),
        completed_at=timestamp(wo.completed_at),
        created_at=timestamp(wo.created_at),
        redacted_description=text(view.redacted_description),
    )


def _required_enum(member, enum_type, field: str):
    value = from_proto_enum(member)
    if value is None:
        raise ConnectError(Code.INVALID_ARGUMENT, f"{field} is required")
    return enum_type(value)


class ReviewQueueRpc:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def list_review_cases(
        self, request: pb.ListReviewCasesRequest, ctx: RequestContext
    ) -> pb.ListReviewCasesResponse:
        limit, offset = page(request.limit, request.offset, default=50, maximum=100)
        async with self._c.session_factory() as session:
            views, total = await OperationsService(session).list_review_cases(
                open_only=not request.include_resolved,
                emergency_only=request.emergency_only,
                limit=limit,
                offset=offset,
            )
        return pb.ListReviewCasesResponse(cases=[to_proto_case(v) for v in views], total=total)

    async def resolve_review_case(
        self, request: pb.ResolveReviewCaseRequest, ctx: RequestContext
    ) -> pb.ResolveReviewCaseResponse:
        case_id = parse_uuid(request.case_id, "case_id")
        resolution = _required_enum(request.resolution, ReviewResolution, "resolution")
        category = from_proto_enum(request.category)
        dismissal = from_proto_enum(request.dismissal_reason)
        async with self._c.session_factory() as session:
            with operation_errors():
                view = await OperationsService(session).resolve_review_case(
                    case_id=case_id,
                    actor_id=current_principal().user_id,
                    resolution=resolution,
                    category=MaintenanceCategory(category) if category else None,
                    priority=request.priority or None,
                    dismissal_reason=DismissalReason(dismissal) if dismissal else None,
                )
        return pb.ResolveReviewCaseResponse(review_case=to_proto_case(view))


class WorkOrderRpc:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def list_work_orders(
        self, request: pb.ListWorkOrdersRequest, ctx: RequestContext
    ) -> pb.ListWorkOrdersResponse:
        limit, offset = page(request.limit, request.offset, default=50, maximum=100)
        status = from_proto_enum(request.status)
        async with self._c.session_factory() as session:
            views, total = await OperationsService(session).list_work_orders(
                viewer=viewer(),
                status=WorkOrderStatus(status) if status else None,
                assigned_to_me=request.assigned_to_me,
                limit=limit,
                offset=offset,
            )
        return pb.ListWorkOrdersResponse(
            work_orders=[to_proto_work_order(v) for v in views], total=total
        )

    async def get_work_order(
        self, request: pb.GetWorkOrderRequest, ctx: RequestContext
    ) -> pb.GetWorkOrderResponse:
        work_order_id = parse_uuid(request.work_order_id, "work_order_id")
        async with self._c.session_factory() as session:
            with operation_errors():
                view = await OperationsService(session).get_work_order(work_order_id, viewer())
        return pb.GetWorkOrderResponse(work_order=to_proto_work_order(view))

    async def approve_work_order(
        self, request: pb.ApproveWorkOrderRequest, ctx: RequestContext
    ) -> pb.ApproveWorkOrderResponse:
        work_order_id = parse_uuid(request.work_order_id, "work_order_id")
        async with self._c.session_factory() as session:
            with operation_errors():
                view = await OperationsService(session).approve(
                    work_order_id, current_principal().user_id
                )
        return pb.ApproveWorkOrderResponse(work_order=to_proto_work_order(view))

    async def reject_work_order(
        self, request: pb.RejectWorkOrderRequest, ctx: RequestContext
    ) -> pb.RejectWorkOrderResponse:
        work_order_id = parse_uuid(request.work_order_id, "work_order_id")
        reason = _required_enum(request.reason, RejectionReason, "reason")
        async with self._c.session_factory() as session:
            with operation_errors():
                view = await OperationsService(session).reject(
                    work_order_id, current_principal().user_id, reason
                )
        return pb.RejectWorkOrderResponse(work_order=to_proto_work_order(view))

    async def assign_work_order(
        self, request: pb.AssignWorkOrderRequest, ctx: RequestContext
    ) -> pb.AssignWorkOrderResponse:
        work_order_id = parse_uuid(request.work_order_id, "work_order_id")
        technician_id = parse_uuid(request.technician_user_id, "technician_user_id")
        async with self._c.session_factory() as session:
            with operation_errors():
                view = await OperationsService(session).assign(
                    work_order_id, technician_id, current_principal().user_id
                )
        return pb.AssignWorkOrderResponse(work_order=to_proto_work_order(view))

    async def list_technicians(
        self, request: pb.ListTechniciansRequest, ctx: RequestContext
    ) -> pb.ListTechniciansResponse:
        async with self._c.session_factory() as session:
            rows = await OperationsService(session).list_technicians()
        return pb.ListTechniciansResponse(
            technicians=[
                pb.Technician(
                    user_id=str(user.id),
                    display_name=user.display_name or user.external_id,
                    open_work_orders=load,
                )
                for user, load in rows
            ]
        )

    async def complete_work_order(
        self, request: pb.CompleteWorkOrderRequest, ctx: RequestContext
    ) -> pb.CompleteWorkOrderResponse:
        work_order_id = parse_uuid(request.work_order_id, "work_order_id")
        async with self._c.session_factory() as session:
            with operation_errors():
                view = await OperationsService(session).complete(
                    work_order_id, current_principal().user_id
                )
        return pb.CompleteWorkOrderResponse(work_order=to_proto_work_order(view))
