import pytest

from nokinc_factory.application.chat_roles import route_chat_role


def test_auto_route_uses_business_analyst_without_repository() -> None:
    role, profile = route_chat_role(
        mode="auto",
        message="We need refunds to be easier to understand.",
        repository=None,
    )

    assert role == "business_analyst"
    assert profile is None


def test_auto_route_delegates_code_question_to_repository_analyst() -> None:
    role, profile = route_chat_role(
        mode="auto",
        message="Where is the login token saved in the code?",
        repository="NOK-Apps/flur-frontend",
    )

    assert role == "code_analyst"
    assert profile == "coding"


def test_auto_route_delegates_system_design_question_to_architect() -> None:
    role, profile = route_chat_role(
        mode="auto",
        message="What is the authentication architecture and data flow?",
        repository="NOK-Apps/flur-frontend",
    )

    assert role == "architect"
    assert profile == "architecture"


def test_explicit_code_role_requires_repository_evidence() -> None:
    with pytest.raises(ValueError, match="requires a selected repository"):
        route_chat_role(mode="coding", message="Review login", repository=None)


def test_explicit_business_role_never_escalates_to_architecture() -> None:
    role, profile = route_chat_role(
        mode="business",
        message="Design a new payment API.",
        repository="NOK-Apps/flur-sdk",
    )

    assert role == "business_analyst"
    assert profile is None