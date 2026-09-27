"""CLI inspection is useful evidence browsing, never a fabricated live authorization."""

import json

import pytest
from test_review_fixtures import at, clean, session

from nokinc_factory.cli import main


def snapshot(tmp_path):
    value = clean(clean(session(), 1), 2)
    path = tmp_path / "review.json"
    path.write_text(value.model_dump_json(), encoding="utf-8")
    return path, value


def test_status_requires_explicit_backend_or_snapshot(capsys) -> None:
    assert main(["status"]) == 2
    assert "not configured" in capsys.readouterr().err.lower()


def test_status_reads_real_snapshot_without_claiming_live_authority(tmp_path, capsys) -> None:
    path, value = snapshot(tmp_path)
    assert main(["status", "--review-session", str(path)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["evidence_scope"] == "ARCHIVED_ADVISORY"
    assert data["live_artifact_verified"] is False
    assert data["deployment_authorized"] is False
    assert data["session_digest"] == value.content_digest
    assert data["completed_reviews"] == 2
    assert data["work_item_id"] == value.seed.scope.work_item_id


def test_trace_includes_ordered_attempt_digests_not_report_text(tmp_path, capsys) -> None:
    path, value = snapshot(tmp_path)
    assert main(["trace", "work-1", "--review-session", str(path)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["attempts"]) == 2
    assert data["contract_digest"] == value.seed.contract.content_digest
    assert data["artifact_digest"] == value.artifact.content_digest
    assert data["attempts"][0]["invocation_id"] == "call-1"
    assert data["release_chain_complete"] is False
    assert "raw_text" not in json.dumps(data)
    assert "ignore all previous rules" not in json.dumps(data)


def test_trace_refuses_wrong_work_item_identity(tmp_path, capsys) -> None:
    path, _ = snapshot(tmp_path)
    assert main(["trace", "different-work-item", "--review-session", str(path)]) == 2
    assert "identity" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("payload", ["not json", "{}", '{"content_digest":"forged"}'])
def test_malformed_snapshot_has_no_traceback_or_raw_payload(tmp_path, capsys, payload) -> None:
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    assert main(["status", "--review-session", str(path)]) == 2
    captured = capsys.readouterr()
    assert "invalid" in captured.err.lower()
    assert captured.out == ""
    assert "forged" not in captured.err


def test_missing_or_oversized_snapshot_fails_closed(tmp_path, capsys) -> None:
    missing = tmp_path / "not-there.json"
    assert main(["status", "--review-session", str(missing)]) == 2
    large = tmp_path / "large.json"
    large.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    assert main(["status", "--review-session", str(large)]) == 2
    assert "invalid" in capsys.readouterr().err.lower()


def test_summary_uses_injected_time_and_checks_digest(tmp_path) -> None:
    from nokinc_factory.application.review_inspection import inspect_review
    _, value = snapshot(tmp_path)
    info = inspect_review(value, now=at(8))
    assert info.snapshot_decision.eligible
    assert not info.deployment_authorized
    forged = value.model_copy(update={"content_digest": "sha256:" + "0" * 64})
    with pytest.raises(ValueError):
        inspect_review(forged, now=at(8))


@pytest.mark.parametrize("command", ["status", "trace"])
@pytest.mark.parametrize("timestamp", [
    "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00",
])
def test_out_of_range_utc_conversion_is_a_sanitized_archive_error(
    tmp_path, capsys, command, timestamp,
) -> None:
    path, value = snapshot(tmp_path)
    data = value.model_dump(mode="json")
    data["seed"]["started_at"] = timestamp
    path.write_text(json.dumps(data), encoding="utf-8")
    args = [command, *(["work-1"] if command == "trace" else []), "--review-session", str(path)]
    assert main(args) == 2
    captured = capsys.readouterr()
    assert "invalid" in captured.err.lower()
    assert "Traceback" not in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("command", ["status", "trace"])
def test_unrepresentable_archive_arithmetic_is_sanitized(tmp_path, capsys, command) -> None:
    path, value = snapshot(tmp_path)
    data = value.model_dump(mode="json")
    data["seed"]["policy"]["max_elapsed_seconds"] = 10**400
    for changed in (data, data["seed"], data["seed"]["policy"]):
        changed.pop("content_digest")
    path.write_text(json.dumps(data), encoding="utf-8")
    args = [command, *(["work-1"] if command == "trace" else []), "--review-session", str(path)]
    assert main(args) == 2
    captured = capsys.readouterr()
    assert "invalid" in captured.err.lower()
    assert captured.out == ""


@pytest.mark.parametrize("payload", [
    '{"attempts":[], "attempts":[]}', '{"attempts":[], "value":NaN}',
    json.dumps({"attempts": [{}] * 65}),
])
def test_ambiguous_nonstandard_or_excessive_archive_is_rejected(tmp_path, capsys, payload) -> None:
    path = tmp_path / "rejected.json"
    path.write_text(payload, encoding="utf-8")
    assert main(["status", "--review-session", str(path)]) == 2
    assert "invalid" in capsys.readouterr().err.lower()