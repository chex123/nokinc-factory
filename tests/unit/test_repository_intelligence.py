from pathlib import Path

import pytest

from nokinc_factory.adapters.repository_intelligence import RepositoryIntelligence


def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "payments.py").write_text(
        "class RefundService:\n    def request_refund(self):\n        return True\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_refunds.py").write_text(
        "def test_duplicate_refund():\n    assert True\n", encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("refund duplicate behavior\n", encoding="utf-8")
    return tmp_path


def test_catalogue_symbols_tests_and_content_digests_are_deterministic(tmp_path: Path) -> None:
    root = repo(tmp_path)
    first = RepositoryIntelligence(root).snapshot()
    second = RepositoryIntelligence(root).snapshot()

    assert first.content_digest == second.content_digest
    assert "src/payments.py" in first.files
    assert "tests/test_refunds.py::test_duplicate_refund" in first.test_ids
    assert any(symbol.name == "RefundService" for symbol in first.symbols)
    assert any(symbol.name.endswith("request_refund") for symbol in first.symbols)


def test_lexical_search_and_bounded_read_are_root_scoped(tmp_path: Path) -> None:
    root = repo(tmp_path)
    intelligence = RepositoryIntelligence(root)

    matches = intelligence.search("duplicate")
    assert {match.path for match in matches} == {"README.md", "tests/test_refunds.py"}
    assert intelligence.read("src/payments.py", max_bytes=200).startswith("class RefundService")

    with pytest.raises(ValueError):
        intelligence.read("../outside.txt")


def test_symlinks_and_oversized_files_fail_closed(tmp_path: Path) -> None:
    root = repo(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (root / "src" / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ValueError, match="symlink"):
        RepositoryIntelligence(root).snapshot()
    with pytest.raises(ValueError, match="byte"):
        RepositoryIntelligence(root, max_file_bytes=2).snapshot()
