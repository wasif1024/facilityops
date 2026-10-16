"""Fakes for exercising the Connect services over HTTP without a database."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from fastapi.testclient import TestClient

from backend.app.api.auth import Identity, Principal
from backend.app.api.container import Container, RateLimitState
from backend.app.core.config import Settings
from backend.app.domain.rbac import SYSTEM_ROLE_PERMISSIONS, SystemRole
from backend.app.main import create_app

# X-Dev-User name -> role, for tests that need different callers.
USERS = {
    "alice": SystemRole.TENANT,
    "root": SystemRole.ADMIN,
    "fm": SystemRole.FACILITY_MANAGER,
}


def principal_for(name: str) -> Principal:
    role = USERS[name]
    return Principal(
        user_id=uuid5(NAMESPACE_URL, name),
        subject=name,
        roles=frozenset({role.value}),
        permissions=SYSTEM_ROLE_PERMISSIONS[role],
    )


async def resolve(identity: Identity) -> Principal:
    return principal_for(identity.external_id)


@asynccontextmanager
async def no_session():
    yield None


async def allow(user_id) -> RateLimitState:
    return RateLimitState(
        allowed=True, limit=30, remaining=29, reset_at=datetime(2030, 1, 1, tzinfo=UTC)
    )


def make_client(*, repository=None, coordinator=None, rate_limit=allow) -> TestClient:
    async def no_launch(run_id, mode) -> None:
        return None

    container = Container(
        settings=Settings(app_env="test"),
        session_factory=no_session,
        resolve_principal=resolve,
        check_rate_limit=rate_limit,
        intake_repository_factory=lambda session: repository,
        coordinator_factory=lambda repo: coordinator,
        launch_evaluation=no_launch,
    )
    return TestClient(create_app(Settings(app_env="test"), container=container))


def call(client: TestClient, rpc: str, body: dict, user: str = "alice"):
    """Invoke a unary RPC with the Connect protocol's JSON encoding."""
    return client.post(
        f"/facilityops.v1.{rpc}",
        json=body,
        headers={"Content-Type": "application/json", "X-Dev-User": user},
    )
