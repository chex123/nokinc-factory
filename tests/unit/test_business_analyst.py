import json

import pytest

from nokinc_factory.application.business_analyst import BusinessAnalystDiscussion
from nokinc_factory.application.chat_roles import ConversationTurn
from nokinc_factory.ports.model import ModelRequest, ModelResponse, ModelStatus


class FakeModel:
    def __init__(self, *, model: str, family: str, output: str | tuple[str, ...]) -> None:
        self.model = model
        self.family = family
        self.outputs = list(output) if isinstance(output, tuple) else [output]
        self.last_output = self.outputs[-1]
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            status=ModelStatus.COMPLETED,
            model=self.model,
            family=self.family,
            output=self.outputs.pop(0) if self.outputs else self.last_output,
            provider_execution_id=f"run-{self.model}",
        )


def _discussion(
    *,
    reviewer_output: str | None = None,
    doer_outputs: tuple[str, ...] | None = None,
    reviewer_outputs: tuple[str, ...] | None = None,
):
    doer = FakeModel(
        model="gpt-6-astra",
        family="openai-astra",
        output=doer_outputs or (json.dumps({
            "reply": "Who experiences the refund problem, and what happens today?",
            "open_questions": ["Who experiences it?", "What happens today?"],
        }),),
    )
    reviewer = FakeModel(
        model="amazon.nova-pro-v1:0",
        family="amazon-nova-pro",
        output=reviewer_outputs or reviewer_output or json.dumps({
            "supported": True,
            "issues": [],
            "open_questions": [],
        }),
    )
    discussion = BusinessAnalystDiscussion(
        doer=doer,
        reviewer=reviewer,
        doer_provider="openai",
        doer_model="gpt-6-astra",
        doer_family="openai-astra",
        reviewer_provider="aws-bedrock",
        reviewer_model="amazon.nova-pro-v1:0",
        reviewer_family="amazon-nova-pro",
    )
    return discussion, doer, reviewer


def test_business_analyst_asks_reviewed_questions_and_uses_conversation_history() -> None:
    discussion, doer, reviewer = _discussion()
    history = (ConversationTurn(role="user", content="Refund processing is confusing."),)

    result = discussion.answer(
        work_item_id="wi-test",
        question="Customers call support to ask where refunds are.",
        repository="NOK-Apps/flur-frontend",
        history=history,
    )

    assert result.status == "ELICITING"
    assert len(result.open_questions) == 2
    assert result.model_runs[0].stage == "business-analyst"
    assert result.model_runs[1].stage == "business-review"
    assert "Refund processing is confusing." in doer.requests[0].prompt
    assert "NOK-Apps/flur-frontend" in doer.requests[0].prompt
    assert "service boundaries" in doer.requests[0].prompt
    assert "service boundary" in reviewer.requests[0].prompt
    assert result.context_digest.startswith("sha256:")


def test_business_analyst_withholds_draft_when_reviewer_finds_assumptions() -> None:
    discussion, doer, reviewer = _discussion(reviewer_output=json.dumps({
        "supported": False,
        "issues": ["The draft invents a user group."],
        "open_questions": ["Which users are affected?"],
    }))

    result = discussion.answer(
        work_item_id="wi-test",
        question="Refunds are confusing.",
    )

    assert result.status == "NEEDS_CLARIFICATION"
    assert result.reply == "I could not verify the assumptions in my draft."
    assert result.open_questions == ("Which users are affected?",)
    assert len(doer.requests) == 3
    assert len(reviewer.requests) == 3
    assert len(result.model_runs) == 6


def test_business_analyst_uses_review_feedback_and_stops_when_supported() -> None:
    first_draft = json.dumps({"reply": "The company loses money on refunds.", "open_questions": []})
    revised_draft = json.dumps({
        "reply": "Customers call support because they cannot see refund status.",
        "open_questions": ["Which refund states should be shown?"],
    })
    discussion, doer, reviewer = _discussion(
        doer_outputs=(first_draft, revised_draft),
        reviewer_outputs=(
            json.dumps({
                "supported": False,
                "issues": ["The conversation does not state a financial loss."],
                "open_questions": ["What problem do customers report?"],
            }),
            json.dumps({"supported": True, "issues": [], "open_questions": []}),
        ),
    )

    result = discussion.answer(
        work_item_id="wi-test",
        question="Customers call support to ask where refunds are.",
    )

    assert result.status == "ELICITING"
    assert result.reply == "Customers call support because they cannot see refund status."
    assert "does not state a financial loss" in doer.requests[1].prompt
    assert len(doer.requests) == 2
    assert len(reviewer.requests) == 2
    assert len(result.model_runs) == 4


def test_business_analyst_prompt_uses_the_doer_model_input_limit() -> None:
    discussion, doer, _ = _discussion()

    with pytest.raises(ValueError, match="model input token ceiling"):
        discussion._invoke(
            doer,
            stage="business-analyst",
            provider="openai",
            role="business_analyst_elicitation",
            prompt="x" * 922_001,
            context_digest="sha256:" + "1" * 64,
            expected_model="gpt-6-astra",
            expected_family="openai-astra",
        )

    assert doer.requests == []


def test_business_analyst_refuses_same_family_reviewer() -> None:
    with pytest.raises(ValueError, match="different model families"):
        BusinessAnalystDiscussion(
            doer=FakeModel(model="model-a", family="same", output="{}"),
            reviewer=FakeModel(model="model-b", family="same", output="{}"),
            doer_provider="vendor-a",
            doer_model="model-a",
            doer_family="same",
            reviewer_provider="vendor-b",
            reviewer_model="model-b",
            reviewer_family="same",
        )