from __future__ import annotations

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
)
from backend.app.db.models import IncidentRecord
from backend.app.gen.facilityops.v1 import incidents_pb as pb
from backend.app.services.incidents import escalate_manually, get_incident, list_incidents


def to_proto_incident(incident: IncidentRecord) -> pb.Incident:
    return pb.Incident(
        id=str(incident.id),
        request_id=str(incident.request_id),
        work_order_id=text(incident.work_order_id),
        status=to_proto_enum(pb.IncidentStatus, incident.status),
        escalation_reason=incident.escalation_reason,
        reason_code=text(incident.reason_code),
        escalated_by_user_id=text(incident.escalated_by_user_id),
        created_at=timestamp(incident.created_at),
        updated_at=timestamp(incident.updated_at),
    )


class IncidentRpc:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def list_incidents(self, request: pb.ListIncidentsRequest, ctx: RequestContext) -> pb.ListIncidentsResponse:
        limit, offset = page(request.limit, request.offset, default=20, maximum=100)
        async with self._c.session_factory() as session:
            incidents, total = await list_incidents(
                session, status=from_proto_enum(request.status), limit=limit, offset=offset
            )
        return pb.ListIncidentsResponse(incidents=[to_proto_incident(i) for i in incidents], total=total)

    async def get_incident(self, request: pb.GetIncidentRequest, ctx: RequestContext) -> pb.GetIncidentResponse:
        incident_id = parse_uuid(request.incident_id, "incident_id")
        async with self._c.session_factory() as session:
            with operation_errors():
                incident = await get_incident(session, incident_id)
        return pb.GetIncidentResponse(incident=to_proto_incident(incident))

    async def escalate_request(
        self, request: pb.EscalateRequestRequest, ctx: RequestContext
    ) -> pb.EscalateRequestResponse:
        request_id = parse_uuid(request.request_id, "request_id")
        async with self._c.session_factory() as session:
            with operation_errors():
                incident = await escalate_manually(session, request_id, current_principal().user_id)
            await session.refresh(incident)
        return pb.EscalateRequestResponse(incident=to_proto_incident(incident))
