import pytest

from nokinc_factory.adapters.model_qualification import FakeModelPort, ModelUnavailable
from nokinc_factory.policy.model_qualification import (
    ModelQualificationError,
    invoke_qualified,
)
from nokinc_factory.ports.model import ModelRequest, ModelStatus

REQUEST = ModelRequest(
    role="reviewer", prompt="review", context_digest="sha256:" + "1" * 64,
)


def test_qualified_invocation_requires_a_distinct_reviewer_family() -> None:
    result = invoke_qualified(
        FakeModelPort(model="reviewer-v1", family="reviewer-family"),
        REQUEST, producer_family="doer-family",
    )

    assert result.status is ModelStatus.COMPLETED
    assert result.family == "reviewer-family"


def test_same_family_is_not_independent() -> None:
    with pytest.raises(ModelQualificationError, match="family"):
        invoke_qualified(
            FakeModelPort(model="reviewer-v1", family="doer-family"),
            REQUEST, producer_family="doer-family",
        )


def test_unavailable_provider_is_not_converted_to_a_pass() -> None:
    with pytest.raises(ModelUnavailable):
        invoke_qualified(
            FakeModelPort(model="missing", family="reviewer-family", available=False),
            REQUEST, producer_family="doer-family",
        )
