from __future__ import annotations

from uuid import uuid4

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from pydantic import ValidationError

from backend.app.api.auth import current_principal
from backend.app.api.container import Container
from backend.app.api.rpc.convert import from_proto_enum, parse_uuid, text, to_proto_enum
from backend.app.domain.intake import IntakeCommand, IntakeResult, RequestSource
from backend.app.gen.facilityops.v1 import intake_pb as pb
from backend.app.services.agent_monitoring import monitoring_scope
from backend.app.services.intake_repository import (
    DuplicateRequestInProgress,
    RequestIdOwnedByAnotherUser,
)


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
            )
        except ValidationError as error:
            # Field names only; the submitted text is never echoed back.
            fields = ", ".join(str(e["loc"][0]) for e in error.errors())
            raise ConnectError(Code.INVALID_ARGUMENT, f"Invalid field(s): {fields}") from None

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
