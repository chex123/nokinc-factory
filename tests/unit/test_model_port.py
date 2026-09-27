import pytest

from nokinc_factory.adapters.model_qualification import FakeModelPort, ModelUnavailable
from nokinc_factory.ports.model import ModelRequest, ModelResponse, ModelStatus


def test_fake_model_port_returns_structured_response_with_identity() -> None:
    port = FakeModelPort(model="fake-reviewer", family="independent-family")

    response = port.complete(ModelRequest(
        role="reviewer", prompt="review this", context_digest="sha256:" + "1" * 64,
    ))

    assert isinstance(response, ModelResponse)
    assert response.status is ModelStatus.COMPLETED
    assert response.model == "fake-reviewer"
    assert response.family == "independent-family"
    assert response.output


def test_unavailable_model_is_not_pass() -> None:
    port = FakeModelPort(model="missing", family="unknown", available=False)

    with pytest.raises(ModelUnavailable):
        port.complete(ModelRequest(
            role="reviewer", prompt="review this", context_digest="sha256:" + "1" * 64,
        ))
