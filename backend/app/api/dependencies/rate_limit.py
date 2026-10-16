from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies.auth import Principal
from backend.app.api.dependencies.auth import get_current_principal
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_session
from backend.app.services.rate_limit import consume_rate_limit, hashed_rate_limit_bucket


@dataclass(frozen=True)
class RateLimitState:
    limit: int
    remaining: int
    reset_at: datetime


async def enforce_rate_limit(
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RateLimitState:
    bucket_hash = hashed_rate_limit_bucket(
        principal.subject, settings.rate_limit_hmac_secret
    )
    try:
        allowed, current_count, reset_at = await consume_rate_limit(
            session,
            bucket_hash=bucket_hash,
            now=datetime.now(UTC),
            window_seconds=settings.rate_limit_window_seconds,
            request_limit=settings.rate_limit_requests,
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Request protection is unavailable",
        ) from error

    if not allowed:
        retry_after = max(1, int((reset_at - datetime.now(UTC)).total_seconds()))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    return RateLimitState(
        limit=settings.rate_limit_requests,
        remaining=max(0, settings.rate_limit_requests - current_count),
        reset_at=reset_at,
    )
