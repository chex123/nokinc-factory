"""Native provider adapters behind the provider-neutral ModelPort."""

from __future__ import annotations

import json
from collections.abc import Mapping
from email.message import Message
from typing import IO, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from nokinc_factory.ports.model import ModelPort, ModelRequest, ModelResponse, ModelStatus


class ModelProviderError(RuntimeError):
    """Raised when a native model provider cannot produce a safe response."""

    def __init__(self, message: str, *, diagnostic_code: str = "MODEL_PROVIDER_ERROR") -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code


class ModelSecretStore(Protocol):
    def get(self, secret_ref: str) -> str: ...


class ModelHttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, bytes]: ...


class BedrockConverseClient(Protocol):
    def converse(self, **kwargs: object) -> object: ...


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


class UrllibModelHttpTransport:
    """Minimal HTTPS transport that never forwards credentials on redirects."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        if timeout <= 0:
            raise ValueError("model request timeout must be positive")
        self._timeout = timeout
        self._opener = build_opener(_NoRedirectHandler())

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, bytes]:
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return response.status, response.read()
        except HTTPError as error:
            try:
                return error.code, error.read()
            except OSError as exc:
                raise ModelProviderError(
                    "model provider returned an unreadable error",
                    diagnostic_code="HTTP_BODY_UNREADABLE",
                ) from exc
        except (URLError, OSError) as exc:
            raise ModelProviderError(
                "model provider request failed",
                diagnostic_code="NETWORK_ERROR",
            ) from exc


class _CredentialModelPort(ModelPort):
    def __init__(
        self,
        *,
        model: str,
        family: str,
        credential_ref: str,
        secrets: ModelSecretStore,
        transport: ModelHttpTransport | None,
    ) -> None:
        self._model = model
        self._family = family
        self._credential_ref = credential_ref
        self._secrets = secrets
        self._transport = transport or UrllibModelHttpTransport()

    def _api_key(self) -> str:
        try:
            secret_text = self._secrets.get(self._credential_ref)
        except Exception as exc:
            raise ModelProviderError(
                "model credential retrieval failed",
                diagnostic_code="MODEL_CREDENTIAL_RETRIEVAL_FAILED",
            ) from exc
        try:
            decoded: object = json.loads(secret_text)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ModelProviderError(
                "model credential is not valid JSON",
                diagnostic_code="MODEL_CREDENTIAL_INVALID_JSON",
            ) from exc
        api_key_value = decoded.get("api_key") if isinstance(decoded, dict) else None
        if not isinstance(api_key_value, str):
            raise ModelProviderError(
                "model credential must contain an api_key",
                diagnostic_code="MODEL_CREDENTIAL_KEY_MISSING",
            )
        api_key = api_key_value.strip()
        if not api_key:
            raise ModelProviderError(
                "model credential api_key must not be empty",
                diagnostic_code="MODEL_CREDENTIAL_KEY_EMPTY",
            )
        return api_key

    def _response_payload(self, status: int, body: bytes) -> dict[str, object]:
        if status < 200 or status >= 300:
            if status in (401, 403):
                diagnostic_code = "HTTP_AUTHORIZATION"
            elif status == 429:
                diagnostic_code = "HTTP_THROTTLED"
            elif status >= 500:
                diagnostic_code = "HTTP_UPSTREAM"
            else:
                diagnostic_code = "HTTP_REJECTED"
            raise ModelProviderError(
                "model provider request failed",
                diagnostic_code=diagnostic_code,
            )
        try:
            decoded: object = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ModelProviderError(
                "model provider returned invalid JSON",
                diagnostic_code="HTTP_INVALID_JSON",
            ) from exc
        if not isinstance(decoded, dict):
            raise ModelProviderError(
                "model provider returned an invalid response",
                diagnostic_code="HTTP_INVALID_SHAPE",
            )
        return cast(dict[str, object], decoded)

    def _response(
        self,
        *,
        output: str,
        provider_execution_id: object,
    ) -> ModelResponse:
        if not output.strip():
            raise ModelProviderError("model provider returned empty output")
        execution_id = provider_execution_id if isinstance(provider_execution_id, str) else None
        return ModelResponse(
            status=ModelStatus.COMPLETED,
            model=self._model,
            family=self._family,
            output=output,
            provider_execution_id=execution_id,
        )


class OpenAIModelPort(_CredentialModelPort):
    def __init__(
        self,
        *,
        model: str,
        family: str,
        credential_ref: str,
        secrets: ModelSecretStore,
        transport: ModelHttpTransport | None = None,
        api_url: str = "https://api.openai.com/v1",
        max_output_tokens: int = 256,
    ) -> None:
        self._api_url = _validate_api_url(api_url, subject="OpenAI api_url")
        self._max_output_tokens = _validate_max_output_tokens(max_output_tokens)
        super().__init__(
            model=model,
            family=family,
            credential_ref=credential_ref,
            secrets=secrets,
            transport=transport,
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        response_status, response_body = self._transport.request(
            "POST",
            f"{self._api_url}/responses",
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key()}",
                "Content-Type": "application/json",
                "User-Agent": "nokinc-factory",
            },
            json.dumps({
                "model": self._model,
                "input": request.prompt,
                "max_output_tokens": self._max_output_tokens,
            }).encode("utf-8"),
        )
        payload = self._response_payload(response_status, response_body)
        output = _openai_output(payload)
        return self._response(output=output, provider_execution_id=payload.get("id"))


class GoogleGeminiModelPort(_CredentialModelPort):
    def __init__(
        self,
        *,
        model: str,
        family: str,
        credential_ref: str,
        secrets: ModelSecretStore,
        transport: ModelHttpTransport | None = None,
        api_url: str = "https://generativelanguage.googleapis.com/v1beta",
        max_output_tokens: int = 256,
    ) -> None:
        self._api_url = _validate_api_url(api_url, subject="Google api_url")
        self._max_output_tokens = _validate_max_output_tokens(max_output_tokens)
        super().__init__(
            model=model,
            family=family,
            credential_ref=credential_ref,
            secrets=secrets,
            transport=transport,
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        response_status, response_body = self._transport.request(
            "POST",
            f"{self._api_url}/models/{quote(self._model, safe='')}:generateContent",
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "nokinc-factory",
                "x-goog-api-key": self._api_key(),
            },
            json.dumps({
                "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
                "generationConfig": {"maxOutputTokens": self._max_output_tokens},
            }).encode("utf-8"),
        )
        payload = self._response_payload(response_status, response_body)
        output = _google_output(payload)
        return self._response(output=output, provider_execution_id=payload.get("responseId"))


class BedrockModelPort(ModelPort):
    def __init__(
        self,
        *,
        model: str,
        family: str,
        client: BedrockConverseClient | None = None,
        region: str | None = None,
        max_output_tokens: int = 256,
    ) -> None:
        self._model = model
        self._family = family
        self._client = client or _bedrock_client(region)
        self._max_output_tokens = _validate_max_output_tokens(max_output_tokens)

    def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            raw_response = self._client.converse(
                modelId=self._model,
                messages=[{
                    "role": "user",
                    "content": [{"text": request.prompt}],
                }],
                inferenceConfig={"maxTokens": self._max_output_tokens},
            )
        except Exception as exc:
            raise ModelProviderError(
                "Bedrock model request failed",
                diagnostic_code=_bedrock_diagnostic_code(exc),
            ) from exc
        if not isinstance(raw_response, dict):
            raise ModelProviderError("Bedrock returned an invalid response")
        response = cast(dict[str, object], raw_response)
        output = _bedrock_output(response)
        metadata = response.get("ResponseMetadata")
        execution_id = None
        if isinstance(metadata, dict) and isinstance(metadata.get("RequestId"), str):
            execution_id = metadata["RequestId"]
        if not output.strip():
            raise ModelProviderError("Bedrock returned empty output")
        return ModelResponse(
            status=ModelStatus.COMPLETED,
            model=self._model,
            family=self._family,
            output=output,
            provider_execution_id=execution_id,
        )


def _validate_api_url(api_url: str, *, subject: str) -> str:
    try:
        parts = urlsplit(api_url)
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"{subject} has an invalid port") from exc
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise ValueError(f"{subject} must use HTTPS with a hostname")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{subject} must not include embedded credentials")
    if parts.query or parts.fragment or port == 0:
        raise ValueError(f"{subject} must not contain query, fragment, or port 0")
    return api_url.rstrip("/")


def _bedrock_diagnostic_code(error: Exception) -> str:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return "BEDROCK_REQUEST_FAILED"
    details = response.get("Error")
    if not isinstance(details, Mapping):
        return "BEDROCK_REQUEST_FAILED"
    code = details.get("Code")
    if not isinstance(code, str):
        return "BEDROCK_REQUEST_FAILED"
    return {
        "AccessDeniedException": "BEDROCK_ACCESS_DENIED",
        "ExpiredTokenException": "BEDROCK_ACCESS_DENIED",
        "UnrecognizedClientException": "BEDROCK_ACCESS_DENIED",
        "ThrottlingException": "BEDROCK_THROTTLED",
        "ModelNotReadyException": "BEDROCK_MODEL_NOT_READY",
        "ValidationException": "BEDROCK_INVALID_REQUEST",
        "ResourceNotFoundException": "BEDROCK_MODEL_UNAVAILABLE",
    }.get(code, "BEDROCK_REQUEST_FAILED")


def _validate_max_output_tokens(max_output_tokens: int) -> int:
    if isinstance(max_output_tokens, bool) or max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be a positive integer")
    return max_output_tokens


def _openai_output(payload: Mapping[str, object]) -> str:
    direct_output = payload.get("output_text")
    if isinstance(direct_output, str):
        return direct_output
    output_items = payload.get("output")
    if not isinstance(output_items, list):
        raise ModelProviderError("OpenAI returned an invalid response")
    text_parts: list[str] = []
    for item in output_items:
        if not isinstance(item, dict) or not isinstance(item.get("content"), list):
            continue
        for content in item["content"]:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                text_parts.append(content["text"])
    if not text_parts:
        raise ModelProviderError("OpenAI returned an invalid response")
    return "".join(text_parts)


def _google_output(payload: Mapping[str, object]) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ModelProviderError("Google returned an invalid response")
    first_candidate = candidates[0]
    if not isinstance(first_candidate, dict):
        raise ModelProviderError("Google returned an invalid response")
    content = first_candidate.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
        raise ModelProviderError("Google returned an invalid response")
    text_parts = [
        part["text"]
        for part in content["parts"]
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    if not text_parts:
        raise ModelProviderError("Google returned an invalid response")
    return "".join(text_parts)


def _bedrock_output(payload: Mapping[str, object]) -> str:
    output = payload.get("output")
    if not isinstance(output, dict):
        raise ModelProviderError("Bedrock returned an invalid response")
    message = output.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        raise ModelProviderError("Bedrock returned an invalid response")
    text_parts = [
        content["text"]
        for content in message["content"]
        if isinstance(content, dict) and isinstance(content.get("text"), str)
    ]
    if not text_parts:
        raise ModelProviderError("Bedrock returned an invalid response")
    return "".join(text_parts)


def _bedrock_client(region: str | None) -> BedrockConverseClient:
    try:
        import boto3  # type: ignore[import-untyped]

        client: object = boto3.client("bedrock-runtime", region_name=region)
    except Exception as exc:
        raise ModelProviderError("Bedrock runtime is unavailable") from exc
    return cast(BedrockConverseClient, client)


__all__ = [
    "BedrockModelPort",
    "GoogleGeminiModelPort",
    "ModelHttpTransport",
    "ModelProviderError",
    "ModelSecretStore",
    "OpenAIModelPort",
    "UrllibModelHttpTransport",
]