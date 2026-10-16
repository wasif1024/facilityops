from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from backend.app.services.rate_limit import (
    build_rate_limit_statement,
    current_window_start,
    hashed_rate_limit_bucket,
)


def test_rate_limit_identity_is_hashed_and_stable():
    first = hashed_rate_limit_bucket("tenant-123", "test-secret")
    second = hashed_rate_limit_bucket("tenant-123", "test-secret")

    assert first == second
    assert first != "tenant-123"
    assert len(first) == 64


def test_window_start_is_aligned_to_window_boundary():
    now = datetime(2026, 10, 16, 12, 0, 47, tzinfo=UTC)

    assert current_window_start(now, 60) == datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def test_rate_limit_increment_is_atomic_postgresql_upsert():
    statement = build_rate_limit_statement(
        bucket_hash="a" * 64,
        window_start=datetime(2026, 10, 16, 12, 0, tzinfo=UTC),
        request_limit=30,
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "ON CONFLICT" in sql
    assert "request_count <" in sql
    assert "RETURNING" in sql
