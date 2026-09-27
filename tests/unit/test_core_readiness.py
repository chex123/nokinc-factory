"""A01 / R01-R03: per-file impact and lifecycle reference regressions."""

from datetime import UTC, datetime

import pytest

from nokinc_factory.domain.states import (
    HUMAN_GATES,
    IllegalTransition,
    StaleTransition,
    Transition,
    WorkItemState,
    allowed_from,
)
from nokinc_factory.policy.impact import (
    Evidence,
    FileChange,
    ImpactClass,
    SecuritySensitiveRegistry,
    classify,
)

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("src/notes.py", "# clarify the calculation"),
        ("src/check.py", "if user.id == order.owner:"),
        ("infra/main.tf", 'resource "example" "worker" {}'),
        ("migrations/001.sql", "ALTER TABLE records ADD value INT;"),
        ("data/mystery.unknown", "changed bytes"),
    ],
    ids=["cosmetic", "authorization", "iac", "migration", "unknown"],
)
def test_each_file_contributes_its_own_impact(path: str, line: str, reverse: bool) -> None:
    """Another file's class must not suppress ordinary code's test invalidation."""
    registry = SecuritySensitiveRegistry()
    ordinary = FileChange(path="src/calc.py", added_lines=["return amount + 1"])
    other = FileChange(path=path, added_lines=[line])
    first, second = (classify([change], registry) for change in (ordinary, other))
    expected_classes = (first.classes | second.classes) - {ImpactClass.COSMETIC}
    changes = [ordinary, other]

    result = classify(changes[::-1] if reverse else changes, registry)

    assert result.classes == expected_classes
    assert result.invalidated == first.invalidated | second.invalidated
    assert {Evidence.UNIT_TESTS, Evidence.ACCEPTANCE_TESTS} <= result.invalidated


@pytest.mark.parametrize("surface,dependency", [(True, False), (False, True), (True, True)])
def test_global_signals_do_not_hide_ordinary_implementation(
    surface: bool, dependency: bool,
) -> None:
    result = classify(
        [FileChange(path="src/calc.py", removed_lines=["return amount + 1"])],
        SecuritySensitiveRegistry(),
        surface_changed=surface,
        new_dependencies=dependency,
    )

    assert ImpactClass.ORDINARY_IMPLEMENTATION in result.classes
    assert {Evidence.UNIT_TESTS, Evidence.ACCEPTANCE_TESTS} <= result.invalidated
    assert (ImpactClass.INTERFACE_SURFACE in result.classes) is surface
    assert (ImpactClass.NEW_DEPENDENCY in result.classes) is dependency


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("src/calc.py", '\"\"\"note\"\"\"; perform_work()'),
        ("src/calc.py", "'''note'''.format(perform_work())"),
        ("src/calc.py", '\"\"\"unproven docstring\"\"\"'),
        ("src/calc.py", "*values, = compute()"),
        ("src/calc.py", "// divisor"),
        ("src/calc.py", "++# not a comment-only diff line"),
        ("src/calc.py", "# note\nperform_work()"),
        ("src/calc.js", "// note\nperform_work()"),
        ("src/calc.js", "/* note */ perform_work();"),
        ("src/calc.js", "/* an unclosed comment"),
        ("src/calc.ts", "#value = compute();"),
        ("src/calc.go", "*value = compute()"),
        ("config.yaml", "*defaults"),
        ("config.json", "# not a JSON comment"),
        ("guide.md", "# Visible heading"),
        ("output.txt", "# Visible output"),
    ],
)
def test_executable_or_unproved_prefixes_are_not_cosmetic(path: str, line: str) -> None:
    change = FileChange(path=path, added_lines=[line])

    assert not change.is_cosmetic
    result = classify([change], SecuritySensitiveRegistry())
    assert ImpactClass.COSMETIC not in result.classes
    assert {Evidence.UNIT_TESTS, Evidence.ACCEPTANCE_TESTS} <= result.invalidated


@pytest.mark.parametrize("prefixed", [False, True])
@pytest.mark.parametrize(
    ("extension", "comment"),
    [("py", "# note"), ("js", "// note"), ("rb", "# note"), ("tf", "# note")],
)
def test_recognized_line_comments_preserve_cosmetic_contract(
    extension: str, comment: str, prefixed: bool,
) -> None:
    change = FileChange(
        path=f"src/notes.{extension}",
        added_lines=[("+" if prefixed else "") + "  " + comment, ""],
        removed_lines=[("-" if prefixed else "") + comment],
    )

    assert change.is_cosmetic
    result = classify([change], SecuritySensitiveRegistry())
    assert result.classes == {ImpactClass.COSMETIC}
    assert result.invalidated == set()


def test_removed_diff_marker_does_not_strip_source_operators() -> None:
    change = FileChange(path="src/calc.py", removed_lines=["--# expression fragment"])
    assert not change.is_cosmetic


def test_java_unicode_escape_cannot_hide_an_executable_statement() -> None:
    """Java resolves Unicode escapes before recognizing line comments."""
    change = FileChange(path="src/Calc.java", added_lines=[r"// note \u000a perform_work();"])
    assert not change.is_cosmetic
    result = classify([change], SecuritySensitiveRegistry())
    assert Evidence.UNIT_TESTS in result.invalidated


def test_unknown_comment_file_still_requires_human_review() -> None:
    result = classify(
        [FileChange(path="src/notes.unknown", added_lines=["# note"])],
        SecuritySensitiveRegistry(),
    )
    assert result.requires_human
    assert result.invalidated == set(Evidence)


def _transition(
    expected: WorkItemState, target: WorkItemState, approval: str | None,
) -> Transition:
    return Transition(
        work_item_id="synthetic-story",
        workflow_run_id="synthetic-run",
        transition_id="synthetic-transition",
        expected_current=expected,
        target=target,
        event_id="synthetic-event",
        actor="synthetic-actor",
        approval_id=approval,
        occurred_at=NOW,
    )


@pytest.mark.parametrize("approval", [None, "", " \t\n\u2003"])
@pytest.mark.parametrize(
    ("expected", "target"),
    [(state, target) for state in sorted(HUMAN_GATES) for target in sorted(allowed_from(state))],
)
def test_every_human_gate_exit_requires_nonblank_reference(
    expected: WorkItemState, target: WorkItemState, approval: str | None,
) -> None:
    with pytest.raises(IllegalTransition, match="approval"):
        _transition(expected, target, approval).validate_against(expected)


@pytest.mark.parametrize("state", sorted(HUMAN_GATES))
def test_nonblank_reference_remains_presence_only_not_verified_authority(
    state: WorkItemState,
) -> None:
    transition = _transition(state, sorted(allowed_from(state))[0], "unverified-reference")
    transition.validate_against(state)
    assert transition.approval_id == "unverified-reference"


def test_nonhuman_transition_does_not_acquire_an_approval_requirement() -> None:
    _transition(WorkItemState.NEW, WorkItemState.REFINING, "").validate_against(
        WorkItemState.NEW
    )


def test_cas_and_legality_still_precede_approval_presence() -> None:
    transition = _transition(WorkItemState.BUSINESS_READY, WorkItemState.RELEASING, "")
    with pytest.raises(StaleTransition):
        transition.validate_against(WorkItemState.SOLUTION_READY)
    with pytest.raises(IllegalTransition, match="not permitted"):
        transition.validate_against(WorkItemState.BUSINESS_READY)