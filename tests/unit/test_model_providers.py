import json
from typing import Any

import pytest

from nokinc_factory.ports.model import ModelRequest, ModelStatus

REQUEST = ModelRequest(
    role="reviewer",
    prompt="Review this synthetic change.",
    context_digest="sha256:" + "1" * 64,
)


class MemorySecrets:
    def __init__(self, value: str) -> None:
        self.value = value
        self.references: list[str] = []

    def get(self, secret_ref: str) -> str:
        self.references.append(secret_ref)
        return self.value


class CapturingTransport:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, bytes]:
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.status, json.dumps(self.payload).encode("utf-8")


class CapturingBedrockClient:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        return {
            "output": {"message": {"content": [{"text": "Bedrock review complete."}]}},
            "usage": {
                "inputTokens": 100,
                "outputTokens": 20,
                "totalTokens": 120,
                "cacheReadInputTokens": 40,
                "cacheWriteInputTokens": 10,
            },
            "ResponseMetadata": {"RequestId": "bedrock-request-1"},
        }


def test_openai_model_port_uses_secret_and_maps_responses_output() -> None:
    from nokinc_factory.adapters.model_providers import OpenAIModelPort

    secrets = MemorySecrets('{"api_key":"openai-test-key"}')
    transport = CapturingTransport(
        {
            "id": "resp-1",
            "output_text": "OpenAI review complete.",
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 40},
                "output_tokens": 20,
            },
        },
    )
    port = OpenAIModelPort(
        model="openai.gpt-5.6-luna",
        family="openai-luna",
        credential_ref="aws-secretsmanager://us-east-1/441186133046/models/openai/coding",
        secrets=secrets,
        transport=transport,
        api_url="https://api.openai.test/v1",
    )

    response = port.complete(REQUEST)

    assert response.status is ModelStatus.COMPLETED
    assert response.model == "openai.gpt-5.6-luna"
    assert response.family == "openai-luna"
    assert response.output == "OpenAI review complete."
    assert response.provider_execution_id == "resp-1"
    assert response.usage is not None
    assert response.usage.input_tokens == 60
    assert response.usage.cached_input_tokens == 40
    assert response.usage.output_tokens == 20
    assert secrets.references == [
        "aws-secretsmanager://us-east-1/441186133046/models/openai/coding",
    ]
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"] == "https://api.openai.test/v1/responses"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer openai-test-key"
    assert json.loads(transport.calls[0]["body"]) == {
        "model": "openai.gpt-5.6-luna",
        "input": "Review this synthetic change.",
        "max_output_tokens": 256,
    }


def test_google_model_port_uses_header_credential_and_maps_candidate_text() -> None:
    from nokinc_factory.adapters.model_providers import GoogleGeminiModelPort

    secrets = MemorySecrets('{"api_key":"google-test-key"}')
    transport = CapturingTransport(
        {
            "responseId": "google-response-1",
            "candidates": [{"content": {"parts": [{"text": "Gemini review complete."}]}}],
            "usageMetadata": {
                "promptTokenCount": 100,
                "cachedContentTokenCount": 40,
                "candidatesTokenCount": 20,
                "thoughtsTokenCount": 5,
            },
        },
    )
    port = GoogleGeminiModelPort(
        model="gemini-3.8-flash",
        family="google-gemini-flash",
        credential_ref="aws-secretsmanager://us-east-1/441186133046/models/google/coding-reviewer",
        secrets=secrets,
        transport=transport,
        api_url="https://generativelanguage.test/v1beta",
    )

    response = port.complete(REQUEST)

    assert response.status is ModelStatus.COMPLETED
    assert response.model == "gemini-3.8-flash"
    assert response.family == "google-gemini-flash"
    assert response.output == "Gemini review complete."
    assert response.provider_execution_id == "google-response-1"
    assert response.usage is not None
    assert response.usage.input_tokens == 60
    assert response.usage.cached_input_tokens == 40
    assert response.usage.output_tokens == 25
    assert transport.calls[0]["url"] == (
        "https://generativelanguage.test/v1beta/models/gemini-3.8-flash:generateContent"
    )
    assert transport.calls[0]["headers"]["x-goog-api-key"] == "google-test-key"
    assert json.loads(transport.calls[0]["body"]) == {
        "contents": [{"role": "user", "parts": [{"text": "Review this synthetic change."}]}],
        "generationConfig": {"maxOutputTokens": 256},
    }


def test_bedrock_model_port_maps_converse_response() -> None:
    from nokinc_factory.adapters.model_providers import BedrockModelPort

    client = CapturingBedrockClient()
    port = BedrockModelPort(
        model="anthropic.claude-opus-5",
        family="anthropic-claude-fable",
        client=client,
    )

    response = port.complete(REQUEST)

    assert response.status is ModelStatus.COMPLETED
    assert response.model == "anthropic.claude-opus-5"
    assert response.family == "anthropic-claude-fable"
    assert response.output == "Bedrock review complete."
    assert response.provider_execution_id == "bedrock-request-1"
    assert response.usage is not None
    assert response.usage.input_tokens == 50
    assert response.usage.cached_input_tokens == 40
    assert response.usage.cache_write_input_tokens == 10
    assert response.usage.output_tokens == 20
    assert client.request == {
        "modelId": "anthropic.claude-opus-5",
        "messages": [{"role": "user", "content": [{"text": "Review this synthetic change."}]}],
        "inferenceConfig": {"maxTokens": 256},
    }


def test_provider_error_does_not_expose_secret_value() -> None:
    from nokinc_factory.adapters.model_providers import ModelProviderError, OpenAIModelPort

    secret = "openai-secret-that-must-not-leak"
    port = OpenAIModelPort(
        model="openai.gpt-5.6-luna",
        family="openai-luna",
        credential_ref="aws-secretsmanager://us-east-1/441186133046/models/openai/coding",
        secrets=MemorySecrets(json.dumps({"api_key": secret})),
        transport=CapturingTransport({"error": "unauthorized"}, status=401),
        api_url="https://api.openai.test/v1",
    )

    with pytest.raises(ModelProviderError) as error:
        port.complete(REQUEST)

    assert secret not in str(error.value)


@pytest.mark.parametrize(
    ("secret_value", "diagnostic_code"),
    [
        ("not-json", "MODEL_CREDENTIAL_INVALID_JSON"),
        ('{"other":"value"}', "MODEL_CREDENTIAL_KEY_MISSING"),
        ('{"api_key":"  "}', "MODEL_CREDENTIAL_KEY_EMPTY"),
    ],
)
def test_openai_rejects_malformed_secret_without_requesting_provider(
    secret_value: str,
    diagnostic_code: str,
) -> None:
    from nokinc_factory.adapters.model_providers import ModelProviderError, OpenAIModelPort

    transport = CapturingTransport({"id": "unused", "output_text": "unused"})
    port = OpenAIModelPort(
        model="gpt-5.6-luna",
        family="openai-luna",
        credential_ref="models/openai/coding",
        secrets=MemorySecrets(secret_value),
        transport=transport,
        api_url="https://api.openai.test/v1",
    )

    with pytest.raises(ModelProviderError) as error:
        port.complete(REQUEST)

    assert error.value.diagnostic_code == diagnostic_code
    assert transport.calls == []


def test_openai_credential_store_failure_is_sanitized() -> None:
    from nokinc_factory.adapters.model_providers import ModelProviderError, OpenAIModelPort

    class BrokenSecrets:
        def get(self, secret_ref: str) -> str:
            raise RuntimeError("secret service response must not escape")

    port = OpenAIModelPort(
        model="gpt-5.6-luna",
        family="openai-luna",
        credential_ref="models/openai/coding",
        secrets=BrokenSecrets(),
        transport=CapturingTransport({}),
        api_url="https://api.openai.test/v1",
    )

    with pytest.raises(ModelProviderError) as error:
        port.complete(REQUEST)

    assert error.value.diagnostic_code == "MODEL_CREDENTIAL_RETRIEVAL_FAILED"
    assert "secret service response" not in str(error.value)


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ({"id": "resp-1", "output_text": "ok", "usage": {"input_tokens": -1}}, None),
        ({"id": "resp-1", "output_text": "ok", "usage": "unknown"}, None),
    ],
)
def test_openai_malformed_usage_is_unknown_not_fabricated(
    payload: dict[str, object],
    expected_code: None,
) -> None:
    from nokinc_factory.adapters.model_providers import OpenAIModelPort

    response = OpenAIModelPort(
        model="gpt-5.6-luna",
        family="openai-luna",
        credential_ref="models/openai/coding",
        secrets=MemorySecrets('{"api_key":"test-key"}'),
        transport=CapturingTransport(payload),
        api_url="https://api.openai.test/v1",
    ).complete(REQUEST)

    assert response.output == "ok"
    assert response.usage is expected_code


@pytest.mark.parametrize(
    ("payload", "provider"),
    [
        ({"candidates": []}, "google"),
        ({"candidates": [{"content": {"parts": []}}]}, "google"),
        ({"output": {"message": {"content": []}}}, "bedrock"),
        ({"output": {"message": {"content": [{"text": " "}]}}}, "bedrock"),
    ],
)
def test_provider_rejects_empty_or_malformed_output(
    payload: dict[str, object],
    provider: str,
) -> None:
    from nokinc_factory.adapters.model_providers import (
        BedrockModelPort,
        GoogleGeminiModelPort,
        ModelProviderError,
    )

    if provider == "google":
        port = GoogleGeminiModelPort(
            model="gemini-3.8-flash",
            family="google-gemini-flash",
            credential_ref="models/google/reviewer",
            secrets=MemorySecrets('{"api_key":"test-key"}'),
            transport=CapturingTransport(payload),
            api_url="https://generativelanguage.test/v1beta",
        )
    else:
        class StaticBedrockClient:
            def converse(self, **kwargs: object) -> object:
                return payload

        port = BedrockModelPort(
            model="amazon.nova-pro-v1:0",
            family="amazon-nova-pro",
            client=StaticBedrockClient(),
        )

    with pytest.raises(ModelProviderError):
        port.complete(REQUEST)


@pytest.mark.parametrize(
    ("status", "diagnostic_code"),
    [
        (401, "HTTP_AUTHORIZATION"),
        (403, "HTTP_AUTHORIZATION"),
        (429, "HTTP_THROTTLED"),
        (500, "HTTP_UPSTREAM"),
        (400, "HTTP_REJECTED"),
    ],
)
def test_http_failure_has_safe_diagnostic_code_without_provider_body(
    status: int,
    diagnostic_code: str,
) -> None:
    from nokinc_factory.adapters.model_providers import ModelProviderError, OpenAIModelPort

    response_marker = "provider-body-must-not-be-logged"
    port = OpenAIModelPort(
        model="openai.gpt-5.6-luna",
        family="openai-luna",
        credential_ref="models/openai/coding",
        secrets=MemorySecrets('{"api_key":"test-key"}'),
        transport=CapturingTransport({"error": response_marker}, status=status),
        api_url="https://api.openai.test/v1",
    )

    with pytest.raises(ModelProviderError) as error:
        port.complete(REQUEST)

    assert error.value.diagnostic_code == diagnostic_code
    assert response_marker not in str(error.value)


def test_bedrock_error_code_is_allowlisted_without_provider_message() -> None:
    from nokinc_factory.adapters.model_providers import BedrockModelPort, ModelProviderError

    provider_message = "account 123 secret response detail"

    class AccessDeniedError(Exception):
        response = {"Error": {"Code": "AccessDeniedException", "Message": provider_message}}

    class FailingBedrockClient:
        def converse(self, **kwargs: object) -> object:
            raise AccessDeniedError(provider_message)

    port = BedrockModelPort(
        model="amazon.nova-pro-v1:0",
        family="amazon-nova-pro",
        client=FailingBedrockClient(),
    )

    with pytest.raises(ModelProviderError) as error:
        port.complete(REQUEST)

    assert error.value.diagnostic_code == "BEDROCK_ACCESS_DENIED"
    assert provider_message not in str(error.value)