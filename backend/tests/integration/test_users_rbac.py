import asyncio

import pytest
from sqlalchemy import select

from backend.app.db.models import RolePermissionRecord
from backend.app.domain.rbac import SYSTEM_ROLE_PERMISSIONS, IdentityProvider, Permission
from backend.app.services.users import UserDirectory

from .conftest import call


async def make_admin(session_factory, name: str = "root") -> None:
    async with session_factory() as session:
        await UserDirectory(session).grant_role(
            provider=IdentityProvider.DEVELOPMENT, external_id=name, role="admin"
        )


@pytest.mark.asyncio
async def test_seeded_permissions_match_code(session_factory):
    async with session_factory() as session:
        rows = (await session.execute(select(RolePermissionRecord))).scalars().all()
    seeded: dict[str, set[str]] = {}
    for row in rows:
        seeded.setdefault(row.role_id, set()).add(row.permission_id)

    assert seeded == {
        role.value: {p.value for p in permissions}
        for role, permissions in SYSTEM_ROLE_PERMISSIONS.items()
    }
    assert set().union(*seeded.values()) == {p.value for p in Permission}


def test_first_sign_in_provisions_least_privileged_user(client):
    response = call(client, "AccountService/GetMe")

    assert response.status_code == 200
    body = response.json()
    assert body["roles"] == ["tenant"]
    assert body["permissions"] == ["intake:read_own", "intake:submit"]
    assert call(client, "AccountService/GetMe").json()["userId"] == body["userId"]


def test_tenant_cannot_use_admin_services(client):
    for rpc in (
        "UserAdminService/ListUsers",
        "UserAdminService/ListRoles",
        "EvaluationService/ListEvaluationRuns",
        "MonitoringService/GetAgentReport",
    ):
        assert call(client, rpc).status_code == 403, rpc


@pytest.mark.asyncio
async def test_admin_manages_users_with_audit_trail(client, session_factory):
    await make_admin(session_factory)

    created = call(
        client,
        "UserAdminService/CreateUser",
        {
            "externalId": "8f1c2d3e-0000-4000-8000-000000000001",
            "displayName": "Sam Synthetic",
            "email": "sam.synthetic@example.com",
            "roles": ["technician"],
        },
        user="root",
    )
    assert created.status_code == 200, created.text
    user = created.json()["user"]
    assert user["roles"] == ["technician"]
    assert user["identityProvider"] == "IDENTITY_PROVIDER_ENTRA"
    user_id = user["id"]

    duplicate = call(
        client,
        "UserAdminService/CreateUser",
        {"externalId": "8f1c2d3e-0000-4000-8000-000000000001"},
        user="root",
    )
    assert duplicate.json()["code"] == "already_exists"

    updated = call(
        client,
        "UserAdminService/SetUserRoles",
        {"userId": user_id, "roles": ["technician", "facility_manager"]},
        user="root",
    )
    assert updated.json()["user"]["roles"] == ["facility_manager", "technician"]

    unknown = call(
        client,
        "UserAdminService/SetUserRoles",
        {"userId": user_id, "roles": ["superuser"]},
        user="root",
    )
    assert unknown.json()["code"] == "failed_precondition"

    managers = call(
        client, "UserAdminService/ListUsers", {"role": "facility_manager"}, user="root"
    ).json()
    assert [u["id"] for u in managers["users"]] == [user_id]

    audit = call(client, "UserAdminService/ListAuditEvents", user="root").json()
    actions = [event["action"] for event in audit["events"]]
    assert "user.created" in actions and "user.roles_changed" in actions
    roles_changed = next(e for e in audit["events"] if e["action"] == "user.roles_changed")
    assert roles_changed["details"] == {"added": ["facility_manager"], "removed": []}
    assert "sam.synthetic@example.com" not in str(audit)


@pytest.mark.asyncio
async def test_last_admin_cannot_be_removed_or_disabled(client, session_factory):
    await make_admin(session_factory)
    me = call(client, "AccountService/GetMe", user="root").json()

    demote = call(
        client,
        "UserAdminService/SetUserRoles",
        {"userId": me["userId"], "roles": ["tenant"]},
        user="root",
    )
    assert demote.json()["code"] == "failed_precondition"
    assert "administrator" in demote.json()["message"]

    disable_self = call(
        client,
        "UserAdminService/UpdateUser",
        {"userId": me["userId"], "status": "USER_STATUS_DISABLED"},
        user="root",
    )
    assert disable_self.json()["code"] == "failed_precondition"


@pytest.mark.asyncio
async def test_disabled_user_is_rejected(client, session_factory):
    await make_admin(session_factory)
    bob_id = call(client, "AccountService/GetMe", user="bob").json()["userId"]

    response = call(
        client,
        "UserAdminService/UpdateUser",
        {"userId": bob_id, "status": "USER_STATUS_DISABLED"},
        user="root",
    )
    assert response.json()["user"]["status"] == "USER_STATUS_DISABLED"

    rejected = call(client, "AccountService/GetMe", user="bob")
    assert rejected.status_code == 403
    assert rejected.json()["message"] == "Account is disabled"


@pytest.mark.asyncio
async def test_admin_has_no_operational_or_pii_permissions(client, session_factory):
    await make_admin(session_factory)
    permissions = set(call(client, "AccountService/GetMe", user="root").json()["permissions"])

    assert not permissions & {"pii:reveal_all", "pii:reveal_assigned", "work_orders:approve"}
    submit = call(client, "IntakeService/SubmitIntake", {"text": "test"}, user="root")
    assert submit.status_code == 403


@pytest.mark.asyncio
async def test_concurrent_first_sign_ins_create_one_user(session_factory):
    async def sign_in():
        async with session_factory() as session:
            access = await UserDirectory(session).resolve_login(
                provider=IdentityProvider.DEVELOPMENT,
                external_id="racer",
                display_name=None,
                email=None,
                default_role="tenant",
            )
            return access.user.id

    ids = await asyncio.gather(*(sign_in() for _ in range(5)))
    assert len(set(ids)) == 1
