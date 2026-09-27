"""The factory workflow must consume structured evidence, not shell exit folklore."""

from pathlib import Path

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "gates.yml"


def test_baseline_job_uses_structured_evidence_policy() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "nokinc_factory.adapters.pytest_evidence" in source
    assert "python -m nokinc_factory.cli verify-tests baseline" in source
    assert "pytest_probe" in source
    assert "Structured baseline evidence" in source


def test_baseline_job_does_not_accept_any_nonzero_pytest_exit() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "if pytest tests/acceptance -q; then" not in source
    assert "Correct - new scenarios fail on the true baseline" not in source


def test_frozen_contract_is_not_only_label_optional() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "frozen-contract" in source
    assert "implementation" in source
    assert "changed files" in source


def test_baseline_job_keeps_candidate_tools_when_testing_the_base_revision() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "git worktree add" in source
    assert 'git checkout -f "origin/${{ github.base_ref }}"' not in source
    assert "PYTHONPATH" in source


def test_frozen_path_policy_does_not_depend_on_mutable_labels() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "contains(github.event.pull_request.labels.*.name, 'frozen-contract')" not in source
    assert "contains(github.event.pull_request.labels.*.name, 'implementation')" not in source
    assert "FROZEN_CHANGED" in source


def test_ci_runs_pytest_as_a_python_module() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "run: python -m pytest tests/unit -q" in source
    assert "run: python -m pytest tests/acceptance -q" in source
    assert "python -m pytest --cov=src" in source

def test_ci_installs_runtime_extras_and_typechecks_deployment_script() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert 'pip install -e ".[dev,phase1,phase2]"' in source
    assert "mypy --strict src scripts/deploy_pilot.py" in source
    assert "ruff check src tests scripts" in source
    assert "python -m compileall -q src scripts" in source