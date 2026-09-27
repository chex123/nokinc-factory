"""Bounded, fail-closed Maestro JUnit ingestion; never trust the process exit alone.

Maestro emits UTF-8 JUnit with testcase names. Require exactly the approved flow
inventory and internally consistent counters; reject skipped/disabled/unknown
outcomes. Reject DTDs/entities before ElementTree can expand them. Raw XML and
failure messages stay in the restricted artifact directory, not user-facing logs.
"""

from dataclasses import dataclass
from xml.etree import ElementTree as ET

MAX_REPORT_BYTES = 2_000_000
MAX_REPORT_ELEMENTS = 10_000
MAX_REPORT_DEPTH = 32
_CHILDREN = {
    "testsuites": {"testsuite", "testsuites", "properties", "system-out", "system-err"},
    "testsuite": {"testsuite", "testcase", "properties", "system-out", "system-err"},
    "testcase": {"properties", "system-out", "system-err", "failure", "error", "skipped"},
    "properties": {"property"},
    "property": set(), "system-out": set(), "system-err": set(),
    "failure": set(), "error": set(), "skipped": set(),
}


class InvalidMaestroReport(ValueError):
    """Unusable evidence is a failed gate, not a successful zero-test run."""


@dataclass(frozen=True)
class ReportSummary:
    tests: int
    failed: int
    skipped: int

    @property
    def passed(self) -> bool:
        return self.tests > 0 and self.failed == 0 and self.skipped == 0


def parse_report(content: bytes, *, expected_tests: tuple[str, ...]) -> ReportSummary:
    """Parse the supported JUnit subset; unsupported producer formats fail closed."""
    if not content or len(content) > MAX_REPORT_BYTES:
        raise InvalidMaestroReport("report size is invalid")
    try:
        text = content.decode("utf-8-sig", errors="strict")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise InvalidMaestroReport("DTD and entity declarations are forbidden")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError) as exc:
        raise InvalidMaestroReport("report is not supported UTF-8 JUnit XML") from exc
    if root.tag not in {"testsuite", "testsuites"}:
        raise InvalidMaestroReport("report root must be testsuite or testsuites")
    elements = _validated_structure(root)
    cases = [element for element in elements if element.tag == "testcase"]
    names = [case.get("name", "") for case in cases]
    if (
        not expected_tests or len(set(expected_tests)) != len(expected_tests)
        or len(names) != len(expected_tests) or set(names) != set(expected_tests)
        or len(set(names)) != len(names)
    ):
        raise InvalidMaestroReport("report does not match the expected test inventory")
    for case in cases:
        if case.get("status", "SUCCESS") not in {"SUCCESS", "FAILED", "FAILURE", "ERROR"}:
            raise InvalidMaestroReport("unknown or skipped testcase status")
        if case.get("status") in {"FAILED", "FAILURE", "ERROR"} and not (
            case.find("failure") is not None or case.find("error") is not None
        ):
            raise InvalidMaestroReport("inconsistent testcase failure status")
    _validate_counts(elements)
    failed = sum(
        case.find("failure") is not None or case.find("error") is not None for case in cases
    )
    skipped = sum(case.find("skipped") is not None for case in cases)
    return ReportSummary(tests=len(cases), failed=failed, skipped=skipped)


def _validated_structure(root: ET.Element) -> list[ET.Element]:
    pending = [(root, 0)]
    elements: list[ET.Element] = []
    while pending:
        element, depth = pending.pop()
        if depth > MAX_REPORT_DEPTH or len(elements) >= MAX_REPORT_ELEMENTS:
            raise InvalidMaestroReport("report exceeds supported structure limits")
        elements.append(element)
        for child in element:
            if child.tag not in _CHILDREN[element.tag]:
                raise InvalidMaestroReport("unsupported report element structure")
            pending.append((child, depth + 1))
    return elements


def _validate_counts(elements: list[ET.Element]) -> None:
    """Postorder aggregation avoids rescanning every nested suite's descendants."""
    counts: dict[ET.Element, tuple[int, ...]] = {}
    keys = ("tests", "failures", "errors", "skipped", "disabled")
    for element in reversed(elements):
        if element.tag == "testcase":
            counts[element] = (
                1, int(element.find("failure") is not None),
                int(element.find("error") is not None),
                int(element.find("skipped") is not None), 0,
            )
        elif element.tag in {"testsuites", "testsuite"}:
            children = [counts[child] for child in element if child in counts]
            totals = tuple(sum(child[n] for child in children) for n in range(len(keys)))
            counts[element] = totals
            for key, actual in zip(keys, totals, strict=True):
                value = element.get(key)
                if value is not None and (
                    len(value) > 7 or not value.isascii() or not value.isdecimal()
                    or int(value) != actual
                ):
                    raise InvalidMaestroReport("inconsistent JUnit counters")