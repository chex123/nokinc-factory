from decimal import Decimal

import pytest

from nokinc_factory.adapters.model_qualification import FakeModelPort
from nokinc_factory.application.model_qualification_runner import (
    QualificationBudget,
    QualificationBudgetExceeded,
    QualificationCase,
    QualificationRunner,
    QualificationStatus,
)
from nokinc_factory.ports.model import ModelRequest

REQUEST = ModelRequest(
    role="qualification",
    prompt="Return the exact marker QUALIFICATION_OK and nothing else.",
    context_digest="sha256:" + "2" * 64,
)


def test_runner_records_redacted_pass_evidence() -> None:
    runner = QualificationRunner(
        QualificationBudget(
            max_calls=1,
            max_estimated_cost_usd=Decimal("2.00"),
            estimated_cost_per_call_usd=Decimal("1.00"),
        ),
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
    assert report.budget.calls_used == 1
    assert report.budget.estimated_cost_usd == Decimal("1.00")


def test_runner_refuses_calls_after_hard_budget() -> None:
    budget = QualificationBudget(
        max_calls=1,
        max_estimated_cost_usd=Decimal("1.00"),
        estimated_cost_per_call_usd=Decimal("1.00"),
    )
    runner = QualificationRunner(budget)
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
        QualificationStatus.BLOCKED_BUDGET,
    ]
    assert budget.calls_used == 1


def test_runner_keeps_provider_failures_redacted() -> None:
    runner = QualificationRunner(
        QualificationBudget(
            max_calls=1,
            max_estimated_cost_usd=Decimal("1.00"),
            estimated_cost_per_call_usd=Decimal("1.00"),
        ),
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


def test_budget_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        QualificationBudget(
            max_calls=0,
            max_estimated_cost_usd=Decimal("1.00"),
            estimated_cost_per_call_usd=Decimal("1.00"),
        )

    with pytest.raises(QualificationBudgetExceeded):
        QualificationBudget(
            max_calls=1,
            max_estimated_cost_usd=Decimal("0.50"),
            estimated_cost_per_call_usd=Decimal("1.00"),
        ).reserve()