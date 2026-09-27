"""The gate CLI evaluates bounded evidence, not human or provider authorization."""

import json
from pathlib import Path

import pytest
from test_baseline_evidence import CANDIDATE, contract, suite

from nokinc_factory.cli import main
from nokinc_factory.domain.review_base import ReviewModel


def _write(directory: Path, name: str, value: ReviewModel) -> str:
    path = directory / name
    path.write_text(value.model_dump_json(), encoding="utf-8")
    return str(path)


def test_baseline_cli_validates_expected_red_without_granting_authority(tmp_path, capsys) -> None:
    args = ["verify-tests", "baseline", "--contract", _write(tmp_path, "contract.json", contract()),
            "--before", _write(tmp_path, "old.json", suite(old=True)),
            "--run", _write(tmp_path, "frozen.json", suite(red=True))]
    assert main(args) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "PASS"
    assert response["authorizes_merge"] is False
    assert response["contract_digest"] == contract().content_digest


def test_candidate_cli_requires_current_frozen_evidence(tmp_path, capsys) -> None:
    path = _write(tmp_path, "contract.json", contract())
    args = ["verify-tests", "candidate", "--contract", path,
            "--candidate-sha", CANDIDATE,
            "--run", _write(tmp_path, "candidate.json", suite(source=CANDIDATE))]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"
    args[-1] = _write(tmp_path, "stale.json", suite())
    assert main(args) == 1
    assert "SOURCE_MISMATCH" in json.loads(capsys.readouterr().out)["reasons"]


def test_missing_evidence_and_infrastructure_are_not_success_exit_codes(tmp_path, capsys) -> None:
    path = _write(tmp_path, "contract.json", contract())
    assert main(["verify-tests", "baseline", "--contract", path]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_AVAILABLE"
    infra = contract(story_kind="INFRASTRUCTURE", expected_red_cases=())
    path = _write(tmp_path, "infra.json", infra)
    assert main(["verify-tests", "baseline", "--contract", path]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_APPLICABLE"


@pytest.mark.parametrize("payload", [
    pytest.param("{", id="malformed"),
    pytest.param('{"work_item_id":"one","work_item_id":"two"}', id="duplicate"),
    pytest.param('{"value":NaN}', id="nonstandard"),
    pytest.param("[]", id="wrong-shape"),
    pytest.param("x" * (4 * 1024 * 1024 + 1), id="oversized"),
])
def test_bad_contract_is_a_sanitized_failure(tmp_path, capsys, payload) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(payload, encoding="utf-8")
    assert main(["verify-tests", "baseline", "--contract", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid or unavailable test evidence" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("arguments", [
    ["candidate"],
    ["candidate", "--candidate-sha", "bad"],
    ["baseline", "--candidate-sha", CANDIDATE],
])
def test_contradictory_or_incomplete_mode_is_rejected(tmp_path, capsys, arguments) -> None:
    path = _write(tmp_path, "contract.json", contract())
    assert main(["verify-tests", *arguments, "--contract", path]) == 2
    assert capsys.readouterr().out == ""