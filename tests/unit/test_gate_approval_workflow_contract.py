from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "gate-approval.yml"


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_gate_workflow_requires_two_ordered_environment_jobs() -> None:
    jobs = _workflow()["jobs"]

    assert jobs["first-approval"]["needs"] == "preflight"
    assert jobs["first-approval"]["environment"]["name"] == "${{ inputs.gate }}-triplexapps"
    assert jobs["second-approval"]["needs"] == "first-approval"
    assert jobs["second-approval"]["environment"]["name"] == "${{ inputs.gate }}-chex123"
    assert jobs["record-run-reference"]["needs"] == "second-approval"


def test_preflight_validates_issue_gate_digest_and_dispatch_branch() -> None:
    preflight = _workflow()["jobs"]["preflight"]
    script = preflight["steps"][0]["with"]["script"]

    assert "github.rest.issues.get" in script
    assert "issue.data.pull_request" in script
    assert "['gate-1', 'gate-2', 'gate-3', 'gate-4']" in script
    assert "^sha256:[0-9a-f]{64}$" in script
    assert "DEFAULT_BRANCH" in script
    assert "['triplexapps', 'chex123']" in script
    assert "github.rest.repos.getEnvironment" in script
    assert "protection_rules" in script
    assert "prevent_self_review !== true" in script
    assert "configured.length !== 1" in script
    assert "{ login: 'triplexapps', environment: `${gate}-triplexapps` }" in script
    assert "{ login: 'chex123', environment: `${gate}-chex123` }" in script


def test_final_job_records_only_a_non_authoritative_run_reference() -> None:
    evidence_job = _workflow()["jobs"]["record-run-reference"]
    script = evidence_job["steps"][0]["with"]["script"]

    assert evidence_job["permissions"]["issues"] == "write"
    assert "issues.createComment" in script
    assert "not approval evidence" in script
    assert "issues.addLabels" not in script