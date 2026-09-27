"""Opt-in readiness regressions, not part of the existing green baseline.

Run explicitly with pytest. Failures expose missing safety properties; they are
not xfailed, skipped, or reported as passes. No provider calls or workspace Git
mutations occur. The Git probe uses only a disposable pytest directory.
"""

import sys
from base64 import b64decode
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import run

import pytest
from pydantic import ValidationError

from nokinc_factory.adapters.git_candidate import GitCandidateCapturer
from nokinc_factory.cli import main
from nokinc_factory.domain.authorization import (
    ActuatorClass,
    Authorization,
    ExpiredAuthorization,
    OnExpiry,
    TargetBinding,
)
from nokinc_factory.domain.preflight import TaskContext
from nokinc_factory.domain.states import IllegalTransition, Transition, WorkItemState
from nokinc_factory.domain.story import (
    BusinessReady,
    DataClassification,
    NonFunctionalTarget,
    RiskTier,
    Scenario,
)
from nokinc_factory.domain.story import TestDataNeed as DataNeed
from nokinc_factory.policy.impact import (
    Evidence,
    FileChange,
    ImpactClass,
    SecuritySensitiveRegistry,
    classify,
)

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def task_context() -> TaskContext:
    return TaskContext.create(
        provider="github",
        repository="audit/synthetic",
        work_item_id="1",
        title="Synthetic readiness probe",
        body="Synthetic data only; no external action authorized.",
        labels=(),
        source_url="https://github.com/audit/synthetic/issues/1",
    )


def test_status_command_is_operational() -> None:
    assert main(["status"]) == 0, "The user-facing status command is still a stub"


@pytest.mark.parametrize("reverse", [False, True])
def test_cosmetic_file_cannot_hide_ordinary_code_change(reverse: bool) -> None:
    changes = [
        FileChange(path="src/calc.py", added_lines=["return amount + 1"]),
        FileChange(path="src/notes.py", added_lines=["# clarify explanation"]),
    ]
    result = classify(changes[::-1] if reverse else changes, SecuritySensitiveRegistry())
    assert ImpactClass.ORDINARY_IMPLEMENTATION in result.classes
    assert Evidence.UNIT_TESTS in result.invalidated
    assert Evidence.ACCEPTANCE_TESTS in result.invalidated


def test_executable_statement_after_docstring_is_not_cosmetic() -> None:
    result = classify(
        [FileChange(path="src/calc.py", added_lines=['"""note"""; perform_work()'])],
        SecuritySensitiveRegistry(),
    )
    assert result.classes != {ImpactClass.COSMETIC}
    assert Evidence.UNIT_TESTS in result.invalidated


def test_blank_approval_reference_cannot_exit_human_gate() -> None:
    with pytest.raises((IllegalTransition, ValidationError)):
        transition = Transition(
            work_item_id="1",
            workflow_run_id="synthetic-run",
            transition_id="synthetic-transition",
            expected_current=WorkItemState.BUSINESS_READY,
            target=WorkItemState.DESIGNING,
            event_id="synthetic-event",
            actor="synthetic-actor",
            approval_id="",
            occurred_at=NOW,
        )
        transition.validate_against(WorkItemState.BUSINESS_READY)


@pytest.mark.parametrize("timing", ["future", "exact-expiry"])
def test_authorization_is_valid_only_within_issued_window(timing: str) -> None:
    authorization = Authorization(
        decision_id="synthetic-decision",
        action="isolate_replica",
        actuator_class=ActuatorClass.SECURITY_EXTERNAL,
        target=TargetBinding(service="synthetic", target_runtime_uid="synthetic-instance"),
        evidence_snapshot="synthetic-evidence",
        coverage_snapshot="synthetic-coverage",
        policy_version="synthetic-policy",
        authorized_at=NOW + timedelta(minutes=1) if timing == "future" else NOW,
        expires_at=NOW + timedelta(minutes=5),
        max_staleness_seconds=3600,
        on_expiry=OnExpiry.HOLD_AND_ESCALATE,
        nonce="synthetic-nonce",
        signature="not-a-real-signature",
    )
    with pytest.raises(ExpiredAuthorization):
        authorization.revalidate(
            authorization.target,
            NOW if timing == "future" else authorization.expires_at,
        )


def test_unresolved_business_questions_prevent_ready_payload() -> None:
    with pytest.raises(ValidationError):
        BusinessReady(
            work_item_id="1",
            problem_and_value="Make a synthetic calculation reproducible",
            scope_in=["Calculation"],
            scope_out=["External transactions"],
            scenarios=[
                Scenario(
                    name="Invalid input",
                    gherkin="Given invalid input\nWhen calculated\nThen reject the request",
                    is_failure_case=True,
                )
            ],
            business_rules=["Reject invalid input"],
            test_data_needs=[
                DataNeed(description="Invalid integers", source="synthetic", volume="3")
            ],
            nfr_impact=[NonFunctionalTarget(metric="latency", target="no change", unchanged=True)],
            data_classification=DataClassification.NONE,
            preliminary_tier=RiskTier.T1,
            rough_size="S",
            size_confidence="high",
            open_questions=["Which inputs are valid?"],
        )


def test_task_context_mutation_cannot_preserve_stale_digest() -> None:
    context = task_context()
    original_digest = context.content_digest
    try:
        context.body = "Different synthetic request"
    except ValidationError:
        return
    assert context.content_digest != original_digest, "Changed context retains old evidence digest"


def test_git_capture_does_not_execute_text_conversion(tmp_path: Path) -> None:
    """A harmless converter marker proves whether capture executes a helper."""

    def git(*args: str) -> bytes:
        return run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True
        ).stdout

    git("init", "-q")
    git("config", "user.name", "Synthetic Audit")
    git("config", "user.email", "synthetic@example.invalid")
    git("config", "core.autocrlf", "false")
    (tmp_path / ".gitattributes").write_text("*.audit diff=audit\n", encoding="utf-8")
    content = tmp_path / "sample.audit"
    content.write_text("before\n", encoding="utf-8")
    git("add", ".gitattributes", "sample.audit")
    git("-c", "core.hooksPath=", "commit", "-qm", "synthetic baseline")
    converter = tmp_path / "converter.py"
    converter.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[0]).with_name('converter-invoked').write_text('called')\n"
        "print('synthetic fixed diff')\n",
        encoding="utf-8",
    )
    git(
        "config", "diff.audit.textconv",
        f'"{Path(sys.executable).as_posix()}" "{converter.as_posix()}"',
    )
    content.write_text("after\n", encoding="utf-8")

    candidate = GitCandidateCapturer().capture(
        tmp_path, base_ref="HEAD", task_context=task_context()
    )
    assert not (tmp_path / "converter-invoked").exists(), "Git capture executed a helper"
    assert candidate.unstaged.paths == ("sample.audit",)
    patch = b64decode(candidate.unstaged.patch_base64)
    assert b"-before" in patch and b"+after" in patch, "Raw changed bytes were not captured"