"""Cognito managed-login authorization-code flow with PKCE."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from typing import IO, Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nokinc_factory.application.service import Principal


class CognitoLoginSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hosted_ui_base_url: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    callback_url: str = Field(min_length=1)
    logout_url: str = Field(min_length=1)

    @model_validator(mode="after")
    def _require_https_origins_and_redirects(self) -> CognitoLoginSettings:
        hosted = urlsplit(self.hosted_ui_base_url)
        if (
            hosted.scheme != "https"
            or not hosted.hostname
            or hosted.username is not None
            or hosted.password is not None
            or hosted.path not in ("", "/")
            or hosted.query
            or hosted.fragment
        ):
            raise ValueError("Cognito Hosted UI base URL must be an HTTPS origin")
        for name, value in (("callback", self.callback_url), ("logout", self.logout_url)):
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"Cognito {name} URL must be a plain HTTPS URL")
        return self


@dataclass(frozen=True)
class CognitoLoginStart:
    authorization_url: str
    state: str
    nonce: str
    code_verifier: str


class CognitoTokenClient(Protocol):
    def exchange_code(
        self,
        *,
        token_endpoint_url: str,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> str: ...


class NonceAwarePrincipalVerifier(Protocol):
    def verify(
        self,
        token: str,
        *,
        now: datetime,
        expected_nonce: str | None = None,
    ) -> Principal: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        return None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Cognito token response field")
        result[key] = value
    return result


class UrllibCognitoTokenClient:
    """Exchange one authorization code without following redirects."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        if timeout <= 0:
            raise ValueError("Cognito token endpoint timeout must be positive")
        self._timeout = timeout
        self._opener = build_opener(_NoRedirectHandler())

    def exchange_code(
        self,
        *,
        token_endpoint_url: str,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> str:
        form = urlencode({
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }).encode("ascii")
        request = Request(
            token_endpoint_url,
            data=form,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "nokinc-factory",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("Cognito token response is too large")
            payload: Any = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (HTTPError, URLError, OSError, ValueError, UnicodeDecodeError):
            raise ValueError("Cognito authorization code exchange failed") from None
        if not isinstance(payload, dict):
            raise ValueError("Cognito authorization code exchange failed")
        id_token = payload.get("id_token")
        if not isinstance(id_token, str) or not id_token or len(id_token) > 32_768:
            raise ValueError("Cognito authorization code exchange failed")
        return id_token


class CognitoHostedLogin:
    def __init__(
        self,
        *,
        settings: CognitoLoginSettings,
        principal_verifier: NonceAwarePrincipalVerifier,
        token_client: CognitoTokenClient | None = None,
    ) -> None:
        self.settings = CognitoLoginSettings.model_validate(settings)
        self._principal_verifier = principal_verifier
        self._token_client = token_client or UrllibCognitoTokenClient()

    def start(self) -> CognitoLoginStart:
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        query = urlencode({
            "client_id": self.settings.client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": self.settings.callback_url,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        hosted_ui_base_url = self.settings.hosted_ui_base_url.rstrip("/")
        return CognitoLoginStart(
            authorization_url=f"{hosted_ui_base_url}/oauth2/authorize?{query}",
            state=state,
            nonce=nonce,
            code_verifier=verifier,
        )

    def exchange_code(self, *, code: str, code_verifier: str) -> str:
        if not code or len(code) > 8192 or not code_verifier or len(code_verifier) > 128:
            raise ValueError("invalid Cognito authorization response")
        endpoint = f"{self.settings.hosted_ui_base_url.rstrip('/')}/oauth2/token"
        return self._token_client.exchange_code(
            token_endpoint_url=endpoint,
            client_id=self.settings.client_id,
            code=code,
            redirect_uri=self.settings.callback_url,
            code_verifier=code_verifier,
        )

    def verify_id_token(
        self,
        token: str,
        *,
        now: datetime,
        expected_nonce: str,
    ) -> Principal:
        return self._principal_verifier.verify(
            token,
            now=now,
            expected_nonce=expected_nonce,
        )

    def logout_url(self) -> str:
        query = urlencode({
            "client_id": self.settings.client_id,
            "logout_uri": self.settings.logout_url,
        })
        return f"{self.settings.hosted_ui_base_url.rstrip('/')}/logout?{query}"