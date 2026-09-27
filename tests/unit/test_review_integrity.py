"""Hash, recursively owned state, identity and exact-artifact consumption boundaries."""

import json

import pytest
from pydantic import ValidationError
from test_review_fixtures import (
    at,
    begin,
    changed_artifact,
    clean,
    digest,
    finding,
    finish,
    model,
    repair_report,
    report,
    session,
)

from nokinc_factory.domain.review import ArtifactRef, QualityContract, ReviewPolicy
from nokinc_factory.domain.review_session import ReviewSession
from nokinc_factory.policy.review import evaluate, start_session


def test_frozen_children_are_recursively_owned_and_json_roundtrips_are_lossless() -> None:
    original = session()
    values = original.seed.contract.model_dump(exclude={"content_digest"})
    contract = QualityContract.model_validate(values)
    values["criteria"][0]["predicate"] = "Caller mutated its dictionary"
    assert contract.criteria[0].predicate == "The fixed cases pass"
    assert contract.criteria[0] is not original.seed.contract.criteria[0]
    with pytest.raises(ValidationError):
        contract.criteria[0].__setattr__("predicate", "move goalposts")
    done = clean(clean(original, 1), 2)
    restored = ReviewSession.model_validate_json(done.model_dump_json())
    assert restored == done
    assert isinstance(restored.attempts, tuple)
    assert isinstance(restored.artifact.inputs, tuple)
    assert evaluate(restored, current_artifact=restored.artifact, now=at(8)).eligible


def test_contract_and_artifact_hashes_are_computed_and_supplied_hashes_are_checked() -> None:
    s = session()
    contract = s.seed.contract
    reordered = dict(reversed(tuple(contract.model_dump().items())))
    assert QualityContract.model_validate(reordered).content_digest == contract.content_digest
    with pytest.raises(ValidationError):
        QualityContract.model_validate(contract.model_dump() | {"content_digest": digest("fake")})
    updated = QualityContract.model_validate(contract.model_dump(exclude={"content_digest"}) | {
        "design_refs": ({"name": "design", "digest": digest("amended-C4")},),
    })
    assert updated.content_digest != contract.content_digest
    assert changed_artifact(s).content_digest != s.artifact.content_digest


@pytest.mark.parametrize("part", ["criteria", "producer", "input", "build", "policy"])
def test_stale_model_copy_child_hash_is_rejected_on_consumption(part: str) -> None:
    s = session()
    if part == "criteria":
        child = s.seed.contract.criteria[0].model_copy(update={"mandatory": False})
        contract = s.seed.contract.model_copy(update={"criteria": (child,)})
        seed = s.seed.model_copy(update={"contract": contract})
    elif part == "policy":
        rules = s.seed.policy.model_copy(update={"min_review_passes": 1})
        seed = s.seed.model_copy(update={"policy": rules})
    else:
        field = {"producer": "producers", "input": "inputs", "build": "build_refs"}[part]
        child = getattr(s.artifact, field)[0].model_copy(update={
            "family" if part == "producer" else "digest": (
                "fake" if part == "producer" else digest("x")),
        })
        artifact = s.artifact.model_copy(update={field: (child,)})
        seed = s.seed.model_copy(update={"initial_artifact": artifact})
    corrupt = s.model_copy(update={"seed": seed})
    with pytest.raises(ValueError):
        evaluate(corrupt, current_artifact=s.artifact, now=at(0))


@pytest.mark.parametrize("field,value", [
    ("valid_completed_reports", []), ("findings", []), ("terminal_reasons", []),
    ("budget", {"invocations": 0}),
])
def test_rehashed_but_inconsistent_materialized_state_cannot_replace_the_ledger(
    field: str, value: object,
) -> None:
    first = begin(session(), 1)
    once = finish(first, report(first, findings=(finding(first),)))
    pending = begin(once, 2)
    stopped = finish(pending, report(pending))
    payload = stopped.model_dump(mode="json")
    payload.pop("content_digest")
    payload["state"].pop("content_digest")
    payload["state"][field] = value
    with pytest.raises(ValueError):
        restored = ReviewSession.model_validate_json(json.dumps(payload))
        evaluate(restored, current_artifact=stopped.artifact, now=at(8))


@pytest.mark.parametrize("field,value", [
    ("payload_digest", digest("drift")), ("context_digest", digest("context-2")),
    ("contract_digest", digest("contract-2")),
    ("inputs", ({"name": "source", "digest": digest("source-2")},)),
    ("build_refs", ({"name": "build", "digest": digest("build-2")},)),
])
def test_any_final_artifact_or_evidence_drift_invalidates_eligibility(
    field: str, value: object,
) -> None:
    done = clean(clean(session(), 1), 2)
    changed = ArtifactRef.model_validate(
        done.artifact.model_dump(exclude={"content_digest"}) | {field: value})
    decision = evaluate(done, current_artifact=changed, now=at(8))
    assert not decision.eligible
    assert "ARTIFACT_DRIFT" in decision.reasons


@pytest.mark.parametrize("change", [
    {"family": None}, {"qualification_ref": None}, {"capability_refs": ()},
    {"canonical_model_id": "doer"}, {"role": "PRODUCER"},
])
def test_missing_qualification_and_role_or_canonical_model_aliases_are_not_independence(
    change: dict[str, object],
) -> None:
    s = session()
    with pytest.raises(ValueError):
        reviewer = type(s.seed.reviewers[0]).model_validate(
            s.seed.reviewers[0].model_dump(exclude={"content_digest"}) | change)
        start_session(scope=s.seed.scope, contract=s.seed.contract, policy=s.seed.policy,
                      original_doer=s.seed.original_doer, reviewers=(reviewer,),
                      artifact=s.artifact, now=at(0))


def test_t2_same_family_is_not_fixed_by_different_provider_or_endpoint() -> None:
    s = session(risk="T2")
    same_family = model("other-model", reviewer=True)
    with pytest.raises(ValueError):
        start_session(scope=s.seed.scope, contract=s.seed.contract, policy=s.seed.policy,
                      original_doer=s.seed.original_doer, reviewers=(same_family,),
                      artifact=s.artifact, now=at(0))


def test_unplanned_model_change_and_reviewer_repair_are_denied_before_execution() -> None:
    s = session()
    with pytest.raises(ValueError):
        begin(s, 1, actor=model("unplanned", reviewer=True, family="c"))
    pending = begin(s, 1)
    reviewed = finish(pending, report(pending, findings=(finding(pending),)))
    with pytest.raises(ValueError):
        begin(reviewed, 2, repair=True, actor=reviewed.seed.reviewers[0])
    with pytest.raises(ValueError):
        begin(reviewed, 2, repair=True, actor=model("replacement-doer"))


def test_reviewer_edit_cannot_be_hidden_in_repair_producer_lineage() -> None:
    first = begin(session(), 1)
    reviewed = finish(first, report(first, findings=(finding(first),)))
    pending = begin(reviewed, 2, repair=True)
    candidate = changed_artifact(pending)
    candidate = ArtifactRef.model_validate(candidate.model_dump(exclude={"content_digest"}) | {
        "producers": (*candidate.producers, model("reviewer", family="b")),
    })
    stopped = finish(pending, repair_report(pending, artifact=candidate))
    assert stopped.artifact == reviewed.artifact
    assert not stopped.resolutions
    assert not evaluate(stopped, current_artifact=candidate, now=at(8)).eligible


@pytest.mark.parametrize("change", [
    {"min_review_passes": 1}, {"min_review_passes": 4, "max_review_passes": 3},
    {"max_implementation_repairs": 4}, {"same_unresolved_failure_limit": 3},
    {"max_tokens": float("inf")}, {"max_cost_microusd": float("nan")},
    {"max_elapsed_seconds": float("inf")}, {"max_invocations": True},
    {"max_tokens": -1}, {"max_elapsed_seconds": 0},
])
def test_policy_has_strict_explicit_finite_caps(change: dict[str, object]) -> None:
    values = session().seed.policy.model_dump(exclude={"content_digest"})
    with pytest.raises(ValidationError):
        ReviewPolicy.model_validate(values | change)


@pytest.mark.parametrize("field", [
    "max_tokens", "max_cost_microusd", "max_elapsed_seconds", "max_invocations",
])
def test_execution_caps_have_no_unlimited_or_silent_defaults(field: str) -> None:
    values = session().seed.policy.model_dump(exclude={"content_digest"})
    values.pop(field)
    with pytest.raises(ValidationError):
        ReviewPolicy.model_validate(values)