"""Review queue, work-order lifecycle, request reads and the audited PII reveal.

Every decision writes an ``intake_audit_events`` row with the acting user; every PII
reveal writes a ``security_audit_events`` row. Neither ever stores personal data.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    HumanReviewCase,
    IntakeAuditEvent,
    IntakeRequestRecord,
    PiiMappingRecord,
    SecurityAuditEvent,
    UserRecord,
    UserRoleRecord,
    WorkOrderRecord,
)
from backend.app.domain.intake import IntakeStatus, MaintenanceCategory
from backend.app.domain.operations import (
    WORK_ORDER_TRANSITIONS,
    DismissalReason,
    RejectionReason,
    ReviewCaseStatus,
    ReviewResolution,
    WorkOrderStatus,
)
from backend.app.domain.rbac import Permission, SystemRole, UserStatus

_OPEN_CASE_STATUSES = (ReviewCaseStatus.QUEUED.value, ReviewCaseStatus.EMERGENCY_ON_CALL_QUEUE.value)


class OperationError(Exception):
    """Base class; messages are safe to return to the caller."""


class NotFound(OperationError):
    pass


class Forbidden(OperationError):
    pass


class InvalidTransition(OperationError):
    pass


@dataclass(frozen=True)
class Viewer:
    user_id: UUID
    permissions: Collection[Permission]

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions


@dataclass(frozen=True)
class RequestView:
    request: IntakeRequestRecord
    work_order_status: str | None


@dataclass(frozen=True)
class ReviewCaseView:
    case: HumanReviewCase
    request: IntakeRequestRecord


@dataclass(frozen=True)
class WorkOrderView:
    work_order: WorkOrderRecord
    redacted_description: str | None
    translated_description: str | None = None
    source_language: str | None = None


# The request text shown with a work order: redacted original plus the staff translation (FR-60).
_REQUEST_TEXT = (
    IntakeRequestRecord.redacted_description,
    IntakeRequestRecord.translated_description,
    IntakeRequestRecord.source_language,
)


@dataclass(frozen=True)
class RevealedDetails:
    request_id: UUID
    redacted_description: str
    values: dict[str, str]
    translated_description: str = ""


class OperationsService:
    def __init__(self, session: AsyncSession, pii_key: str | None = None) -> None:
        self._session = session
        self._pii_key = pii_key

    # --- Intake requests -----------------------------------------------------------

    async def list_requests(
        self,
        *,
        submitted_by: UUID | None,
        status: IntakeStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[RequestView], int]:
        query = select(IntakeRequestRecord, WorkOrderRecord.status).outerjoin(
            WorkOrderRecord, WorkOrderRecord.request_id == IntakeRequestRecord.request_id
        )
        if submitted_by is not None:
            query = query.where(IntakeRequestRecord.submitted_by_user_id == submitted_by)
        if status is not None:
            query = query.where(IntakeRequestRecord.status == status.value)
        total = await self._count(query)
        rows = await self._session.execute(
            query.order_by(IntakeRequestRecord.created_at.desc()).limit(limit).offset(offset)
        )
        return [RequestView(request, wo_status) for request, wo_status in rows], total

    async def get_request(self, request_id: UUID, viewer: Viewer) -> RequestView:
        row = (
            await self._session.execute(
                select(IntakeRequestRecord, WorkOrderRecord.status)
                .outerjoin(
                    WorkOrderRecord, WorkOrderRecord.request_id == IntakeRequestRecord.request_id
                )
                .where(IntakeRequestRecord.request_id == request_id)
            )
        ).first()
        # Not found and not yours look the same, so ids cannot be probed.
        if row is None or not (
            viewer.can(Permission.INTAKE_READ_ALL)
            or row[0].submitted_by_user_id == viewer.user_id
        ):
            raise NotFound("Request not found")
        return RequestView(row[0], row[1])

    async def reveal(self, request_id: UUID, viewer: Viewer) -> RevealedDetails:
        request = await self._session.scalar(
            select(IntakeRequestRecord).where(IntakeRequestRecord.request_id == request_id)
        )
        if request is None:
            raise NotFound("Request not found")
        if not viewer.can(Permission.PII_REVEAL_ALL):
            assigned = await self._session.scalar(
                select(WorkOrderRecord.id).where(
                    WorkOrderRecord.request_id == request_id,
                    WorkOrderRecord.assigned_technician_id == viewer.user_id,
                )
            )
            if not (viewer.can(Permission.PII_REVEAL_ASSIGNED) and assigned):
                raise Forbidden("Details can only be revealed for work orders assigned to you")

        mapping_row = await self._session.get(PiiMappingRecord, request_id)
        values: dict[str, str] = {}
        if mapping_row is not None:
            if not self._pii_key:
                raise OperationError("PII mapping key is not configured")
            try:
                values = json.loads(
                    Fernet(self._pii_key.encode("ascii")).decrypt(mapping_row.encrypted_mapping)
                )
            except InvalidToken as error:
                raise OperationError("PII mapping cannot be decrypted with the current key") from error

        self._session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=viewer.user_id,
                action="pii.revealed",
                target_type="intake_request",
                target_id=str(request_id),
                details={"fields": len(values)},
            )
        )
        await self._session.commit()
        return RevealedDetails(
            request_id, request.redacted_description or "", values, request.translated_description or ""
        )

    # --- Review queue --------------------------------------------------------------

    async def list_review_cases(
        self, *, open_only: bool, emergency_only: bool, limit: int, offset: int
    ) -> tuple[list[ReviewCaseView], int]:
        query = select(HumanReviewCase, IntakeRequestRecord).join(
            IntakeRequestRecord, IntakeRequestRecord.request_id == HumanReviewCase.request_id
        )
        if open_only:
            query = query.where(HumanReviewCase.status.in_(_OPEN_CASE_STATUSES))
        if emergency_only:
            query = query.where(
                HumanReviewCase.status == ReviewCaseStatus.EMERGENCY_ON_CALL_QUEUE.value
            )
        total = await self._count(query)
        # Emergencies first, then oldest first: a queue, not a feed.
        is_emergency = HumanReviewCase.status == ReviewCaseStatus.EMERGENCY_ON_CALL_QUEUE.value
        rows = await self._session.execute(
            query.order_by(is_emergency.desc(), HumanReviewCase.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        return [ReviewCaseView(case, request) for case, request in rows], total

    async def resolve_review_case(
        self,
        *,
        case_id: UUID,
        actor_id: UUID,
        resolution: ReviewResolution,
        category: MaintenanceCategory | None = None,
        priority: int | None = None,
        dismissal_reason: DismissalReason | None = None,
    ) -> ReviewCaseView:
        case = await self._session.get(HumanReviewCase, case_id, with_for_update=True)
        if case is None:
            raise NotFound("Review case not found")
        if case.status not in _OPEN_CASE_STATUSES:
            raise InvalidTransition("Review case is already resolved")
        request = await self._session.scalar(
            select(IntakeRequestRecord).where(IntakeRequestRecord.request_id == case.request_id)
        )
        now = datetime.now(UTC)

        if resolution is ReviewResolution.WORK_ORDER_CREATED:
            if category is None or priority is None or not 1 <= priority <= 5:
                raise InvalidTransition("A work order needs a category and a priority from 1 to 5")
            if await self._session.scalar(
                select(WorkOrderRecord.id).where(WorkOrderRecord.request_id == case.request_id)
            ):
                raise InvalidTransition("This request already has a work order")
            # The reviewer's decision is the human approval, so the order starts open.
            work_order = WorkOrderRecord(
                id=uuid4(),
                request_id=case.request_id,
                category=category.value,
                priority=priority,
                confidence=1.0,
                status=WorkOrderStatus.OPEN.value,
                approval_required=False,
                decided_by_user_id=actor_id,
                decided_at=now,
            )
            self._session.add(work_order)
            request.status = IntakeStatus.WORK_ORDER_CREATED.value
            request.category = category.value
            request.priority = priority
            request.work_order_id = work_order.id
            reason = ReviewResolution.WORK_ORDER_CREATED.value
        else:
            if dismissal_reason is None:
                raise InvalidTransition("Dismissing needs a dismissal reason")
            request.status = IntakeStatus.DISMISSED.value
            reason = dismissal_reason.value

        case.status = ReviewCaseStatus.RESOLVED.value
        case.resolution = resolution.value
        case.resolution_reason = None if resolution is ReviewResolution.WORK_ORDER_CREATED else reason
        case.resolved_by_user_id = actor_id
        case.resolved_at = now
        self._audit(case.request_id, "review_resolved", reason, actor_id)
        await self._session.commit()
        return ReviewCaseView(case, request)

    # --- Work orders ---------------------------------------------------------------

    async def list_work_orders(
        self,
        *,
        viewer: Viewer,
        status: WorkOrderStatus | None,
        assigned_to_me: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkOrderView], int]:
        query = select(WorkOrderRecord, *_REQUEST_TEXT).join(
            IntakeRequestRecord, IntakeRequestRecord.request_id == WorkOrderRecord.request_id
        )
        # Without read_all, a technician only ever sees their own assignments.
        if assigned_to_me or not viewer.can(Permission.WORK_ORDERS_READ_ALL):
            query = query.where(WorkOrderRecord.assigned_technician_id == viewer.user_id)
        if status is not None:
            query = query.where(WorkOrderRecord.status == status.value)
        total = await self._count(query)
        rows = await self._session.execute(
            query.order_by(WorkOrderRecord.priority.desc(), WorkOrderRecord.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        return [WorkOrderView(wo, *text) for wo, *text in rows], total

    async def get_work_order(self, work_order_id: UUID, viewer: Viewer) -> WorkOrderView:
        row = (
            await self._session.execute(
                select(WorkOrderRecord, *_REQUEST_TEXT)
                .join(
                    IntakeRequestRecord,
                    IntakeRequestRecord.request_id == WorkOrderRecord.request_id,
                )
                .where(WorkOrderRecord.id == work_order_id)
            )
        ).first()
        if row is None or not (
            viewer.can(Permission.WORK_ORDERS_READ_ALL)
            or row[0].assigned_technician_id == viewer.user_id
        ):
            raise NotFound("Work order not found")
        return WorkOrderView(row[0], *row[1:])

    async def approve(self, work_order_id: UUID, actor_id: UUID) -> WorkOrderView:
        work_order = await self._transition(work_order_id, "approve")
        work_order.decided_by_user_id = actor_id
        work_order.decided_at = datetime.now(UTC)
        await self._set_request_status(work_order.request_id, IntakeStatus.WORK_ORDER_CREATED)
        self._audit(work_order.request_id, "work_order_approved", "approved", actor_id)
        return await self._commit_view(work_order)

    async def reject(
        self, work_order_id: UUID, actor_id: UUID, reason: RejectionReason
    ) -> WorkOrderView:
        work_order = await self._transition(work_order_id, "reject")
        work_order.decided_by_user_id = actor_id
        work_order.decided_at = datetime.now(UTC)
        work_order.decision_reason = reason.value
        await self._set_request_status(work_order.request_id, IntakeStatus.DISMISSED)
        self._audit(work_order.request_id, "work_order_rejected", reason.value, actor_id)
        return await self._commit_view(work_order)

    async def assign(
        self, work_order_id: UUID, technician_id: UUID, actor_id: UUID
    ) -> WorkOrderView:
        work_order = await self._session.get(WorkOrderRecord, work_order_id, with_for_update=True)
        if work_order is None:
            raise NotFound("Work order not found")
        if work_order.status not in {WorkOrderStatus.OPEN.value, WorkOrderStatus.PENDING_APPROVAL.value}:
            raise InvalidTransition(f"A {work_order.status} work order cannot be assigned")
        is_technician = await self._session.scalar(
            select(func.count())
            .select_from(UserRoleRecord)
            .join(UserRecord, UserRecord.id == UserRoleRecord.user_id)
            .where(
                UserRoleRecord.user_id == technician_id,
                UserRoleRecord.role_id == SystemRole.TECHNICIAN.value,
                UserRecord.status == UserStatus.ACTIVE.value,
            )
        )
        if not is_technician:
            raise InvalidTransition("Assignee must be an active technician")
        work_order.assigned_technician_id = technician_id
        self._audit(work_order.request_id, "work_order_assigned", "assigned", actor_id)
        return await self._commit_view(work_order)

    async def complete(self, work_order_id: UUID, actor_id: UUID) -> WorkOrderView:
        work_order = await self._session.get(WorkOrderRecord, work_order_id, with_for_update=True)
        if work_order is None or work_order.assigned_technician_id != actor_id:
            raise NotFound("Work order not found")
        work_order = await self._transition(work_order_id, "complete")
        work_order.completed_at = datetime.now(UTC)
        self._audit(work_order.request_id, "work_order_completed", "completed", actor_id)
        return await self._commit_view(work_order)

    async def list_technicians(self) -> list[tuple[UserRecord, int]]:
        """Active technicians and how many open work orders each has."""
        open_load = (
            select(func.count())
            .select_from(WorkOrderRecord)
            .where(
                WorkOrderRecord.assigned_technician_id == UserRecord.id,
                WorkOrderRecord.status == WorkOrderStatus.OPEN.value,
            )
            .scalar_subquery()
        )
        rows = await self._session.execute(
            select(UserRecord, open_load)
            .join(UserRoleRecord, UserRoleRecord.user_id == UserRecord.id)
            .where(
                UserRoleRecord.role_id == SystemRole.TECHNICIAN.value,
                UserRecord.status == UserStatus.ACTIVE.value,
            )
            .order_by(UserRecord.display_name)
        )
        return [(user, int(count)) for user, count in rows]

    # --- Helpers -------------------------------------------------------------------

    async def _transition(self, work_order_id: UUID, action: str) -> WorkOrderRecord:
        work_order = await self._session.get(WorkOrderRecord, work_order_id, with_for_update=True)
        if work_order is None:
            raise NotFound("Work order not found")
        allowed_from, target = WORK_ORDER_TRANSITIONS[action]
        if work_order.status not in {status.value for status in allowed_from}:
            raise InvalidTransition(f"Cannot {action} a {work_order.status} work order")
        work_order.status = target.value
        return work_order

    async def _set_request_status(self, request_id: UUID, status: IntakeStatus) -> None:
        request = await self._session.scalar(
            select(IntakeRequestRecord).where(IntakeRequestRecord.request_id == request_id)
        )
        if request is not None:
            request.status = status.value

    async def _commit_view(self, work_order: WorkOrderRecord) -> WorkOrderView:
        await self._session.commit()
        row = (
            await self._session.execute(
                select(*_REQUEST_TEXT).where(IntakeRequestRecord.request_id == work_order.request_id)
            )
        ).first()
        return WorkOrderView(work_order, *(row or (None, None, None)))

    def _audit(self, request_id: UUID, event_type: str, reason_code: str, actor_id: UUID) -> None:
        self._session.add(
            IntakeAuditEvent(
                id=uuid4(),
                request_id=request_id,
                event_type=event_type,
                reason_code=reason_code,
                actor_id=str(actor_id),
            )
        )

    async def _count(self, query: Any) -> int:
        return int(
            await self._session.scalar(select(func.count()).select_from(query.subquery())) or 0
        )
