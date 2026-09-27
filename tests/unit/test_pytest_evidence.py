"""Inventory and contract evidence remains content-bound before CI consumes it."""

import json
from pathlib import Path

import pytest

from nokinc_factory.adapters.pytest_evidence import build_contract, collect_inventory


def test_collect_inventory_hashes_all_declared_files_and_cases(tmp_path: Path) -> None:
    (tmp_path / "test_sample.py").write_text(
        "def test_first():\n    assert 1 == 1\n\n"
        "def test_second():\n    assert 2 == 2\n",
        encoding="utf-8",
    )
    inventory = collect_inventory(
        root=tmp_path, paths=("test_sample.py",), work_item_id="work-1",
        source_sha="a" * 40, runner_digest="sha256:" + "1" * 64,
        environment_digest="sha256:" + "2" * 64,
    )
    assert inventory.exit_code == 0
    assert inventory.collection_errors == 0
    assert inventory.case_ids == ("test_sample.py::test_first", "test_sample.py::test_second")
    assert inventory.suite_digest.startswith("sha256:")


def test_inventory_changes_when_declared_fixture_bytes_change(tmp_path: Path) -> None:
    path = tmp_path / "test_sample.py"
    path.write_text("def test_one():\n    assert True\n", encoding="utf-8")
    values = dict(
        root=tmp_path, paths=("test_sample.py",), work_item_id="work-1",
        source_sha="a" * 40, runner_digest="sha256:" + "1" * 64,
        environment_digest="sha256:" + "2" * 64,
    )
    first = collect_inventory(**values)
    path.write_text("def test_one():\n    assert False\n", encoding="utf-8")
    second = collect_inventory(**values)
    assert first.suite_digest != second.suite_digest


def test_contract_declares_only_new_case_ids_as_expected_red(tmp_path: Path) -> None:
    root = tmp_path
    for name, body in {
        "test_old.py": "def test_old():\n    assert True\n",
        "test_new.py": "def test_new():\n    assert True\n",
    }.items():
        (root / name).write_text(body, encoding="utf-8")
    values = dict(
        root=root, work_item_id="work-1", source_sha="a" * 40,
        runner_digest="sha256:" + "1" * 64, environment_digest="sha256:" + "2" * 64,
    )
    baseline = collect_inventory(paths=("test_old.py",), **values)
    frozen = collect_inventory(paths=("test_old.py", "test_new.py"), **values)
    output = root / "contract.json"
    contract = build_contract(baseline, frozen, story_kind="NEW_BEHAVIOR", output=output)
    assert contract.expected_red_cases == ("test_new.py::test_new",)
    saved_contract = json.loads(output.read_text(encoding="utf-8"))
    assert saved_contract["content_digest"] == contract.content_digest


def test_collection_failure_is_recorded_and_not_a_green_inventory(tmp_path: Path) -> None:
    (tmp_path / "test_broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    inventory = collect_inventory(
        root=tmp_path, paths=("test_broken.py",), work_item_id="work-1",
        source_sha="a" * 40, runner_digest="sha256:" + "1" * 64,
        environment_digest="sha256:" + "2" * 64,
    )
    assert inventory.exit_code != 0 or inventory.collection_errors > 0
    assert not inventory.case_ids


@pytest.mark.parametrize("path_text", ["../outside", "", ".git"])
def test_invalid_inventory_path_fails_closed(tmp_path: Path, path_text: str) -> None:
    with pytest.raises(ValueError):
        collect_inventory(
            root=tmp_path, paths=(path_text,), work_item_id="work-1",
            source_sha="a" * 40, runner_digest="sha256:" + "1" * 64,
            environment_digest="sha256:" + "2" * 64,
        )