"""Bearer-token validation for the Entra directories this API trusts.

Two directories can sign users in:

- Workforce Entra ID (staff): issuer https://login.microsoftonline.com/{tenant}/v2.0
- Entra External ID (residents): issuer https://{tenant}.ciamlogin.com/{tenant}/v2.0, and
  tokens have been seen carrying the login.microsoftonline.com form too, so both are accepted.

Each directory's issuer and signing keys come from its OpenID discovery document, not
hard-coded URLs. The token's unverified `tid` claim is used only to pick a directory; the
signature, audience, issuer, expiry and tenant are then all verified.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

from backend.app.core.config import Settings
from backend.app.domain.rbac import IdentityProvider

_METADATA_TTL_SECONDS = 24 * 60 * 60
_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "tid"]


@dataclass(frozen=True)
class TrustedDirectory:
    provider: IdentityProvider
    tenant_id: str
    audience: str
    discovery_url: str
    # Issuers accepted in addition to the one in the discovery document.
    extra_issuers: tuple[str, ...]
    # Role granted on a user's first sign-in from this directory (None: wait for an admin).
    default_role: str | None


@dataclass(frozen=True)
class _Metadata:
    issuer: str
    jwks_uri: str
    fetched_at: float


def trusted_directories(settings: Settings) -> list[TrustedDirectory]:
    directories: list[TrustedDirectory] = []
    if settings.entra_tenant_id and settings.entra_api_audience:
        tenant = settings.entra_tenant_id
        directories.append(
            TrustedDirectory(
                provider=IdentityProvider.ENTRA,
                tenant_id=tenant,
                audience=settings.entra_api_audience,
                discovery_url=f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration",
                extra_issuers=(),
                default_role=settings.entra_workforce_default_role or None,
            )
        )
    if (
        settings.entra_external_tenant_id
        and settings.entra_external_tenant_subdomain
        and settings.entra_external_api_audience
    ):
        tenant = settings.entra_external_tenant_id
        subdomain = settings.entra_external_tenant_subdomain
        directories.append(
            TrustedDirectory(
                provider=IdentityProvider.ENTRA_EXTERNAL,
                tenant_id=tenant,
                audience=settings.entra_external_api_audience,
                discovery_url=f"https://{subdomain}.ciamlogin.com/{tenant}/v2.0/.well-known/openid-configuration",
                extra_issuers=(
                    f"https://{tenant}.ciamlogin.com/{tenant}/v2.0",
                    f"https://login.microsoftonline.com/{tenant}/v2.0",
                ),
                default_role=settings.default_user_role or None,
            )
        )
    return directories


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - https only, fixed hosts
        return json.loads(response.read())


class TokenValidator:
    def __init__(
        self,
        directories: list[TrustedDirectory],
        fetch_json: Callable[[str], dict[str, Any]] = _fetch_json,
        signing_key: Callable[[str, str], Any] | None = None,
    ) -> None:
        self._directories = {d.tenant_id.lower(): d for d in directories}
        self._fetch_json = fetch_json
        self._signing_key = signing_key or self._jwks_signing_key
        self._metadata: dict[str, _Metadata] = {}
        self._jwks_clients: dict[str, PyJWKClient] = {}
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._directories)

    def validate(self, token: str) -> tuple[TrustedDirectory, dict[str, Any]]:
        unverified = jwt.decode(token, options={"verify_signature": False})
        directory = self._directories.get(str(unverified.get("tid", "")).lower())
        if directory is None:
            raise jwt.InvalidIssuerError("Token is from a directory this API does not trust")

        metadata = self._metadata_for(directory)
        claims = jwt.decode(
            token,
            self._signing_key(metadata.jwks_uri, token),
            algorithms=["RS256"],
            audience=directory.audience,
            issuer=[metadata.issuer, *directory.extra_issuers],
            options={"require": _REQUIRED_CLAIMS},
        )
        if str(claims["tid"]).lower() != directory.tenant_id.lower():
            raise jwt.InvalidIssuerError("Token tenant does not match its issuer")
        return directory, claims

    def _metadata_for(self, directory: TrustedDirectory) -> _Metadata:
        with self._lock:
            cached = self._metadata.get(directory.tenant_id)
            if cached and time.monotonic() - cached.fetched_at < _METADATA_TTL_SECONDS:
                return cached
        document = self._fetch_json(directory.discovery_url)
        metadata = _Metadata(document["issuer"], document["jwks_uri"], time.monotonic())
        with self._lock:
            self._metadata[directory.tenant_id] = metadata
        return metadata

    def _jwks_signing_key(self, jwks_uri: str, token: str) -> Any:
        with self._lock:
            client = self._jwks_clients.setdefault(jwks_uri, PyJWKClient(jwks_uri, cache_keys=True))
        return client.get_signing_key_from_jwt(token).key
