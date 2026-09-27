"""Build-derived release identity artifacts."""

from __future__ import annotations

from datetime import datetime

from nokinc_factory.adapters.repository_intelligence import RepositorySnapshot
from nokinc_factory.domain.identity import CodeModelSnapshot, CodeModelToolchain
from nokinc_factory.domain.review_base import content_digest


def build_code_model_snapshot(
    *, repo_snapshot: RepositorySnapshot, git_sha: str, build_id: str,
    image_digest: str, build_config_digest: str | None,
    toolchain: CodeModelToolchain, generated_at: datetime,
) -> CodeModelSnapshot:
    """Generate the immutable code model identity from the build inputs."""
    values = {
        "git_sha": git_sha,
        "build_id": build_id,
        "image_digest": image_digest,
        "build_config_digest": build_config_digest,
        "toolchain": toolchain.model_dump(mode="json"),
        "repository_snapshot_digest": repo_snapshot.content_digest,
        "generated_at": generated_at.isoformat(),
        "generated_by": "build-pipeline",
    }
    snapshot_id = content_digest(values)
    return CodeModelSnapshot(
        snapshot_id=snapshot_id, git_sha=git_sha, build_id=build_id,
        image_digest=image_digest, build_config_digest=build_config_digest,
        toolchain=toolchain, generated_at=generated_at,
    )
