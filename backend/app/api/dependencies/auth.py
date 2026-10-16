from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from pydantic import BaseModel

from backend.app.core.config import Settings, get_settings


class FacilityRole(StrEnum):
    TENANT = "tenant"
    TECHNICIAN = "technician"
    FACILITY_MANAGER = "facility_manager"


class Principal(BaseModel):
    subject: str
    roles: frozenset[FacilityRole]


_ROLE_CLAIMS = {
    "FacilityOps.Tenant": FacilityRole.TENANT,
    "FacilityOps.Technician": FacilityRole.TECHNICIAN,
    "FacilityOps.FacilityManager": FacilityRole.FACILITY_MANAGER,
}
_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=8)
def _jwks_client(tenant_id: str) -> PyJWKClient:
    return PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    )


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


async def get_current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    if not settings.auth_enabled:
        if settings.app_env == "production":
            raise HTTPException(
                status_code=503, detail="Authentication is not configured"
            )
        client_host = request.client.host if request.client else "unknown"
        return Principal(
            subject=f"local-development:{client_host}",
            roles=frozenset(FacilityRole),
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = await run_in_threadpool(
            _validate_token, credentials.credentials, settings
        )
    except jwt.PyJWTError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity validation is unavailable",
        ) from error

    roles = frozenset(
        _ROLE_CLAIMS[claim]
        for claim in claims.get("roles", [])
        if claim in _ROLE_CLAIMS
    )
    if not roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Required app role missing"
        )

    return Principal(subject=claims["sub"], roles=roles)


def require_roles(*allowed_roles: FacilityRole):
    async def check_roles(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> Principal:
        if not principal.roles.intersection(allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role"
            )
        return principal

    return check_roles
