"""A01 / R05: explicit blockers and blank content are not Business Ready."""

import pytest
from pydantic import ValidationError

from nokinc_factory.domain.story import BusinessReady, DataClassification, RiskTier


def _payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "work_item_id": "synthetic-story",
        "problem_and_value": "Make a synthetic calculation reproducible",
        "scope_in": ["Calculation"],
        "scope_out": ["External transactions"],
        "scenarios": [{
            "name": "Invalid input",
            "gherkin": "Given invalid input When calculated Then reject the request",
            "is_failure_case": True,
        }],
        "business_rules": ["Reject invalid input"],
        "test_data_needs": [
            {"description": "Invalid integers", "source": "fixture", "volume": "3"},
        ],
        "nfr_impact": [{"metric": "latency", "target": "unchanged", "unchanged": True}],
        "data_classification": DataClassification.NONE,
        "preliminary_tier": RiskTier.T1,
        "rough_size": "S",
        "size_confidence": "high",
    }
    payload.update(updates)
    return payload


@pytest.mark.parametrize("questions", [["Which inputs are valid?"], [""], [" \t"]])
def test_open_questions_prevent_business_ready(questions: list[str]) -> None:
    with pytest.raises(ValidationError, match="open_questions"):
        BusinessReady.model_validate(_payload(open_questions=questions))


@pytest.mark.parametrize(
    "field", ["work_item_id", "problem_and_value", "rough_size", "size_confidence"],
)
@pytest.mark.parametrize("blank", ["", " \t\n\u2003"])
def test_required_scalar_text_cannot_be_blank(field: str, blank: str) -> None:
    with pytest.raises(ValidationError, match=field):
        BusinessReady.model_validate(_payload(**{field: blank}))


@pytest.mark.parametrize("field", ["scope_in", "scope_out"])
def test_both_scope_boundaries_must_be_explicit(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        BusinessReady.model_validate(_payload(**{field: []}))


@pytest.mark.parametrize("field", ["scope_in", "scope_out", "business_rules", "known_constraints"])
@pytest.mark.parametrize("blank", ["", " \t"])
def test_list_entries_cannot_hide_blank_required_facts(field: str, blank: str) -> None:
    with pytest.raises(ValidationError, match=field):
        BusinessReady.model_validate(_payload(**{field: ["established fact", blank]}))


@pytest.mark.parametrize("field", ["name", "gherkin"])
@pytest.mark.parametrize("blank", ["", " \t"])
def test_failure_scenario_requires_actual_content(field: str, blank: str) -> None:
    scenario: dict[str, object] = {
        "name": "Invalid input",
        "gherkin": "Given invalid input When calculated Then reject",
        "is_failure_case": True,
    }
    scenario[field] = blank
    with pytest.raises(ValidationError, match=field):
        BusinessReady.model_validate(_payload(scenarios=[scenario]))


@pytest.mark.parametrize("field", ["description", "source", "volume"])
def test_declared_test_data_needs_cannot_be_blank(field: str) -> None:
    need = {"description": "Invalid integers", "source": "fixture", "volume": "3"}
    need[field] = " \t"
    with pytest.raises(ValidationError, match=field):
        BusinessReady.model_validate(_payload(test_data_needs=[need]))


@pytest.mark.parametrize("field", ["metric", "target"])
def test_declared_nfr_requires_content_even_for_no_change(field: str) -> None:
    target: dict[str, object] = {"metric": "latency", "target": "unchanged", "unchanged": True}
    target[field] = " \t"
    with pytest.raises(ValidationError, match=field):
        BusinessReady.model_validate(_payload(nfr_impact=[target]))


@pytest.mark.parametrize("scenarios", [[], [{"name": "Success", "gherkin": "Given x Then y"}]])
def test_existing_failure_case_requirement_remains(scenarios: list[dict[str, object]]) -> None:
    with pytest.raises(ValidationError, match="failure or edge"):
        BusinessReady.model_validate(_payload(scenarios=scenarios))


def test_existing_fixture_and_explicit_no_change_remain_valid() -> None:
    payload = _payload()
    story = BusinessReady.model_validate(payload)
    assert story.open_questions == []
    assert story.known_constraints == []
    assert story.nfr_impact[0].unchanged
    assert BusinessReady.model_validate_json(story.model_dump_json()) == story


def test_empty_optional_collections_do_not_acquire_new_requirements() -> None:
    story = BusinessReady.model_validate(_payload(
        business_rules=[], test_data_needs=[], nfr_impact=[],
        known_constraints=[], open_questions=[],
    ))
    assert story.business_rules == []
    assert story.test_data_needs == []
    assert story.nfr_impact == []


def test_nonblank_content_is_not_silently_normalized() -> None:
    story = BusinessReady.model_validate(_payload(problem_and_value="  Established value  "))
    assert story.problem_and_value == "  Established value  "