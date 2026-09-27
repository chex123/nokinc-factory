"""Actual pytest subprocess observations, not invented model verdicts or cloud evidence."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nokinc_factory.adapters.pytest_probe import PytestEvidenceRecorder
from nokinc_factory.domain.gate_evidence import BaselineContract, ProbeBinding, SuiteRun
from nokinc_factory.policy.baseline import verify_baseline, verify_candidate

pytest_plugins = ("pytester",)

BASE = "a" * 40
HEAD = "b" * 40
RUNNER = "sha256:" + "1" * 64
ENVIRONMENT = "sha256:" + "2" * 64


def _digest(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()


def _run(directory: Path, test_source: str, run_id: str, *, source_sha: str = BASE) -> SuiteRun:
    tests = directory / "test_shipping.py"
    tests.write_text(test_source, encoding="utf-8")
    binding = ProbeBinding(
        work_item_id="synthetic-shipping", run_id=run_id, source_sha=source_sha,
        suite_digest=_digest(test_source), runner_digest=RUNNER, environment_digest=ENVIRONMENT,
    )
    binding_file = directory / f"{run_id}-binding.json"
    result_file = directory / f"{run_id}-evidence.json"
    binding_file.write_text(binding.model_dump_json(), encoding="utf-8")
    environment = {name: value for name, value in os.environ.items() if name.upper() in {
        "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA",
    }}
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
    result = subprocess.run(
        [sys.executable, "-m", "nokinc_factory.adapters.pytest_probe",
         "--binding", str(binding_file), "--report", str(result_file),
         "--", "-q", "-p", "no:cacheprovider", "test_shipping.py"],
        cwd=directory, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result_file.exists(), result.stdout + result.stderr
    evidence = SuiteRun.model_validate_json(result_file.read_text(encoding="utf-8"))
    assert result.returncode == evidence.exit_code
    return evidence


def test_actual_red_then_green_is_distinguished_from_collection_errors(tmp_path: Path) -> None:
    application = tmp_path / "business.py"
    application.write_text("def shipping_total(amount):\n    return amount + 5\n", encoding="utf-8")
    old_tests = (
        "from business import shipping_total\ndef test_small_order():\n"
        "    assert shipping_total(10) == 15\n"
    )
    frozen_tests = old_tests + "def test_free_shipping():\n    assert shipping_total(50) == 50\n"
    before = _run(tmp_path, old_tests, "old")
    baseline = _run(tmp_path, frozen_tests, "baseline")
    contract = BaselineContract(
        work_item_id="synthetic-shipping", story_kind="NEW_BEHAVIOR", baseline_sha=BASE,
        baseline_suite_digest=_digest(old_tests), frozen_suite_digest=_digest(frozen_tests),
        runner_digest=RUNNER, environment_digest=ENVIRONMENT,
        baseline_cases=("test_shipping.py::test_small_order",),
        frozen_cases=("test_shipping.py::test_small_order", "test_shipping.py::test_free_shipping"),
        expected_red_cases=("test_shipping.py::test_free_shipping",),
    )
    assert verify_baseline(contract, before, baseline).status == "PASS"
    assert not verify_baseline(contract, before, baseline).authorizes_merge
    application.write_text(
        "def shipping_total(amount):\n    return amount if amount >= 50 else amount + 5\n",
        encoding="utf-8",
    )
    candidate = _run(tmp_path, frozen_tests, "candidate", source_sha=HEAD)
    assert verify_candidate(contract, HEAD, candidate).status == "PASS"
    collection = _run(tmp_path, "def invalid syntax", "collection")
    assert collection.collection_errors > 0
    assert collection.exit_code != 1
    assert verify_baseline(contract, before, collection).status == "FAIL"


@pytest.mark.parametrize("body,outcome", [
    ("assert 1 == 2", "ASSERTION_FAILURE"),
    ("raise RuntimeError('synthetic failure')", "ERROR"),
    ("pytest.skip('not implemented')", "SKIPPED"),
    ("pytest.xfail('unimplemented')", "SKIPPED"),
    ("assert 4 == 2 + 2", "PASS"),
])
def test_reports_call_outcome_without_raw_failure_text(tmp_path: Path, body, outcome) -> None:
    result = _run(tmp_path, f"import pytest\ndef test_case():\n    {body}\n", "observed")
    assert result.cases[0].outcome == outcome
    assert "synthetic failure" not in result.model_dump_json()


def test_setup_failure_cannot_masquerade_as_intended_assertion(tmp_path: Path) -> None:
    tests = (
        "import pytest\n@pytest.fixture\ndef broken():\n    assert False\n"
        "def test_case(broken):\n    assert True\n"
    )
    result = _run(tmp_path, tests, "setup")
    assert result.cases[0].outcome == "ERROR"


def test_no_test_collection_is_not_a_green_suite(tmp_path: Path) -> None:
    result = _run(tmp_path, "value = 1\n", "empty")
    assert result.exit_code == 5
    assert result.cases == ()


def test_native_hooks_observe_each_phase_without_inventing_an_assertion(pytester) -> None:
    pytester.makepyfile(test_native="""
        import pytest

        def test_pass():
            assert sum([2, 3]) == 5

        def test_assertion():
            assert 2 == 3

        def test_exception():
            raise RuntimeError("synthetic runtime error")

        def test_skip():
            pytest.skip("synthetic unavailable case")

        @pytest.fixture
        def setup_failure():
            assert False

        def test_setup(setup_failure):
            assert True

        @pytest.fixture
        def teardown_failure():
            yield
            assert False

        def test_teardown(teardown_failure):
            assert True
    """)
    recorder = PytestEvidenceRecorder()
    result = pytester.runpytest_inprocess("-q", "-p", "no:cacheprovider", plugins=[recorder])
    evidence = recorder.evidence(ProbeBinding(
        work_item_id="synthetic", run_id="native-hook", source_sha=BASE,
        suite_digest=_digest("native hook cases"), runner_digest=RUNNER,
        environment_digest=ENVIRONMENT,
    ), int(result.ret))
    assert evidence.completed
    assert {case.case_id.rsplit("::", 1)[1]: case.outcome for case in evidence.cases} == {
        "test_pass": "PASS", "test_assertion": "ASSERTION_FAILURE", "test_exception": "ERROR",
        "test_skip": "SKIPPED", "test_setup": "ERROR", "test_teardown": "ERROR",
    }