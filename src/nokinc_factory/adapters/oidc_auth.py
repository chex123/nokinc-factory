"""OIDC/JWKS principal verification for staging and production runtimes."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient
from pydantic import ValidationError

from nokinc_factory.application.service import Principal
from nokinc_factory.domain.review_base import utc


class SigningKey(Protocol):
    key: Any


class JwksResolver(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> SigningKey: ...


class OidcPrincipalVerifier:
    """Verify a signed RS256 bearer token and map trusted claims to Principal.

    Tenant membership and application roles must be asserted by the configured
    identity provider in the named claims. The API never trusts request-body
    tenant or role values.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        tenant_claim: str = "tenant_id",
        roles_claim: str = "roles",
        jwks_client: JwksResolver | None = None,
    ) -> None:
        issuer_parts = urlsplit(issuer)
        if (
            issuer_parts.scheme != "https"
            or not issuer_parts.hostname
            or issuer_parts.username is not None
            or issuer_parts.password is not None
            or issuer_parts.query
            or issuer_parts.fragment
            or not audience.strip()
        ):
            raise ValueError("OIDC issuer must use HTTPS and audience must be set")
        parts = urlsplit(jwks_url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError("OIDC JWKS URL must be an HTTPS URL without credentials or query")
        if not tenant_claim.strip() or not roles_claim.strip():
            raise ValueError("OIDC tenant and roles claim names must be set")
        self.issuer = issuer
        self.audience = audience
        self._tenant_claim = tenant_claim
        self._roles_claim = roles_claim
        self._jwks: JwksResolver = jwks_client or PyJWKClient(
            jwks_url, cache_jwk_set=True, cache_keys=True,
        )

    def verify(
        self,
        token: str,
        *,
        now: datetime,
        expected_nonce: str | None = None,
    ) -> Principal:
        if not isinstance(token, str) or not token or len(token) > 32_768:
            raise ValueError("invalid OIDC token")
        current = utc(now)
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                cast(Any, signing_key.key),
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": ["iss", "aud", "sub", "iat", "exp"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except Exception:
            raise ValueError("invalid OIDC token") from None

        if expected_nonce is not None:
            nonce = claims.get("nonce")
            if not isinstance(nonce, str) or not hmac.compare_digest(nonce, expected_nonce):
                raise ValueError("invalid OIDC token")

        issued = claims.get("iat")
        expires = claims.get("exp")
        not_before = claims.get("nbf", issued)
        subject = claims.get("sub")
        tenant_id = claims.get(self._tenant_claim)
        roles = claims.get(self._roles_claim)
        if (
            isinstance(issued, bool)
            or not isinstance(issued, int)
            or isinstance(expires, bool)
            or not isinstance(expires, int)
            or not isinstance(subject, str)
            or not isinstance(tenant_id, str)
            or not isinstance(roles, list)
            or any(not isinstance(role, str) for role in roles)
        ):
            raise ValueError("OIDC identity claims are invalid")
        if isinstance(not_before, bool) or not isinstance(not_before, int):
            raise ValueError("OIDC identity claims are invalid")
        current_timestamp = int(current.timestamp())
        if (
            issued > current_timestamp
            or not_before > current_timestamp
            or expires <= current_timestamp
        ):
            raise ValueError("OIDC token is outside its validity window")
        try:
            issued_at = datetime.fromtimestamp(issued, UTC)
            expires_at = datetime.fromtimestamp(expires, UTC)
            return Principal.model_validate({
                "tenant_id": tenant_id,
                "subject_id": subject,
                "issuer": self.issuer,
                "audience": self.audience,
                "roles": tuple(roles),
                "issued_at": issued_at,
                "expires_at": expires_at,
            })
        except (OverflowError, OSError, ValidationError, ValueError):
            raise ValueError("OIDC identity claims are invalid") from None


__all__ = ["JwksResolver", "OidcPrincipalVerifier", "SigningKey"]