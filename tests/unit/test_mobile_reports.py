"""Mobile gates must not turn empty, skipped or malformed evidence into PASS."""

import pytest

from nokinc_factory.adapters.maestro_reports import InvalidMaestroReport, parse_report


def test_complete_junit_reports_successful_flows() -> None:
    report = parse_report(
        b'<testsuite tests="2" failures="0" errors="0" skipped="0">'
        b'<testcase name="login" status="SUCCESS"/>'
        b'<testcase name="denied" status="SUCCESS"/></testsuite>',
        expected_tests=("login", "denied"),
    )
    assert report.tests == 2
    assert report.passed
    assert report.failed == report.skipped == 0


def test_nested_suites_count_cases_once() -> None:
    report = parse_report(
        b'<testsuites tests="1"><testsuite tests="1">'
        b'<testcase name="login"/></testsuite></testsuites>',
        expected_tests=("login",),
    )
    assert report.passed
    assert report.tests == 1


@pytest.mark.parametrize("element", ["failure", "error", "skipped"])
def test_failed_or_skipped_case_is_not_pass(element: str) -> None:
    report = parse_report(
        f'<testsuite tests="1"><testcase name="login"><{element}/>'
        '</testcase></testsuite>'.encode(),
        expected_tests=("login",),
    )
    assert not report.passed


@pytest.mark.parametrize(
    "xml",
    [
        b"not xml", b"<testsuite/>", b'<testsuite tests="1"/>',
        b'<testsuite tests="2"><testcase name="login"/></testsuite>',
        b'<testsuite failures="1"><testcase name="login"/></testsuite>',
        b'<testsuite tests="-1"><testcase name="login"/></testsuite>',
        b'<testsuite><testcase name="login"/><testcase name="login"/></testsuite>',
        b'<testsuite><testcase name="unexpected"/></testsuite>',
        b'<testsuite><testcase name="login" status="RUNNING"/></testsuite>',
        b'<testsuite><testcase name="login" status="SKIPPED"/></testsuite>',
        b'<testsuite disabled="1"><testcase name="login"/></testsuite>',
        b'<testsuite><testcase/></testsuite>',
        b'<unrecognized><testcase name="login"/></unrecognized>',
        b'<!DOCTYPE testsuite [<!ENTITY x "expanded">]>'
        b'<testsuite><testcase name="&x;"/></testsuite>',
        b'\xff\xfe<testsuite/>',
    ],
)
def test_invalid_or_incomplete_report_fails_closed(xml: bytes) -> None:
    with pytest.raises(InvalidMaestroReport):
        parse_report(xml, expected_tests=("login",))


def test_oversized_report_fails_before_parsing() -> None:
    with pytest.raises(InvalidMaestroReport, match="size"):
        parse_report(b"x" * 2_000_001, expected_tests=("login",))


def test_missing_one_expected_flow_fails_closed() -> None:
    with pytest.raises(InvalidMaestroReport, match="inventory"):
        parse_report(
            b'<testsuite tests="1"><testcase name="login"/></testsuite>',
            expected_tests=("login", "denied"),
        )


@pytest.mark.parametrize(
    "xml",
    [
        b'<testsuite><properties><testcase name="login"/></properties></testsuite>',
        b'<testsuite><testcase name="login"><outcome><failure/></outcome>'
        b'</testcase></testsuite>',
        b'<testsuite xmlns:x="synthetic"><testcase name="login"><x:failure/>'
        b'</testcase></testsuite>',
        b'<testsuite><testcase name="login"><unknown/></testcase></testsuite>',
    ],
)
def test_unsupported_structure_never_becomes_success(xml: bytes) -> None:
    with pytest.raises(InvalidMaestroReport):
        parse_report(xml, expected_tests=("login",))


def test_deeply_nested_report_is_rejected_within_structure_limit() -> None:
    xml = b"<testsuite>" * 100 + b'<testcase name="login"/>' + b"</testsuite>" * 100
    with pytest.raises(InvalidMaestroReport, match="structure"):
        parse_report(xml, expected_tests=("login",))


def test_supported_metadata_does_not_hide_test_outcome() -> None:
    xml = (
        b'<testsuite><properties><property name="synthetic" value="yes"/></properties>'
        b'<testcase name="login"><properties><property name="id" value="1"/>'
        b'</properties><system-out>restricted</system-out></testcase></testsuite>'
    )
    assert parse_report(xml, expected_tests=("login",)).passed