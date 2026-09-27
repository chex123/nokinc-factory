from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from nokinc_factory.adapters.oidc_auth import OidcPrincipalVerifier

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
ISSUER = "https://identity.example.test/tenant-pool"
AUDIENCE = "nokinc-factory-api"


class StaticJwks:
    def __init__(self, public_key: object) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        assert token
        return SimpleNamespace(key=self.public_key)


@pytest.fixture
def oidc_keys() -> tuple[Any, Any]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _token(private_key: Any, claims: dict[str, object]) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


def _verifier(public_key: object) -> OidcPrincipalVerifier:
    return OidcPrincipalVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url="https://identity.example.test/.well-known/jwks.json",
        tenant_claim="tenant_id",
        roles_claim="factory_roles",
        jwks_client=StaticJwks(public_key),
    )


def _claims(**changes: object) -> dict[str, object]:
    return {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-42",
        "tenant_id": "tenant-blue",
        "factory_roles": ["operator"],
        "iat": int((NOW - timedelta(minutes=1)).timestamp()),
        "exp": int((NOW + timedelta(minutes=10)).timestamp()),
        **changes,
    }


def test_oidc_verifier_maps_verified_tenant_and_roles(oidc_keys: tuple[Any, Any]) -> None:
    private_key, public_key = oidc_keys
    token = _token(private_key, _claims())

    principal = _verifier(public_key).verify(token, now=NOW)

    assert principal.tenant_id == "tenant-blue"
    assert principal.subject_id == "user-42"
    assert principal.roles == ("operator",)
    assert principal.issuer == ISSUER
    assert principal.audience == AUDIENCE


def test_oidc_verifier_requires_the_login_nonce_when_supplied(
    oidc_keys: tuple[Any, Any],
) -> None:
    private_key, public_key = oidc_keys
    verifier = _verifier(public_key)
    token = _token(private_key, _claims(nonce="expected-login-nonce"))

    principal = verifier.verify(token, now=NOW, expected_nonce="expected-login-nonce")

    assert principal.subject_id == "user-42"
    with pytest.raises(ValueError, match="OIDC token"):
        verifier.verify(token, now=NOW, expected_nonce="different-login-nonce")


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://attacker.example.test"},
        {"aud": "another-application"},
        {"tenant_id": ""},
        {"factory_roles": ["owner"]},
        {"exp": int((NOW - timedelta(seconds=1)).timestamp())},
        {"iat": int((NOW + timedelta(seconds=1)).timestamp())},
    ],
)
def test_oidc_verifier_rejects_untrusted_or_invalid_claims(
    oidc_keys: tuple[Any, Any], claims: dict[str, object],
) -> None:
    private_key, public_key = oidc_keys
    token = _token(private_key, _claims(**claims))

    with pytest.raises(ValueError):
        _verifier(public_key).verify(token, now=NOW)


def test_oidc_verifier_rejects_signature_from_another_key(
    oidc_keys: tuple[Any, Any],
) -> None:
    private_key, _ = oidc_keys
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(private_key, _claims())

    with pytest.raises(ValueError, match="OIDC token"):
        _verifier(other_key.public_key()).verify(token, now=NOW)


@pytest.mark.parametrize(
    "jwks_url",
    [
        "http://identity.example.test/keys",
        "https://user:password@identity.example.test/keys",
        "https://identity.example.test/keys?redirect=http://evil.test",
    ],
)
def test_oidc_verifier_requires_a_plain_https_jwks_endpoint(jwks_url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        OidcPrincipalVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url=jwks_url,
            tenant_claim="tenant_id",
            roles_claim="factory_roles",
            jwks_client=StaticJwks(object()),
        )