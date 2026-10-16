"""User directory: provisioning, role assignment and the security audit trail."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    PermissionRecord,
    RolePermissionRecord,
    RoleRecord,
    SecurityAuditEvent,
    UserRecord,
    UserRoleRecord,
)
from backend.app.domain.rbac import IdentityProvider, Permission, SystemRole, UserStatus

_LAST_LOGIN_RESOLUTION = timedelta(minutes=15)


class UserDirectoryError(Exception):
    """A user-management rule was violated. The message is safe to show to admins."""


class UserNotFound(UserDirectoryError):
    pass


class UserAlreadyExists(UserDirectoryError):
    pass


@dataclass(frozen=True)
class UserAccess:
    user: UserRecord
    roles: frozenset[str]
    permissions: frozenset[Permission]


@dataclass(frozen=True)
class RoleDescription:
    id: str
    display_name: str
    description: str
    is_system: bool
    permissions: tuple[str, ...]


class UserDirectory:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_login(
        self,
        *,
        provider: IdentityProvider,
        external_id: str,
        display_name: str | None,
        email: str | None,
        default_role: str | None,
    ) -> UserAccess:
        """Return the caller's access, creating the user on first sign-in."""
        user = await self._find(provider, external_id)
        now = datetime.now(UTC)
        if user is None:
            try:
                user = await self._provision(provider, external_id, display_name, email, now)
                if default_role:
                    await self._grant(user.id, [default_role], granted_by=None)
                self._audit(
                    actor=user.id,
                    action="user.provisioned",
                    target_id=user.id,
                    details={"provider": provider.value, "default_role": default_role},
                )
                await self._session.commit()
            except IntegrityError:
                # Parallel first requests from the same new user: the other one won.
                await self._session.rollback()
                user = await self._find(provider, external_id)
                if user is None:
                    raise
        elif user.last_login_at is None or now - user.last_login_at > _LAST_LOGIN_RESOLUTION:
            user.last_login_at = now
            if display_name and not user.display_name:
                user.display_name = display_name
            await self._session.commit()
        return await self.access_for(user)

    async def access_for(self, user: UserRecord) -> UserAccess:
        role_rows = await self._session.scalars(
            select(UserRoleRecord.role_id).where(UserRoleRecord.user_id == user.id)
        )
        roles = frozenset(role_rows)
        permission_rows = await self._session.scalars(
            select(RolePermissionRecord.permission_id)
            .where(RolePermissionRecord.role_id.in_(roles))
            .distinct()
        )
        known = {permission.value for permission in Permission}
        permissions = frozenset(Permission(p) for p in permission_rows if p in known)
        return UserAccess(user=user, roles=roles, permissions=permissions)

    async def get(self, user_id: UUID) -> UserAccess:
        user = await self._session.get(UserRecord, user_id)
        if user is None:
            raise UserNotFound("User not found")
        return await self.access_for(user)

    async def list_users(
        self,
        *,
        role: str | None = None,
        status: UserStatus | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[UserAccess], int]:
        query: Select[tuple[UserRecord]] = select(UserRecord)
        if role:
            query = query.where(
                UserRecord.id.in_(
                    select(UserRoleRecord.user_id).where(UserRoleRecord.role_id == role)
                )
            )
        if status:
            query = query.where(UserRecord.status == status.value)
        if search:
            pattern = f"%{search.strip()}%"
            query = query.where(
                UserRecord.display_name.ilike(pattern) | UserRecord.email.ilike(pattern)
            )
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        users = await self._session.scalars(
            query.order_by(UserRecord.created_at.desc()).limit(limit).offset(offset)
        )
        return [await self.access_for(user) for user in users], int(total or 0)

    async def create(
        self,
        *,
        actor_id: UUID,
        provider: IdentityProvider,
        external_id: str,
        display_name: str | None,
        email: str | None,
        roles: list[str],
    ) -> UserAccess:
        """Pre-provision a user (for example staff) before their first sign-in."""
        if await self._find(provider, external_id) is not None:
            raise UserAlreadyExists("A user with this identity already exists")
        await self._require_roles_exist(roles)
        user = await self._provision(provider, external_id, display_name, email, None)
        await self._grant(user.id, roles, granted_by=actor_id)
        self._audit(
            actor=actor_id,
            action="user.created",
            target_id=user.id,
            details={"provider": provider.value, "roles": sorted(roles)},
        )
        await self._session.commit()
        return await self.access_for(user)

    async def update(
        self,
        *,
        actor_id: UUID,
        user_id: UUID,
        display_name: str | None = None,
        status: UserStatus | None = None,
    ) -> UserAccess:
        access = await self.get(user_id)
        user = access.user
        changed: dict[str, Any] = {}
        if status is not None and status.value != user.status:
            if user_id == actor_id and status is UserStatus.DISABLED:
                raise UserDirectoryError("You cannot disable your own account")
            if status is UserStatus.DISABLED and SystemRole.ADMIN in access.roles:
                await self._require_another_admin(user_id)
            changed["status"] = {"from": user.status, "to": status.value}
            user.status = status.value
        if display_name is not None and display_name != user.display_name:
            changed["display_name"] = True
            user.display_name = display_name
        if changed:
            self._audit(actor=actor_id, action="user.updated", target_id=user.id, details=changed)
            await self._session.commit()
        return await self.access_for(user)

    async def set_roles(self, *, actor_id: UUID, user_id: UUID, roles: list[str]) -> UserAccess:
        access = await self.get(user_id)
        wanted = set(roles)
        await self._require_roles_exist(wanted)
        added = wanted - access.roles
        removed = access.roles - wanted
        if not added and not removed:
            return access
        if SystemRole.ADMIN.value in removed:
            await self._require_another_admin(user_id)

        if removed:
            await self._session.execute(
                delete(UserRoleRecord).where(
                    UserRoleRecord.user_id == user_id, UserRoleRecord.role_id.in_(removed)
                )
            )
        await self._grant(user_id, sorted(added), granted_by=actor_id)
        self._audit(
            actor=actor_id,
            action="user.roles_changed",
            target_id=user_id,
            details={"added": sorted(added), "removed": sorted(removed)},
        )
        await self._session.commit()
        return await self.access_for(access.user)

    async def grant_role(
        self,
        *,
        provider: IdentityProvider,
        external_id: str,
        role: str,
        display_name: str | None = None,
    ) -> UserAccess:
        """Bootstrap path for the CLI: create the user if needed and add one role."""
        await self._require_roles_exist([role])
        user = await self._find(provider, external_id)
        if user is None:
            user = await self._provision(provider, external_id, display_name, None, None)
        await self._grant(user.id, [role], granted_by=None)
        self._audit(
            actor=None,
            action="user.role_granted_by_cli",
            target_id=user.id,
            details={"role": role},
        )
        await self._session.commit()
        return await self.access_for(user)

    async def roles(self) -> list[RoleDescription]:
        roles = await self._session.scalars(select(RoleRecord).order_by(RoleRecord.id))
        mapping = await self._session.execute(
            select(RolePermissionRecord.role_id, RolePermissionRecord.permission_id).order_by(
                RolePermissionRecord.permission_id
            )
        )
        by_role: dict[str, list[str]] = {}
        for role_id, permission_id in mapping:
            by_role.setdefault(role_id, []).append(permission_id)
        return [
            RoleDescription(
                id=role.id,
                display_name=role.display_name,
                description=role.description,
                is_system=role.is_system,
                permissions=tuple(by_role.get(role.id, [])),
            )
            for role in roles
        ]

    async def permissions(self) -> list[PermissionRecord]:
        return list(await self._session.scalars(select(PermissionRecord).order_by(PermissionRecord.id)))

    async def audit_events(
        self, *, action: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[SecurityAuditEvent], int]:
        query = select(SecurityAuditEvent)
        if action:
            query = query.where(SecurityAuditEvent.action == action)
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        events = await self._session.scalars(
            query.order_by(SecurityAuditEvent.created_at.desc()).limit(limit).offset(offset)
        )
        return list(events), int(total or 0)

    async def _find(self, provider: IdentityProvider, external_id: str) -> UserRecord | None:
        return await self._session.scalar(
            select(UserRecord).where(
                UserRecord.identity_provider == provider.value,
                UserRecord.external_id == external_id,
            )
        )

    async def _provision(
        self,
        provider: IdentityProvider,
        external_id: str,
        display_name: str | None,
        email: str | None,
        last_login_at: datetime | None,
    ) -> UserRecord:
        user = UserRecord(
            id=uuid4(),
            identity_provider=provider.value,
            external_id=external_id,
            display_name=display_name,
            email=email,
            status=UserStatus.ACTIVE.value,
            last_login_at=last_login_at,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def _grant(self, user_id: UUID, roles: list[str], granted_by: UUID | None) -> None:
        if not roles:
            return
        await self._session.execute(
            insert(UserRoleRecord)
            .values(
                [
                    {"user_id": user_id, "role_id": role, "granted_by_user_id": granted_by}
                    for role in roles
                ]
            )
            .on_conflict_do_nothing()
        )

    async def _require_roles_exist(self, roles: list[str] | set[str]) -> None:
        if not roles:
            return
        found = set(await self._session.scalars(select(RoleRecord.id).where(RoleRecord.id.in_(roles))))
        unknown = sorted(set(roles) - found)
        if unknown:
            raise UserDirectoryError(f"Unknown role(s): {', '.join(unknown)}")

    async def _require_another_admin(self, user_id: UUID) -> None:
        """Refuse a change that would leave no active administrator."""
        remaining = await self._session.scalar(
            select(func.count())
            .select_from(UserRoleRecord)
            .join(UserRecord, UserRecord.id == UserRoleRecord.user_id)
            .where(
                UserRoleRecord.role_id == SystemRole.ADMIN.value,
                UserRecord.status == UserStatus.ACTIVE.value,
                UserRecord.id != user_id,
            )
        )
        if not remaining:
            raise UserDirectoryError("At least one active administrator must remain")

    def _audit(
        self,
        *,
        actor: UUID | None,
        action: str,
        target_id: UUID,
        details: dict[str, Any],
    ) -> None:
        self._session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=actor,
                action=action,
                target_type="user",
                target_id=str(target_id),
                details=details,
            )
        )
