import pytest

from backend.app.services.demo_accounts import DEMO_ACCOUNTS, add_demo_accounts, remove_demo_accounts
from backend.app.services.users import UserDirectoryError

from .conftest import call
from .test_users_rbac import make_admin


@pytest.mark.asyncio
async def test_demo_accounts_sign_in_with_their_roles(client, session_factory):
    async with session_factory() as session:
        await add_demo_accounts(session)

    for account in DEMO_ACCOUNTS:
        me = call(client, "AccountService/GetMe", user=account.external_id).json()
        assert me["roles"] == [account.role]


@pytest.mark.asyncio
async def test_removing_demo_accounts_keeps_an_administrator(client, session_factory):
    async with session_factory() as session:
        await add_demo_accounts(session)
        with pytest.raises(UserDirectoryError, match="No other active administrator"):
            await remove_demo_accounts(session)

    await make_admin(session_factory, "real-admin")
    async with session_factory() as session:
        removed = await remove_demo_accounts(session)
    assert sorted(removed) == sorted(account.external_id for account in DEMO_ACCOUNTS)

    # A removed demo name signing in again is just a new tenant, with none of the old roles.
    assert call(client, "AccountService/GetMe", user="local-developer").json()["roles"] == ["tenant"]
