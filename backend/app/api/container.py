"""Wiring for the RPC services. Tests pass a Container with fakes instead of the defaults."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.api.auth import Identity, Principal, resolve_principal
from backend.app.core.config import Settings
from backend.app.domain.workflow import IntakeCoordinator
from backend.app.services.agent_monitoring import AgentRunRecorder, SqlAgentRunRecorder
from backend.app.services.azure_adapters import (
    AzureContentSafetyInspector,
    AzureLanguagePiiRedactor,
    FoundryMaintenanceClassifier,
)
from backend.app.services.evaluations import execute_admin_run
from backend.app.services.intake_repository import SqlIntakeRepository
from backend.app.services.rate_limit import consume_rate_limit, hashed_rate_limit_bucket

PrincipalResolver = Callable[[Identity], Awaitable[Principal]]
EvaluationLauncher = Callable[[UUID, str], Awaitable[None]]


@dataclass(frozen=True)
class RateLimitState:
    allowed: bool
    limit: int
    remaining: int
    reset_at: datetime


RateLimiter = Callable[[UUID], Awaitable[RateLimitState]]


@dataclass
class Container:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    resolve_principal: PrincipalResolver
    # Per-user request budget for intake; keyed by an HMAC of the user id.
    check_rate_limit: RateLimiter
    intake_repository_factory: Callable[[AsyncSession], Any]
    coordinator_factory: Callable[[Any], Any]
    # Starts an evaluation run and returns without waiting for it to finish.
    launch_evaluation: EvaluationLauncher

    @classmethod
    def default(
        cls,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> Container:
        if session_factory is None:
            engine = create_async_engine(settings.database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
        recorder = SqlAgentRunRecorder(session_factory)

        async def resolve(identity: Identity) -> Principal:
            return await resolve_principal(identity, settings, session_factory)

        async def check_rate_limit(user_id: UUID) -> RateLimitState:
            async with session_factory() as session:
                allowed, count, reset_at = await consume_rate_limit(
                    session,
                    bucket_hash=hashed_rate_limit_bucket(
                        str(user_id), settings.rate_limit_hmac_secret
                    ),
                    now=datetime.now(UTC),
                    window_seconds=settings.rate_limit_window_seconds,
                    request_limit=settings.rate_limit_requests,
                )
            return RateLimitState(
                allowed=allowed,
                limit=settings.rate_limit_requests,
                remaining=max(0, settings.rate_limit_requests - count),
                reset_at=reset_at,
            )

        def coordinator_for(repository: SqlIntakeRepository) -> IntakeCoordinator:
            return build_coordinator(settings, repository, recorder)

        return cls(
            settings=settings,
            session_factory=session_factory,
            resolve_principal=resolve,
            check_rate_limit=check_rate_limit,
            intake_repository_factory=SqlIntakeRepository,
            coordinator_factory=coordinator_for,
            launch_evaluation=_background_launcher(session_factory),
        )


def build_coordinator(
    settings: Settings, repository: SqlIntakeRepository, recorder: AgentRunRecorder
) -> IntakeCoordinator:
    return IntakeCoordinator(
        pii_redactor=AzureLanguagePiiRedactor(settings),
        safety_inspector=AzureContentSafetyInspector(settings),
        classifier=FoundryMaintenanceClassifier(settings, recorder=recorder),
        work_order_writer=repository,
        decision_writer=repository,
        emergency_handoff=repository.emergency_handoff,
        minimum_confidence=settings.minimum_classification_confidence,
        approval_priority_threshold=settings.approval_priority_threshold,
    )


def _background_launcher(
    session_factory: async_sessionmaker[AsyncSession],
) -> EvaluationLauncher:
    # Strong references keep running tasks from being garbage-collected mid-run.
    running: set[asyncio.Task[None]] = set()

    async def launch(run_id: UUID, mode: str) -> None:
        task = asyncio.create_task(execute_admin_run(run_id, mode, session_factory))
        running.add(task)
        task.add_done_callback(running.discard)

    return launch
