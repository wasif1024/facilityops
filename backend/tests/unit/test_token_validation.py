"""Token validation for staff (workforce Entra ID) and residents (Entra External ID).

Tokens are really signed with a local RSA key; only the discovery documents and the
key lookup are faked, so every check (signature, audience, issuer, expiry, tenant) runs.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.app.api.token_validation import TokenValidator, trusted_directories
from backend.app.core.config import Settings
from backend.app.domain.rbac import IdentityProvider

STAFF_TENANT = "11111111-1111-1111-1111-111111111111"
RESIDENT_TENANT = "22222222-2222-2222-2222-222222222222"
STAFF_API = "33333333-3333-3333-3333-333333333333"
RESIDENT_API = "44444444-4444-4444-4444-444444444444"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

SETTINGS = Settings(
    app_env="test",
    entra_tenant_id=STAFF_TENANT,
    entra_api_audience=STAFF_API,
    entra_external_tenant_id=RESIDENT_TENANT,
    entra_external_tenant_subdomain="facilityopsresidents",
    entra_external_api_audience=RESIDENT_API,
    default_user_role="tenant",
)

DISCOVERY = {
    f"https://login.microsoftonline.com/{STAFF_TENANT}/v2.0/.well-known/openid-configuration": {
        "issuer": f"https://login.microsoftonline.com/{STAFF_TENANT}/v2.0",
        "jwks_uri": "https://keys.example/staff",
    },
    f"https://facilityopsresidents.ciamlogin.com/{RESIDENT_TENANT}/v2.0/.well-known/openid-configuration": {
        "issuer": f"https://{RESIDENT_TENANT}.ciamlogin.com/{RESIDENT_TENANT}/v2.0",
        "jwks_uri": "https://keys.example/residents",
    },
}


def validator() -> TokenValidator:
    fetched: list[str] = []

    def fetch(url: str):
        fetched.append(url)
        return DISCOVERY[url]

    v = TokenValidator(trusted_directories(SETTINGS), fetch_json=fetch, signing_key=lambda uri, token: KEY.public_key())
    v.fetched = fetched  # type: ignore[attr-defined]
    return v


def token(*, tenant: str, audience: str, issuer: str, key=KEY, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "tid": tenant,
        "sub": "subject-1",
        "oid": "object-1",
        "iat": now,
        "exp": now + 600,
        "name": "Avery Synthetic",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


def staff_token(**kw) -> str:
    return token(tenant=STAFF_TENANT, audience=STAFF_API, issuer=f"https://login.microsoftonline.com/{STAFF_TENANT}/v2.0", **kw)


def resident_token(issuer: str | None = None, **kw) -> str:
    return token(
        tenant=RESIDENT_TENANT,
        audience=RESIDENT_API,
        issuer=issuer or f"https://{RESIDENT_TENANT}.ciamlogin.com/{RESIDENT_TENANT}/v2.0",
        **kw,
    )


def test_staff_token_maps_to_workforce_with_no_default_role():
    directory, claims = validator().validate(staff_token())

    assert directory.provider is IdentityProvider.ENTRA
    assert directory.default_role is None
    assert claims["oid"] == "object-1"


@pytest.mark.parametrize(
    "issuer",
    [
        f"https://{RESIDENT_TENANT}.ciamlogin.com/{RESIDENT_TENANT}/v2.0",
        # External ID tokens have also been seen with the workforce-style issuer.
        f"https://login.microsoftonline.com/{RESIDENT_TENANT}/v2.0",
    ],
)
def test_resident_token_maps_to_external_id_and_becomes_a_tenant(issuer):
    directory, _ = validator().validate(resident_token(issuer=issuer))

    assert directory.provider is IdentityProvider.ENTRA_EXTERNAL
    assert directory.default_role == "tenant"


@pytest.mark.parametrize(
    ("make_token", "why"),
    [
        (lambda: staff_token(aud="another-api"), "wrong audience"),
        (lambda: resident_token(aud=STAFF_API), "staff API audience on a resident token"),
        (lambda: staff_token(iss="https://evil.example/v2.0"), "wrong issuer"),
        (lambda: staff_token(exp=int(time.time()) - 60), "expired"),
        (lambda: staff_token(key=OTHER_KEY), "signed with an unknown key"),
        (lambda: token(tenant="99999999-0000-0000-0000-000000000000", audience=STAFF_API,
                       issuer="https://login.microsoftonline.com/99999999-0000-0000-0000-000000000000/v2.0"),
         "untrusted directory"),
    ],
)
def test_rejected_tokens(make_token, why):
    with pytest.raises(jwt.PyJWTError):
        validator().validate(make_token())


def test_a_resident_token_cannot_pose_as_staff():
    # Claims to be from the staff tenant but carries the resident issuer.
    forged = token(
        tenant=STAFF_TENANT,
        audience=STAFF_API,
        issuer=f"https://{RESIDENT_TENANT}.ciamlogin.com/{RESIDENT_TENANT}/v2.0",
    )
    with pytest.raises(jwt.InvalidIssuerError):
        validator().validate(forged)


def test_discovery_documents_are_cached():
    v = validator()
    v.validate(staff_token())
    v.validate(staff_token())

    assert len(v.fetched) == 1  # type: ignore[attr-defined]


def test_only_configured_directories_are_trusted():
    staff_only = Settings(app_env="test", entra_tenant_id=STAFF_TENANT, entra_api_audience=STAFF_API)

    assert [d.provider for d in trusted_directories(staff_only)] == [IdentityProvider.ENTRA]
    assert trusted_directories(Settings(app_env="test")) == []
