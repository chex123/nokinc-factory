"""Pytest-native observations for the scoped baseline gate (spec Part 8).

Run this inside an isolated unprivileged gate worker with approved frozen tests.
The outer worker pins/verifies the binding, provides a hard process timeout and
owns evidence retention; this module is NOT a sandbox, signer or approval port.
Model/candidate code must never choose binding values or gain worker credentials.
Only structured IDs/outcomes are emitted, never raw exception text or test data.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Generator, Sequence
from pathlib import Path

import pytest

from nokinc_factory.adapters.gate_evidence_io import read_evidence
from nokinc_factory.domain.gate_evidence import CaseOutcome, CaseResult, ProbeBinding, SuiteRun


class PytestEvidenceRecorder:
    def __init__(self) -> None:
        self.outcomes: dict[str, CaseOutcome | None] = {}
        self.calls: set[str] = set()
        self.collection_errors = 0
        self.finished = False

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed or report.skipped:
            self.collection_errors += 1

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        for item in session.items:
            if item.nodeid in self.outcomes:
                self.collection_errors += 1
            self.outcomes[item.nodeid] = None

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None],
    ) -> Generator[None, pytest.TestReport, pytest.TestReport]:
        report = yield
        previous = self.outcomes.get(item.nodeid)
        if report.when == "call":
            if item.nodeid in self.calls:
                previous = "ERROR"
            self.calls.add(item.nodeid)
        observed: CaseOutcome | None = None
        if report.skipped or getattr(report, "wasxfail", None) is not None:
            observed = "SKIPPED"
        elif report.failed:
            assertion = call.excinfo is not None and call.excinfo.errisinstance(AssertionError)
            observed = "ASSERTION_FAILURE" if report.when == "call" and assertion else "ERROR"
        elif report.when == "call" and report.passed:
            observed = "PASS"
        priority = {None: 0, "PASS": 1, "ASSERTION_FAILURE": 2, "SKIPPED": 3, "ERROR": 4}
        self.outcomes[item.nodeid] = (
            observed if priority[observed] > priority[previous] else previous
        )
        return report

    def pytest_sessionfinish(self) -> None:
        self.finished = True

    def evidence(self, binding: ProbeBinding, exit_code: int) -> SuiteRun:
        values = binding.model_dump(exclude={"content_digest"})
        return SuiteRun.model_validate(values | {
            "completed": self.finished, "exit_code": exit_code,
            "collection_errors": self.collection_errors,
            "cases": tuple(CaseResult(case_id=name, outcome=outcome or "ERROR")
                           for name, outcome in sorted(self.outcomes.items())),
        })


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("pytest_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        binding = read_evidence(args.binding, ProbeBinding, max_bytes=32_768)
        arguments = args.pytest_arguments
        if not arguments or arguments[0] != "--" or len(arguments) < 2:
            raise ValueError("Explicit pytest arguments are required")
        with args.report.open("x", encoding="utf-8") as output:
            recorder = PytestEvidenceRecorder()
            exit_code = int(pytest.main(arguments[1:], plugins=[recorder]))
            output.write(recorder.evidence(binding, exit_code).model_dump_json())
        return exit_code
    except (ValueError, OSError, RecursionError):
        print("Invalid probe binding, arguments or evidence output; no gate pass.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())