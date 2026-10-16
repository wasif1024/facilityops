from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import RateLimitCounter


def hashed_rate_limit_bucket(identity: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), identity.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def current_window_start(now: datetime, window_seconds: int) -> datetime:
    if window_seconds < 1:
        raise ValueError("window_seconds must be positive")
    timestamp = int(now.timestamp())
    return datetime.fromtimestamp(timestamp - timestamp % window_seconds, tz=UTC)


def build_rate_limit_statement(
    *,
    bucket_hash: str,
    window_start: datetime,
    request_limit: int,
):
    if request_limit < 1:
        raise ValueError("request_limit must be positive")

    table = RateLimitCounter.__table__
    return (
        insert(RateLimitCounter)
        .values(bucket_hash=bucket_hash, window_start=window_start, request_count=1)
        .on_conflict_do_update(
            index_elements=[table.c.bucket_hash, table.c.window_start],
            set_={"request_count": table.c.request_count + 1},
            where=table.c.request_count < request_limit,
        )
        .returning(table.c.request_count)
    )


async def consume_rate_limit(
    session: AsyncSession,
    *,
    bucket_hash: str,
    now: datetime,
    window_seconds: int,
    request_limit: int,
) -> tuple[bool, int, datetime]:
    window_start = current_window_start(now, window_seconds)
    statement = build_rate_limit_statement(
        bucket_hash=bucket_hash,
        window_start=window_start,
        request_limit=request_limit,
    )
    result = await session.execute(statement)
    request_count = result.scalar_one_or_none()
    await session.commit()
    if request_count is None:
        return False, request_limit, window_start + timedelta(seconds=window_seconds)
    return True, request_count, window_start + timedelta(seconds=window_seconds)


async def delete_expired_rate_limits(session: AsyncSession, *, now: datetime) -> int:
    result = await session.execute(
        delete(RateLimitCounter).where(
            RateLimitCounter.window_start < now - timedelta(days=1)
        )
    )
    await session.commit()
    return int(result.rowcount or 0)
