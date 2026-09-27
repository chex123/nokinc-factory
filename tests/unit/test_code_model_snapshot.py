from datetime import UTC, datetime
from pathlib import Path

from nokinc_factory.adapters.release_artifacts import build_code_model_snapshot
from nokinc_factory.adapters.repository_intelligence import RepositoryIntelligence
from nokinc_factory.domain.identity import CodeModelToolchain


def test_code_model_snapshot_is_content_bound_to_repo_and_build(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    repo_snapshot = RepositoryIntelligence(tmp_path).snapshot()
    toolchain = CodeModelToolchain(
        repository_intelligence_version="ri-1",
        tree_sitter_version="ts-1",
    )

    first = build_code_model_snapshot(
        repo_snapshot=repo_snapshot, git_sha="a" * 40, build_id="build-1",
        image_digest="sha256:" + "1" * 64, build_config_digest="sha256:" + "2" * 64,
        toolchain=toolchain, generated_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    second = build_code_model_snapshot(
        repo_snapshot=repo_snapshot, git_sha="a" * 40, build_id="build-1",
        image_digest="sha256:" + "1" * 64, build_config_digest="sha256:" + "2" * 64,
        toolchain=toolchain, generated_at=datetime(2026, 9, 20, tzinfo=UTC),
    )

    assert first == second
    assert first.snapshot_id.startswith("sha256:")


def test_code_model_snapshot_changes_when_repo_or_build_identity_changes(tmp_path: Path) -> None:
    file_path = tmp_path / "service.py"
    file_path.write_text("def run():\n    return 1\n", encoding="utf-8")
    toolchain = CodeModelToolchain(
        repository_intelligence_version="ri-1", tree_sitter_version="ts-1",
    )
    first = build_code_model_snapshot(
        repo_snapshot=RepositoryIntelligence(tmp_path).snapshot(), git_sha="a" * 40,
        build_id="build-1", image_digest="sha256:" + "1" * 64,
        build_config_digest=None, toolchain=toolchain,
        generated_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    file_path.write_text("def run():\n    return 2\n", encoding="utf-8")
    changed = build_code_model_snapshot(
        repo_snapshot=RepositoryIntelligence(tmp_path).snapshot(), git_sha="a" * 40,
        build_id="build-1", image_digest="sha256:" + "1" * 64,
        build_config_digest=None, toolchain=toolchain,
        generated_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    assert changed.snapshot_id != first.snapshot_id
