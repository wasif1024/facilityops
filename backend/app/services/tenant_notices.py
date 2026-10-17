"""Tenant notices in the tenant's language (FR-61).

1. The communicator drafts a notice in English; it travels in the plan (``tenant_notice``).
2. After the plan is approved, a facility manager approves the English text, editing it if needed.
3. The application (not an agent) translates the approved text into the tenant's language with
   Azure AI Translator and stores both: the English is what a person approved, the translation is
   what the tenant reads, labelled as a machine translation.

The tenant's language is the one their request was translated from (FR-60); a request written in
English, or one whose translation failed, gets the English notice. A failed translation never
holds a notice back: the tenant gets the approved English with ``translation_failed`` recorded.
Notices never carry personal data: text with a [PII_nnnn] placeholder is refused.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    IncidentActionRecord,
    IncidentPlanRecord,
    IncidentRecord,
    IntakeAuditEvent,
    IntakeRequestRecord,
    TenantNoticeRecord,
)
from backend.app.domain.decision_trail import DecisionStep, StepOutcome, current_trace_id
from backend.app.domain.incident_plan import NOTICE_BODY_MAX, NOTICE_SUBJECT_MAX, has_placeholder
from backend.app.domain.intake import Translation
from backend.app.services.incidents import APPROVED, append_steps
from backend.app.services.operations import InvalidTransition, NotFound

_log = logging.getLogger("facilityops.notices")
NOTICE_LANGUAGE = "en"


class NoticeTranslator(Protocol):
    async def translate(
        self, text: str, *, source_language: str | None, target_language: str | None = None
    ) -> Translation | None: ...


async def send_notice(
    session: AsyncSession,
    translator: NoticeTranslator | None,
    *,
    incident_id: UUID,
    subject: str,
    body: str,
    actor_id: UUID,
) -> TenantNoticeRecord:
    subject, body = subject.strip(), body.strip()
    if not subject or not body:
        raise InvalidTransition("A notice needs a subject and a message")
    if len(subject) > NOTICE_SUBJECT_MAX or len(body) > NOTICE_BODY_MAX:
        raise InvalidTransition(
            f"Keep the subject under {NOTICE_SUBJECT_MAX} and the message under {NOTICE_BODY_MAX} characters"
        )
    if has_placeholder(subject) or has_placeholder(body):
        raise InvalidTransition("Remove the personal-data placeholders: notices never include personal details")

    incident = await session.get(IncidentRecord, incident_id, with_for_update=True)
    if incident is None:
        raise NotFound("Incident not found")
    if incident.status != APPROVED:
        raise InvalidTransition("Notices are sent once the incident plan is approved")
    request = await session.scalar(
        select(IntakeRequestRecord).where(IntakeRequestRecord.request_id == incident.request_id)
    )
    draft = await _approved_draft(session, incident_id)

    language = (request.source_language if request else None) or NOTICE_LANGUAGE
    translated_subject = translated_body = reason_code = None
    if language != NOTICE_LANGUAGE:
        try:
            if translator is None:
                raise RuntimeError("Translator is not configured")
            subject_t = await translator.translate(subject, source_language=NOTICE_LANGUAGE, target_language=language)
            body_t = await translator.translate(body, source_language=NOTICE_LANGUAGE, target_language=language)
            if subject_t is None or body_t is None:
                raise RuntimeError("Translator returned no translation")
            translated_subject, translated_body = subject_t.text, body_t.text
        except Exception as error:  # noqa: BLE001 - the tenant still gets the approved English
            _log.warning("notice translation failed", extra={"error_type": type(error).__name__})
            language, reason_code = NOTICE_LANGUAGE, "translation_failed"

    edited = draft is None or (draft.get("subject", "").strip(), draft.get("body", "").strip()) != (subject, body)
    notice = TenantNoticeRecord(
        id=uuid4(),
        incident_id=incident.id,
        request_id=incident.request_id,
        subject=subject,
        body=body,
        language=language,
        translated_subject=translated_subject,
        translated_body=translated_body,
        reason_code=reason_code,
        edited=edited,
        approved_by_user_id=actor_id,
    )
    session.add(notice)

    # The queued notify_tenant action of the approved plan is now done.
    queued = await session.scalar(
        select(IncidentActionRecord)
        .where(
            IncidentActionRecord.incident_id == incident.id,
            IncidentActionRecord.action_type == "notify_tenant",
            IncidentActionRecord.status == "queued",
        )
        .order_by(IncidentActionRecord.position)
        .limit(1)
    )
    if queued is not None:
        queued.status, queued.reason_code = "done", "notice_sent"

    session.add(
        IntakeAuditEvent(
            id=uuid4(),
            request_id=incident.request_id,
            event_type="tenant_notice_sent",
            reason_code=reason_code or ("edited" if edited else "as_drafted"),
            actor_id=str(actor_id),
        )
    )
    await append_steps(
        session,
        incident.request_id,
        [
            DecisionStep(
                step="incident.tenant_notice_sent",
                outcome=StepOutcome.FAILED if reason_code else StepOutcome.PASSED,
                started_at=datetime.now(UTC),
                reason_code=reason_code,
                service="Azure AI Translator" if translated_body else "facility manager",
                details={"language": language, "translated": translated_body is not None, "edited": edited},
                trace_id=current_trace_id(),
            )
        ],
    )
    await session.commit()
    return notice


async def _approved_draft(session: AsyncSession, incident_id: UUID) -> dict | None:
    plan = await session.scalar(
        select(IncidentPlanRecord)
        .where(IncidentPlanRecord.incident_id == incident_id, IncidentPlanRecord.status == "approved")
        .order_by(IncidentPlanRecord.version.desc())
        .limit(1)
    )
    return (plan.plan.get("tenant_notice") if plan else None) or None


async def list_notices(
    session: AsyncSession, *, incident_id: UUID | None = None, request_id: UUID | None = None
) -> list[TenantNoticeRecord]:
    query = select(TenantNoticeRecord)
    if incident_id:
        query = query.where(TenantNoticeRecord.incident_id == incident_id)
    if request_id:
        query = query.where(TenantNoticeRecord.request_id == request_id)
    return list(await session.scalars(query.order_by(TenantNoticeRecord.created_at.desc())))
