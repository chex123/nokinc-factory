import pytest

from nokinc_factory.adapters.model_providers import ModelProviderError
from nokinc_factory.adapters.model_qualification import FakeModelPort
from nokinc_factory.application.model_qualification_runner import (
    QualificationCallLimit,
    QualificationCallLimitReached,
    QualificationCase,
    QualificationRunner,
    QualificationStatus,
)
from nokinc_factory.ports.model import ModelRequest, ModelResponse, ModelStatus, ModelUsage

REQUEST = ModelRequest(
    role="qualification",
    prompt="Return the exact marker QUALIFICATION_OK and nothing else.",
    context_digest="sha256:" + "2" * 64,
)


class FailingProviderPort:
    def __init__(self, diagnostic_code: str) -> None:
        self._diagnostic_code = diagnostic_code

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise ModelProviderError(
            "private provider response detail",
            diagnostic_code=self._diagnostic_code,
        )


class UsageModelPort:
    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            status=ModelStatus.COMPLETED,
            model="gpt-5.6-luna",
            family="openai-luna",
            output="QUALIFICATION_OK",
            provider_execution_id="usage-test-run",
            usage=ModelUsage(
                input_tokens=100,
                cached_input_tokens=20,
                output_tokens=10,
            ),
        )


def test_runner_records_redacted_pass_evidence() -> None:
    runner = QualificationRunner(
        QualificationCallLimit(max_calls=1),
    )

    report = runner.run((
        QualificationCase(
            provider="test",
            model="test-model",
            family="test-family",
            port=FakeModelPort(model="test-model", family="test-family"),
        ),
    ), REQUEST)

    evidence = report.evidence[0]
    assert report.passed is True
    assert evidence.status is QualificationStatus.PASSED
    assert evidence.output_digest.startswith("sha256:")
    assert evidence.output_digest != ""
    assert evidence.output == ""
    assert report.call_limit.calls_used == 1
    assert "estimated_cost_usd" not in report.as_dict()["call_limit"]


def test_runner_records_actual_usage_and_published_list_price() -> None:
    runner = QualificationRunner(QualificationCallLimit(max_calls=1))

    report = runner.run((
        QualificationCase(
            provider="openai",
            model="gpt-5.6-luna",
            family="openai-luna",
            port=UsageModelPort(),
        ),
    ), REQUEST)

    evidence = report.evidence[0]
    assert evidence.usage == ModelUsage(
        input_tokens=100,
        cached_input_tokens=20,
        output_tokens=10,
    )
    assert evidence.list_price_cost is not None
    assert evidence.list_price_cost.cost_nanodollars == 32_400
    assert report.as_dict()["evidence"][0]["list_price_cost"]["cost_nanodollars"] == 32_400


def test_runner_refuses_calls_after_explicit_call_limit() -> None:
    call_limit = QualificationCallLimit(max_calls=1)
    runner = QualificationRunner(call_limit)
    cases = tuple(
        QualificationCase(
            provider="test",
            model=f"test-model-{index}",
            family="test-family",
            port=FakeModelPort(model=f"test-model-{index}", family="test-family"),
        )
        for index in range(2)
    )

    report = runner.run(cases, REQUEST)

    assert report.passed is False
    assert [item.status for item in report.evidence] == [
        QualificationStatus.PASSED,
        QualificationStatus.BLOCKED_CALL_LIMIT,
    ]
    assert call_limit.calls_used == 1


def test_runner_keeps_provider_failures_redacted() -> None:
    runner = QualificationRunner(
        QualificationCallLimit(max_calls=1),
    )
    report = runner.run((
        QualificationCase(
            provider="test",
            model="missing",
            family="test-family",
            port=FakeModelPort(model="missing", family="test-family", available=False),
        ),
    ), REQUEST)

    assert report.passed is False
    assert report.evidence[0].status is QualificationStatus.FAILED
    assert report.evidence[0].error_code == "MODEL_UNAVAILABLE"
    assert report.evidence[0].output == ""


@pytest.mark.parametrize(
    ("diagnostic_code", "expected_code"),
    [
        ("HTTP_AUTHORIZATION", "HTTP_AUTHORIZATION"),
        ("provider response token", "PROVIDER_ERROR"),
    ],
)
def test_runner_retains_only_safe_provider_diagnostic_codes(
    diagnostic_code: str,
    expected_code: str,
) -> None:
    runner = QualificationRunner(
        QualificationCallLimit(max_calls=1),
    )
    report = runner.run((
        QualificationCase(
            provider="test",
            model="provider-error",
            family="test-family",
            port=FailingProviderPort(diagnostic_code),
        ),
    ), REQUEST)

    evidence = report.evidence[0]
    assert evidence.status is QualificationStatus.FAILED
    assert evidence.error_code == expected_code
    assert evidence.output == ""
    assert "private provider response detail" not in str(report.as_dict())


def test_call_limit_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        QualificationCallLimit(max_calls=0)

    with pytest.raises(QualificationCallLimitReached):
        call_limit = QualificationCallLimit(max_calls=1)
        call_limit.reserve()
        call_limit.reserve()