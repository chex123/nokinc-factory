from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from nokinc_factory.adapters.cognito_login import (
    CognitoHostedLogin,
    CognitoLoginSettings,
)
from nokinc_factory.application.service import Principal, create_app

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
CALLBACK_URL = "https://factory.nokinc.com/auth/callback"
PUBLIC_ORIGIN = "https://factory.nokinc.com"
ID_TOKEN = "verified-id-token"


class FakeVerifier:
    def __init__(self) -> None:
        self.expected_nonce: str | None = None

    def verify(
        self,
        token: str,
        *,
        now: datetime,
        expected_nonce: str | None = None,
    ) -> Principal:
        assert token == ID_TOKEN
        self.expected_nonce = expected_nonce
        return Principal(
            tenant_id="tenant-blue",
            subject_id="user-42",
            issuer="https://cognito-idp.us-east-1.amazonaws.com/pool-id",
            audience="client-id",
            roles=("operator",),
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=15),
        )


class FakeTokenClient:
    def __init__(self) -> None:
        self.exchange: dict[str, str] | None = None

    def exchange_code(
        self,
        *,
        token_endpoint_url: str,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> str:
        self.exchange = {
            "token_endpoint_url": token_endpoint_url,
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        return ID_TOKEN


def _login() -> tuple[CognitoHostedLogin, FakeVerifier, FakeTokenClient]:
    verifier = FakeVerifier()
    token_client = FakeTokenClient()
    login = CognitoHostedLogin(
        settings=CognitoLoginSettings(
            hosted_ui_base_url=(
                "https://nokinc-factory-pilot.auth.us-east-1.amazoncognito.com"
            ),
            client_id="client-id",
            callback_url=CALLBACK_URL,
            logout_url=f"{PUBLIC_ORIGIN}/auth/signed-out",
        ),
        principal_verifier=verifier,
        token_client=token_client,
    )
    return login, verifier, token_client


def test_cognito_login_uses_authorization_code_pkce_and_nonce() -> None:
    login, _, _ = _login()

    attempt = login.start()
    parsed = urlsplit(attempt.authorization_url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.path == "/oauth2/authorize"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == [CALLBACK_URL]
    assert query["scope"] == ["openid email profile"]
    assert query["state"] == [attempt.state]
    assert query["nonce"] == [attempt.nonce]
    assert query["code_challenge_method"] == ["S256"]
    assert len(attempt.code_verifier) >= 43


def test_cognito_login_rejects_insecure_redirect_urls() -> None:
    with pytest.raises(ValueError):
        CognitoLoginSettings(
            hosted_ui_base_url="http://login.example.test",
            client_id="client-id",
            callback_url="http://factory.example.test/auth/callback",
            logout_url="https://factory.example.test/auth/signed-out",
        )


def test_callback_validates_state_and_sets_http_only_session_cookie() -> None:
    login, verifier, token_client = _login()
    app = create_app(
        principal_verifier=verifier,
        browser_login=login,
        cookie_auth_origin=PUBLIC_ORIGIN,
        clock=lambda: NOW,
    )

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        started = client.get("/auth/login", follow_redirects=False)
        query = parse_qs(urlsplit(started.headers["location"]).query)
        state = query["state"][0]
        code_verifier = client.cookies.get("__Host-factory_oidc_verifier")

        assert started.status_code == 302
        assert code_verifier is not None
        assert "httponly" in started.headers["set-cookie"].lower()
        assert "secure" in started.headers["set-cookie"].lower()

        callback = client.get(
            "/auth/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )

        assert callback.status_code == 303
        assert callback.headers["location"] == "/chat"
        assert verifier.expected_nonce == query["nonce"][0]
        assert token_client.exchange is not None
        assert token_client.exchange["code_verifier"] == code_verifier
        assert client.cookies.get("__Host-factory_oidc_state") is None
        assert client.cookies.get("__Host-factory_id_token") == ID_TOKEN
        assert "httponly" in callback.headers["set-cookie"].lower()
        assert "secure" in callback.headers["set-cookie"].lower()
        assert client.get("/v1/status").status_code == 200
        landing = client.get("/chat")
        assert landing.status_code == 200
        assert landing.headers["content-type"].startswith("text/html")
        assert landing.headers["cache-control"] == "no-store"
        assert 'id="message"' in landing.text
        assert 'id="chat-form"' in landing.text
        assert client.get("/").url.path == "/chat"


def test_cookie_auth_requires_same_origin_for_mutating_requests() -> None:
    login, verifier, _ = _login()
    app = create_app(
        principal_verifier=verifier,
        browser_login=login,
        cookie_auth_origin=PUBLIC_ORIGIN,
        clock=lambda: NOW,
    )

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        started = client.get("/auth/login", follow_redirects=False)
        state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
        client.get(
            "/auth/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )

        body = {"message": "inspect Flur app"}
        assert client.post("/v1/chat", json=body).status_code == 403
        assert client.post(
            "/v1/chat", json=body, headers={"Origin": PUBLIC_ORIGIN}
        ).status_code == 202


def test_logout_clears_the_session_cookie_and_redirects_to_cognito() -> None:
    login, verifier, _ = _login()
    app = create_app(
        principal_verifier=verifier,
        browser_login=login,
        cookie_auth_origin=PUBLIC_ORIGIN,
        clock=lambda: NOW,
    )

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        started = client.get("/auth/login", follow_redirects=False)
        state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
        client.get(
            "/auth/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )

        response = client.get("/auth/logout", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"].startswith(
            "https://nokinc-factory-pilot.auth.us-east-1.amazoncognito.com/logout?"
        )
        assert client.cookies.get("__Host-factory_id_token") is None
        assert client.get("/v1/status").status_code == 401


def test_callback_rejects_state_mismatch_without_exchanging_code() -> None:
    login, verifier, token_client = _login()
    app = create_app(
        principal_verifier=verifier,
        browser_login=login,
        cookie_auth_origin=PUBLIC_ORIGIN,
        clock=lambda: NOW,
    )

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        client.get("/auth/login", follow_redirects=False)
        response = client.get(
            "/auth/callback",
            params={"code": "authorization-code", "state": "wrong-state"},
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert token_client.exchange is None