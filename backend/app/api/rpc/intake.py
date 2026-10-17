from __future__ import annotations

from uuid import uuid4

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from protobuf.wkt import Struct
from pydantic import ValidationError

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
from backend.app.db.models import TenantNoticeRecord
from backend.app.domain.rbac import Permission
from backend.app.domain.intake import IntakeCommand, IntakeResult, IntakeStatus, RequestSource
from backend.app.gen.facilityops.v1 import intake_pb as pb
from backend.app.services.agent_monitoring import monitoring_scope
from backend.app.services.decision_trail import DecisionTrailView, read_decision_trail
from backend.app.services.operations import OperationsService, RequestView
from backend.app.services.tenant_notices import list_notices
from backend.app.services.intake_repository import (
    DuplicateRequestInProgress,
    RequestIdOwnedByAnotherUser,
)


def _describe(error: ValidationError) -> str:
    """Field names for field errors; our own fixed messages for cross-field rules.

    Pydantic error inputs are never included, so submitted text and media are not echoed.
    """
    problems = []
    for item in error.errors(include_input=False, include_url=False):
        if item["loc"]:
            problems.append(f"invalid {item['loc'][0]}")
        else:
            problems.append(str(item.get("ctx", {}).get("error", "invalid request")))
    return "; ".join(problems)


def to_proto_result(result: IntakeResult) -> pb.IntakeResult:
    return pb.IntakeResult(
        request_id=str(result.request_id),
        status=to_proto_enum(pb.IntakeStatus, result.status.value),
        reason_code=result.reason_code,
        category=to_proto_enum(
            pb.MaintenanceCategory, result.category.value if result.category else None
        ),
        priority=result.priority,
        confidence=result.confidence,
        work_order_id=text(result.work_order_id),
    )


def to_proto_summary(view: RequestView) -> pb.IntakeRequestSummary:
    request = view.request
    return pb.IntakeRequestSummary(
        request_id=str(request.request_id),
        source=to_proto_enum(pb.RequestSource, request.source),
        status=to_proto_enum(pb.IntakeStatus, request.status),
        reason_code=text(request.reason_code),
        category=to_proto_enum(pb.MaintenanceCategory, request.category),
        priority=request.priority,
        confidence=request.confidence,
        work_order_id=text(request.work_order_id),
        work_order_status=text(view.work_order_status),
        redacted_description=text(request.redacted_description),
        translated_description=text(request.translated_description),
        source_language=text(request.source_language),
        incident_status=text(view.incident_status),
        created_at=timestamp(request.created_at),
    )


def to_proto_notice(record: TenantNoticeRecord, *, staff: bool) -> pb.TenantNotice:
    return pb.TenantNotice(
        id=str(record.id),
        subject=record.subject,
        body=record.body,
        language=record.language,
        translated_subject=text(record.translated_subject),
        translated_body=text(record.translated_body),
        reason_code=text(record.reason_code),
        edited=record.edited if staff else False,
        approved_by_user_id=text(record.approved_by_user_id) if staff else "",
        created_at=timestamp(record.created_at),
    )


def to_proto_trail(view: DecisionTrailView, retention_days: int) -> pb.GetDecisionTrailResponse:
    return pb.GetDecisionTrailResponse(
        request_id=str(view.request_id),
        steps=[
            pb.DecisionStep(
                step=s.step,
                outcome=s.outcome,
                reason_code=text(s.reason_code),
                service=text(s.service),
                version=text(s.version),
                duration_ms=s.duration_ms,
                details=Struct.from_python(s.details or {}),
                trace_id=text(s.trace_id),
                started_at=timestamp(s.started_at),
            )
            for s in view.steps
        ],
        model_calls=[
            pb.ModelCall(
                id=str(c.run.id),
                agent_name=c.run.agent_name,
                model=text(c.run.model),
                prompt_sha=text(c.run.prompt_sha),
                status=c.run.status,
                error_type=text(c.run.error_type),
                duration_ms=c.run.duration_ms,
                input_tokens=c.run.input_tokens,
                output_tokens=c.run.output_tokens,
                trace_id=text(c.run.trace_id),
                started_at=timestamp(c.run.started_at),
                input_text=c.content.input_text if c.content else "",
                output_text=c.content.output_text if c.content else "",
                instructions=text(c.instructions),
            )
            for c in view.model_calls
        ],
        human_actions=[
            pb.HumanAction(
                event_type=a.event_type,
                reason_code=a.reason_code,
                actor_user_id=text(a.actor_id),
                created_at=timestamp(a.created_at),
            )
            for a in view.human_actions
        ],
        content_included=view.content_included,
        content_retention_days=retention_days,
    )


class IntakeRpc:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def submit_intake(
        self, request: pb.SubmitIntakeRequest, ctx: RequestContext
    ) -> pb.SubmitIntakeResponse:
        principal = current_principal()
        try:
            command = IntakeCommand(
                request_id=parse_uuid(request.request_id, "request_id", required=False) or uuid4(),
                source=RequestSource(from_proto_enum(request.source) or RequestSource.TEXT.value),
                text=request.text,
                language=request.language or None,
                media=request.media or None,
                media_type=request.media_type or None,
            )
        except ValidationError as error:
            raise ConnectError(Code.INVALID_ARGUMENT, _describe(error)) from None

        try:
            rate = await self._c.check_rate_limit(principal.user_id)
        except Exception as error:
            raise ConnectError(Code.UNAVAILABLE, "Request protection is unavailable") from error
        headers = ctx.response_headers
        headers["x-ratelimit-limit"] = str(rate.limit)
        headers["x-ratelimit-remaining"] = str(rate.remaining)
        headers["x-ratelimit-reset"] = str(int(rate.reset_at.timestamp()))
        if not rate.allowed:
            raise ConnectError(Code.RESOURCE_EXHAUSTED, "Rate limit exceeded")

        async with self._c.session_factory() as session:
            repository = self._c.intake_repository_factory(session)
            try:
                existing = await repository.begin_request(
                    command, submitted_by_user_id=principal.user_id
                )
            except DuplicateRequestInProgress as error:
                raise ConnectError(
                    Code.ABORTED, "Request with this id is already being processed"
                ) from error
            except RequestIdOwnedByAnotherUser as error:
                raise ConnectError(Code.ALREADY_EXISTS, "Request id is already in use") from error
            except Exception as error:
                raise ConnectError(Code.UNAVAILABLE, "Intake persistence is unavailable") from error
            if existing is not None:
                return pb.SubmitIntakeResponse(result=to_proto_result(existing))

            coordinator = self._c.coordinator_factory(repository)
            try:
                with monitoring_scope(request_id=command.request_id):
                    result = await coordinator.process(command)
            except Exception as error:
                raise ConnectError(Code.UNAVAILABLE, "Intake processing is unavailable") from error
        return pb.SubmitIntakeResponse(result=to_proto_result(result))

    # --- Reading requests and revealing PII -------------------------------------------

    async def list_my_requests(
        self, request: pb.ListMyRequestsRequest, ctx: RequestContext
    ) -> pb.ListMyRequestsResponse:
        limit, offset = page(request.limit, request.offset, default=20, maximum=100)
        async with self._c.session_factory() as session:
            views, total = await OperationsService(session).list_requests(
                submitted_by=current_principal().user_id, status=None, limit=limit, offset=offset
            )
        return pb.ListMyRequestsResponse(requests=[to_proto_summary(v) for v in views], total=total)

    async def list_requests(
        self, request: pb.ListRequestsRequest, ctx: RequestContext
    ) -> pb.ListRequestsResponse:
        limit, offset = page(request.limit, request.offset, default=20, maximum=100)
        status = from_proto_enum(request.status)
        async with self._c.session_factory() as session:
            views, total = await OperationsService(session).list_requests(
                submitted_by=None,
                status=IntakeStatus(status) if status else None,
                limit=limit,
                offset=offset,
            )
        return pb.ListRequestsResponse(requests=[to_proto_summary(v) for v in views], total=total)

    async def get_request(
        self, request: pb.GetRequestRequest, ctx: RequestContext
    ) -> pb.GetRequestResponse:
        request_id = parse_uuid(request.request_id, "request_id")
        async with self._c.session_factory() as session:
            with operation_errors():
                view = await OperationsService(session).get_request(request_id, viewer())
            notices = await list_notices(session, request_id=request_id)
        staff = viewer().can(Permission.INTAKE_READ_ALL)
        return pb.GetRequestResponse(
            request=to_proto_summary(view), notices=[to_proto_notice(n, staff=staff) for n in notices]
        )

    async def get_decision_trail(
        self, request: pb.GetDecisionTrailRequest, ctx: RequestContext
    ) -> pb.GetDecisionTrailResponse:
        request_id = parse_uuid(request.request_id, "request_id")
        async with self._c.session_factory() as session:
            with operation_errors():
                view = await read_decision_trail(session, request_id, viewer())
        return to_proto_trail(view, self._c.settings.model_content_retention_days)

    async def reveal_request_details(
        self, request: pb.RevealRequestDetailsRequest, ctx: RequestContext
    ) -> pb.RevealRequestDetailsResponse:
        request_id = parse_uuid(request.request_id, "request_id")
        # SecurityHeadersMiddleware sends Cache-Control: no-store on every response,
        # so revealed values are never cached by browsers or proxies.
        async with self._c.session_factory() as session:
            with operation_errors():
                details = await OperationsService(
                    session, self._c.settings.pii_mapping_encryption_key
                ).reveal(request_id, viewer())
        return pb.RevealRequestDetailsResponse(
            request_id=str(details.request_id),
            redacted_description=details.redacted_description,
            translated_description=details.translated_description,
            values=details.values,
        )
