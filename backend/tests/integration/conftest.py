"""Integration tests against a real PostgreSQL, through the Connect RPC API.

Uses TEST_DATABASE_URL (default: a ``facilityops_test`` database on localhost), which
is created if missing and rebuilt from the Alembic migrations once per test session,
so these tests also prove the migrations work from an empty database. Tests are
skipped when no PostgreSQL server is reachable.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from cryptography.fernet import Fernet
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.api.container import Container
from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.services.evaluations import eval_arguments, run_and_store

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://localhost:5432/facilityops_test"
)
# Fixed per test session; encrypts the synthetic PII mappings the tests insert.
TEST_PII_KEY = Fernet.generate_key().decode()

# Seeded reference data (roles, permissions) is kept; everything else is wiped per test.
_DATA_TABLES = (
    "users",
    "intake_requests",
    "intake_audit_events",
    "evaluation_runs",
    "agent_runs",
    "security_audit_events",
    "rate_limit_counters",
    "decision_steps",
    "prompt_versions",
    "outbox_events",
    "processed_events",
)


def _libpq_url(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _ensure_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    try:
        psycopg.connect(_libpq_url(TEST_DATABASE_URL), connect_timeout=3).close()
        return
    except psycopg.OperationalError as error:
        if "does not exist" not in str(error):
            pytest.skip(f"PostgreSQL not reachable for integration tests: {error}")
    admin_url = _libpq_url(url.set(database="postgres").render_as_string(hide_password=False))
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=3) as connection:
        connection.execute(f'CREATE DATABASE "{url.database}"')


@pytest.fixture(scope="session")
def migrated_database() -> str:
    _ensure_database()
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
def session_factory(migrated_database):
    # NullPool: no connection outlives the event loop that opened it.
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with psycopg.connect(_libpq_url(migrated_database), autocommit=True) as connection:
        connection.execute(f"TRUNCATE {', '.join(_DATA_TABLES)} CASCADE")
    yield factory


@pytest.fixture
def client(session_factory, migrated_database):
    settings = Settings(
        app_env="test",
        database_url=migrated_database,
        azure_language_endpoint=None,
        content_safety_endpoint=None,
        foundry_endpoint=None,
        rate_limit_requests=1000,
        pii_mapping_encryption_key=TEST_PII_KEY,
    )
    container = Container.default(settings, session_factory=session_factory)

    async def run_evaluation_inline(run_id, mode):
        # Production launches a background task; tests wait so results can be asserted.
        await run_and_store(
            eval_arguments(mode), source="admin", run_id=run_id, session_factory=session_factory
        )

    container.launch_evaluation = run_evaluation_inline
    with TestClient(create_app(settings, container=container)) as test_client:
        yield test_client


def call(client: TestClient, rpc: str, body: dict | None = None, user: str = "alice"):
    """Invoke a unary RPC with the Connect protocol's JSON encoding."""
    return client.post(
        f"/facilityops.v1.{rpc}",
        json=body or {},
        headers={"Content-Type": "application/json", "X-Dev-User": user},
    )


async def scalar(session_factory, sql: str, **params):
    async with session_factory() as session:
        return (await session.execute(text(sql), params)).scalar()
