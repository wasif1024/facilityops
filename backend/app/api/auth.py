"""Authentication (who is calling) and the authenticated principal.

Entra ID authenticates; the user directory in PostgreSQL authorizes. A first sign-in
creates the user with ``DEFAULT_USER_ROLE``; administrators grant everything else.
Framework-agnostic: works on request headers, raises ``ConnectError``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextvars import ContextVar
from functools import lru_cache
from typing import Any
from uuid import UUID

import jwt
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from jwt import PyJWKClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool

from backend.app.core.config import Settings
from backend.app.domain.rbac import IdentityProvider, Permission, UserStatus
from backend.app.services.users import UserDirectory

DEV_USER_HEADER = "X-Dev-User"
_DEV_USER_PATTERN = re.compile(r"^[a-z0-9._-]{1,64}$")
_DEFAULT_DEV_USER = "local-developer"


class Principal(BaseModel):
    user_id: UUID
    subject: str
    display_name: str | None = None
    roles: frozenset[str]
    permissions: frozenset[Permission]


class Identity(BaseModel):
    provider: IdentityProvider
    external_id: str
    display_name: str | None = None
    email: str | None = None


_current_principal: ContextVar[Principal | None] = ContextVar("current_principal", default=None)


def current_principal() -> Principal:
    """The caller of the RPC being handled. Set by ``AuthInterceptor``."""
    principal = _current_principal.get()
    if principal is None:
        raise ConnectError(Code.UNAUTHENTICATED, "Not authenticated")
    return principal


@lru_cache(maxsize=8)
def _jwks_client(tenant_id: str) -> PyJWKClient:
    return PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys")


def _validate_token(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.entra_tenant_id or not settings.entra_api_audience:
        raise RuntimeError("Entra token validation is not configured")

    signing_key = _jwks_client(settings.entra_tenant_id).get_signing_key_from_jwt(token)
    issuer = f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0"
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.entra_api_audience,
        issuer=issuer,
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )


async def authenticate(headers: Mapping[str, str], settings: Settings) -> Identity:
    if not settings.auth_enabled:
        if settings.app_env == "production":
            raise ConnectError(Code.UNAVAILABLE, "Authentication is not configured")
        # Local development: pick an identity with a header to try different users.
        dev_user = (headers.get(DEV_USER_HEADER) or _DEFAULT_DEV_USER).strip().lower()
        if not _DEV_USER_PATTERN.fullmatch(dev_user):
            raise ConnectError(
                Code.INVALID_ARGUMENT,
                f"{DEV_USER_HEADER} must match {_DEV_USER_PATTERN.pattern}",
            )
        return Identity(
            provider=IdentityProvider.DEVELOPMENT, external_id=dev_user, display_name=dev_user
        )

    scheme, _, token = (headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ConnectError(Code.UNAUTHENTICATED, "Bearer token required")
    try:
        claims = await run_in_threadpool(_validate_token, token, settings)
    except jwt.PyJWTError as error:
        raise ConnectError(Code.UNAUTHENTICATED, "Invalid bearer token") from error
    except Exception as error:
        raise ConnectError(Code.UNAVAILABLE, "Identity validation is unavailable") from error

    # oid is the user's stable object id in the tenant; sub is per-application.
    return Identity(
        provider=IdentityProvider.ENTRA,
        external_id=str(claims.get("oid") or claims["sub"]),
        display_name=claims.get("name"),
        email=claims.get("email") or claims.get("preferred_username"),
    )


async def resolve_principal(
    identity: Identity,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> Principal:
    try:
        async with session_factory() as session:
            access = await UserDirectory(session).resolve_login(
                provider=identity.provider,
                external_id=identity.external_id,
                display_name=identity.display_name,
                email=identity.email,
                default_role=settings.default_user_role,
            )
    except Exception as error:
        raise ConnectError(Code.UNAVAILABLE, "User directory is unavailable") from error

    if access.user.status != UserStatus.ACTIVE.value:
        raise ConnectError(Code.PERMISSION_DENIED, "Account is disabled")

    return Principal(
        user_id=access.user.id,
        subject=identity.external_id,
        display_name=access.user.display_name,
        roles=access.roles,
        permissions=access.permissions,
    )
