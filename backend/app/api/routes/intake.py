from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies.auth import FacilityRole, Principal, require_roles
from backend.app.api.dependencies.rate_limit import RateLimitState, enforce_rate_limit
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_session
from backend.app.domain.intake import IntakeCommand, IntakeResult
from backend.app.domain.workflow import IntakeCoordinator
from backend.app.services.azure_adapters import (
    AzureContentSafetyInspector,
    AzureLanguagePiiRedactor,
    FoundryMaintenanceClassifier,
)
from backend.app.services.intake_repository import (
    DuplicateRequestInProgress,
    SqlIntakeRepository,
)


router = APIRouter(prefix="/api/v1", tags=["intake"])
_INTAKE_ROLES = (
    FacilityRole.TENANT,
    FacilityRole.TECHNICIAN,
    FacilityRole.FACILITY_MANAGER,
)


def get_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlIntakeRepository:
    return SqlIntakeRepository(session)


def get_coordinator(
    repository: Annotated[SqlIntakeRepository, Depends(get_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> IntakeCoordinator:
    return IntakeCoordinator(
        pii_redactor=AzureLanguagePiiRedactor(settings),
        safety_inspector=AzureContentSafetyInspector(settings),
        classifier=FoundryMaintenanceClassifier(settings),
        work_order_writer=repository,
        decision_writer=repository,
        emergency_handoff=repository.emergency_handoff,
        minimum_confidence=settings.minimum_classification_confidence,
        approval_priority_threshold=settings.approval_priority_threshold,
    )


@router.post(
    "/intake",
    response_model=IntakeResult,
    status_code=status.HTTP_200_OK,
    summary="Submit a normalized maintenance request",
)
async def submit_intake(
    command: IntakeCommand,
    response: Response,
    principal: Annotated[Principal, Depends(require_roles(*_INTAKE_ROLES))],
    rate_limit: Annotated[RateLimitState, Depends(enforce_rate_limit)],
    repository: Annotated[SqlIntakeRepository, Depends(get_repository)],
    coordinator: Annotated[IntakeCoordinator, Depends(get_coordinator)],
) -> IntakeResult:
    response.headers["X-RateLimit-Limit"] = str(rate_limit.limit)
    response.headers["X-RateLimit-Remaining"] = str(rate_limit.remaining)
    response.headers["X-RateLimit-Reset"] = str(int(rate_limit.reset_at.timestamp()))

    try:
        existing_result = await repository.begin_request(command)
    except DuplicateRequestInProgress as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this id is already being processed",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Intake persistence is unavailable",
        ) from error

    if existing_result is not None:
        return existing_result

    try:
        result = await coordinator.process(command)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Intake processing is unavailable",
        ) from error

    return result
