"""Demo accounts for local development: one per role, no passwords.

Migration 0004 creates them on every development database, so a fresh clone can be explored at
once. They are development identities (`X-Dev-User`), which the backend accepts only when
AUTH_ENABLED=false outside production. Anyone adopting this project should remove them:

    scripts/db.sh remove-demo-accounts
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import SecurityAuditEvent, UserRecord, UserRoleRecord
from backend.app.domain.rbac import IdentityProvider, SystemRole, UserStatus
from backend.app.services.users import UserDirectory, UserDirectoryError


@dataclass(frozen=True)
class DemoAccount:
    external_id: str
    display_name: str
    role: str


# Keep in step with migration 0004 (which has its own frozen copy).
DEMO_ACCOUNTS = (
    DemoAccount("alice", "Alice (demo tenant)", SystemRole.TENANT.value),
    DemoAccount("fm", "Fran Manager (demo)", SystemRole.FACILITY_MANAGER.value),
    DemoAccount("tech", "Taylor Tech (demo)", SystemRole.TECHNICIAN.value),
    DemoAccount("local-developer", "Local developer (demo admin)", SystemRole.ADMIN.value),
)
DEMO_IDS = tuple(account.external_id for account in DEMO_ACCOUNTS)


async def add_demo_accounts(session: AsyncSession) -> list[str]:
    """Create the demo accounts (or restore their roles). Returns "name: role" lines."""
    directory = UserDirectory(session)
    added = []
    for account in DEMO_ACCOUNTS:
        await directory.grant_role(
            provider=IdentityProvider.DEVELOPMENT,
            external_id=account.external_id,
            role=account.role,
            display_name=account.display_name,
        )
        added.append(f"{account.external_id}: {account.role}")
    return added


async def remove_demo_accounts(session: AsyncSession, *, force: bool = False) -> list[str]:
    """Delete the demo accounts. Their requests and work orders stay, without an author.

    Refuses when no other active administrator would remain, unless ``force`` is set:
    create your own administrator first (grant-role) so the system stays manageable.
    """
    demo_users = (
        await session.scalars(
            select(UserRecord).where(
                UserRecord.identity_provider == IdentityProvider.DEVELOPMENT.value,
                UserRecord.external_id.in_(DEMO_IDS),
            )
        )
    ).all()
    if not demo_users:
        return []

    other_admins = await session.scalar(
        select(func.count())
        .select_from(UserRoleRecord)
        .join(UserRecord, UserRecord.id == UserRoleRecord.user_id)
        .where(
            UserRoleRecord.role_id == SystemRole.ADMIN.value,
            UserRecord.status == UserStatus.ACTIVE.value,
            UserRecord.id.not_in([user.id for user in demo_users]),
        )
    )
    if not other_admins and not force:
        raise UserDirectoryError(
            "No other active administrator would remain. Create your own first, for example:\n"
            "  python -m backend.app.cli grant-role --external-id <your-entra-object-id> --role admin\n"
            "or re-run with --force to remove them anyway."
        )

    for user in demo_users:
        session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=None,
                action="user.demo_account_removed",
                target_type="user",
                target_id=str(user.id),
                details={},
            )
        )
    await session.execute(delete(UserRecord).where(UserRecord.id.in_([user.id for user in demo_users])))
    await session.commit()
    return [user.external_id for user in demo_users]
