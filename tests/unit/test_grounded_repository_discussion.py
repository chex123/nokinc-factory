import json
from datetime import UTC, datetime

import pytest

from nokinc_factory.adapters.github_repository_reader import (
    RepositoryCodeContext,
    RepositorySourceFile,
)
from nokinc_factory.application.chat_roles import ConversationTurn
from nokinc_factory.application.grounded_repository_discussion import (
    GroundedRepositoryDiscussion,
    GroundedRepositoryDiscussionRouter,
)
from nokinc_factory.domain.review_base import content_digest
from nokinc_factory.ports.model import ModelRequest, ModelResponse, ModelStatus

NOW = datetime(2026, 9, 25, 20, tzinfo=UTC)
SOURCE = "export function login(token: string) {\n  localStorage.setItem('auth_token', token);\n}\n"
REPOSITORY = "NOK-Apps/flur-frontend"


class FakeSourceReader:
    def __init__(self) -> None:
        source_file = RepositorySourceFile(
            path="apps/web/src/auth.ts",
            blob_sha="a" * 40,
            content_digest="sha256:" + "b" * 64,
            text=SOURCE,
        )
        self.context = RepositoryCodeContext(
            repository=REPOSITORY,
            default_branch="main",
            tree_sha="c" * 40,
            context_digest=content_digest({"tree": "c" * 40, "file": source_file.content_digest}),
            files=(source_file,),
        )
        self.calls: list[tuple[str, str]] = []

    def read_codebase_context(self, repository: str, question: str) -> RepositoryCodeContext:
        self.calls.append((repository, question))
        return self.context


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
            provider_execution_id=f"run-{len(self.requests)}",
        )


def _draft(*, quote: str = "localStorage.setItem('auth_token', token);") -> str:
    return json.dumps({
        "summary": "The web login stores the auth token in localStorage.",
        "claims": [{
            "statement": "The login function persists the token in localStorage.",
            "citations": [{
                "path": "apps/web/src/auth.ts",
                "line_start": 2,
                "line_end": 2,
                "quote": quote,
            }],
        }],
        "open_questions": [],
    })


def _discussion(
    *,
    draft: str | None = None,
    review: str | None = None,
    draft_sequence: tuple[str, ...] | None = None,
    review_sequence: tuple[str, ...] | None = None,
    reviewer_family: str = "amazon-nova-pro",
) -> tuple[GroundedRepositoryDiscussion, FakeSourceReader, FakeModel, FakeModel]:
    source = FakeSourceReader()
    doer = FakeModel(
        model="gpt-6-astra",
        family="openai-astra",
        output=draft_sequence or (draft or _draft(),),
    )
    reviewer = FakeModel(
        model="amazon.nova-pro-v1:0",
        family=reviewer_family,
        output=review_sequence or (review or json.dumps({
            "supported": True,
            "issues": [],
            "open_questions": [],
        }),),
    )
    discussion = GroundedRepositoryDiscussion(
        source_reader=source,
        doer=doer,
        reviewer=reviewer,
        doer_provider="openai",
        doer_model="gpt-6-astra",
        doer_family="openai-astra",
        reviewer_provider="aws-bedrock",
        reviewer_model="amazon.nova-pro-v1:0",
        reviewer_family=reviewer_family,
        clock=lambda: NOW,
    )
    return discussion, source, doer, reviewer


def test_discussion_returns_only_independently_reviewed_cited_claims() -> None:
    discussion, source, doer, reviewer = _discussion()

    result = discussion.answer(repository=REPOSITORY, question="Where is the auth token stored?")

    assert result.status == "ANSWERED"
    assert result.summary == "The web login stores the auth token in localStorage."
    assert result.claims[0].citations[0].path == "apps/web/src/auth.ts"
    assert result.claims[0].citations[0].line_start == 2
    assert result.model_runs[0].family == "openai-astra"
    assert result.model_runs[1].family == "amazon-nova-pro"
    assert result.context_digest == source.context.context_digest
    assert len(doer.requests) == 1 and len(reviewer.requests) == 1
    assert doer.requests[0].context_digest == reviewer.requests[0].context_digest


@pytest.mark.parametrize(
    ("model", "family", "provider", "stage", "limit"),
    [
        ("gpt-6-astra", "openai-astra", "openai", "architect", 922_000),
        (
            "amazon.nova-pro-v1:0",
            "amazon-nova-pro",
            "aws-bedrock",
            "independent-review",
            290_000,
        ),
    ],
)
def test_prompt_respects_each_models_documented_input_limit(
    model: str,
    family: str,
    provider: str,
    stage: str,
    limit: int,
) -> None:
    discussion, _, doer, reviewer = _discussion()
    model_port = reviewer if provider == "aws-bedrock" else doer

    with pytest.raises(ValueError, match="model input token ceiling"):
        discussion._invoke(
            model_port,
            stage=stage,
            provider=provider,
            role="context_limit_test",
            prompt="x" * (limit + 1),
            context_digest=content_digest("context-limit-test"),
            expected_model=model,
            expected_family=family,
        )

    assert model_port.requests == []


def test_source_context_preflights_against_smaller_model_in_the_pair() -> None:
    discussion, source, doer, reviewer = _discussion()
    auth_source = RepositorySourceFile(
        path="apps/web/src/auth.ts",
        blob_sha="1" * 40,
        content_digest=content_digest(SOURCE + "x" * (32_000 - len(SOURCE))),
        text=SOURCE + "x" * (32_000 - len(SOURCE)),
    )
    large_sources = (auth_source,) + tuple(
        RepositorySourceFile(
            path=f"src/large-{index}.ts",
            blob_sha=f"{index + 2:040x}",
            content_digest=content_digest("x" * 32_000),
            text="x" * 32_000,
        )
        for index in range(7)
    )
    source.context = RepositoryCodeContext(
        repository=REPOSITORY,
        default_branch="main",
        tree_sha="e" * 40,
        context_digest=content_digest([
            item.content_digest for item in large_sources
        ]),
        files=large_sources,
    )

    with pytest.raises(ValueError, match="paired model input limit"):
        discussion.answer(repository=REPOSITORY, question="Review the large source context.")

    assert doer.requests == []
    assert reviewer.requests == []


def test_discussion_combines_repositories_and_scopes_same_path_citations() -> None:
    repositories = (REPOSITORY, "NOK-Apps/flur-sdk")
    source_text = {
        repositories[0]: "Frontend token storage",
        repositories[1]: "SDK token storage",
    }
    contexts = {
        repository: RepositoryCodeContext(
            repository=repository,
            default_branch="main",
            tree_sha=sha,
            context_digest=content_digest({"repository": repository}),
            files=(RepositorySourceFile(
                path="README.md",
                blob_sha=sha,
                content_digest=content_digest(source_text[repository]),
                text=source_text[repository],
            ),),
        )
        for repository, sha in zip(repositories, ("d" * 40, "e" * 40), strict=True)
    }

    class MultiRepositoryReader:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def read_codebase_context(self, repository: str, question: str) -> RepositoryCodeContext:
            self.calls.append((repository, question))
            return contexts[repository]

    draft = json.dumps({
        "summary": "The two repositories persist tokens differently.",
        "claims": [
            {
                "statement": "The frontend persists a token locally.",
                "citations": [{
                    "repository": repositories[0],
                    "path": "README.md",
                    "line_start": 1,
                    "line_end": 1,
                    "quote": source_text[repositories[0]],
                }],
            },
            {
                "statement": "The SDK persists a token in a cookie.",
                "citations": [{
                    "repository": repositories[1],
                    "path": "README.md",
                    "line_start": 1,
                    "line_end": 1,
                    "quote": source_text[repositories[1]],
                }],
            },
        ],
        "open_questions": [],
    })
    reader = MultiRepositoryReader()
    doer = FakeModel(model="gpt-6-astra", family="openai-astra", output=draft)
    reviewer = FakeModel(
        model="amazon.nova-pro-v1:0",
        family="amazon-nova-pro",
        output='{"supported":true,"issues":[],"open_questions":[]}',
    )
    discussion = GroundedRepositoryDiscussion(
        source_reader=reader,
        doer=doer,
        reviewer=reviewer,
        doer_provider="openai",
        doer_model="gpt-6-astra",
        doer_family="openai-astra",
        reviewer_provider="aws-bedrock",
        reviewer_model="amazon.nova-pro-v1:0",
        reviewer_family="amazon-nova-pro",
        clock=lambda: NOW,
    )

    result = discussion.answer(
        repositories=repositories,
        question="Compare token storage across these repositories.",
    )

    assert result.status == "ANSWERED"
    assert result.repositories == repositories
    assert reader.calls == [
        (repository, "Compare token storage across these repositories.")
        for repository in repositories
    ]
    assert [claim.citations[0].repository for claim in result.claims] == list(repositories)
    assert doer.requests[0].context_digest == reviewer.requests[0].context_digest


def test_discussion_rejects_a_quote_not_present_at_its_cited_lines() -> None:
    discussion, _, _, reviewer = _discussion(draft=_draft(quote="token is encrypted"))

    with pytest.raises(ValueError, match="citation quote"):
        discussion.answer(repository=REPOSITORY, question="How is auth stored?")

    assert reviewer.requests == []


def test_independent_reviewer_can_withhold_an_unsupported_answer() -> None:
    discussion, _, doer, reviewer = _discussion(
        review=json.dumps({
            "supported": False,
            "issues": ["The repository evidence does not support the claim."],
            "open_questions": ["Inspect the mobile authentication flow."],
        })
    )

    result = discussion.answer(repository=REPOSITORY, question="How is auth stored?")

    assert result.status == "NEEDS_CLARIFICATION"
    assert result.claims == ()
    assert result.open_questions == ("Inspect the mobile authentication flow.",)
    assert len(doer.requests) == 3
    assert len(reviewer.requests) == 3
    assert len(result.model_runs) == 6


def test_architect_refines_against_independent_review_before_answering() -> None:
    first_review = json.dumps({
        "supported": False,
        "issues": ["The summary overstates what the cited line proves."],
        "open_questions": [],
    })
    accepted_review = json.dumps({"supported": True, "issues": [], "open_questions": []})
    discussion, _, doer, reviewer = _discussion(
        draft_sequence=(_draft(), _draft()),
        review_sequence=(first_review, accepted_review),
    )

    result = discussion.answer(
        repository=REPOSITORY,
        question="Where is the auth token stored?",
    )

    assert result.status == "ANSWERED"
    assert len(doer.requests) == 2
    assert len(reviewer.requests) == 2
    assert len(result.model_runs) == 4
    assert "overstates what the cited line proves" in doer.requests[1].prompt


def test_discussion_requires_model_family_independence() -> None:
    source = FakeSourceReader()
    same_family_doer = FakeModel(model="model-a", family="same", output=_draft())
    same_family_reviewer = FakeModel(
        model="model-b",
        family="same",
        output='{"supported":true,"issues":[],"open_questions":[]}',
    )

    with pytest.raises(ValueError, match="different model families"):
        GroundedRepositoryDiscussion(
            source_reader=source,
            doer=same_family_doer,
            reviewer=same_family_reviewer,
            doer_provider="provider-a",
            doer_model="model-a",
            doer_family="same",
            reviewer_provider="provider-b",
            reviewer_model="model-b",
            reviewer_family="same",
        )


def test_discussion_router_uses_only_the_selected_profile() -> None:
    class FakeProfile:
        def __init__(self, result: str) -> None:
            self.result = result
            self.calls: list[tuple[str, str]] = []

        def answer(
            self,
            *,
            repository: str,
            question: str,
            history: tuple[ConversationTurn, ...] = (),
        ) -> str:
            self.calls.append((repository, question))
            return self.result

    architecture = FakeProfile("architecture-result")
    coding = FakeProfile("coding-result")
    router = GroundedRepositoryDiscussionRouter(
        architecture=architecture,
        coding=coding,
    )

    result = router.answer(
        repository=REPOSITORY,
        question="Review the SDK implementation",
        profile="coding",
    )

    assert result == "coding-result"
    assert architecture.calls == []
    assert coding.calls == [(REPOSITORY, "Review the SDK implementation")]