from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_factory_container_contract_exists() -> None:
    dockerfile = REPOSITORY_ROOT / "Dockerfile"

    assert dockerfile.is_file()
    content = dockerfile.read_text(encoding="utf-8")
    assert (
        "FROM cgr.dev/chainguard/python:latest-dev@sha256:"
        "af8fafef8f22e0f7004332dd8ff0a247e273ec159fd8919dfeb3c6fb6142d3b9"
        in content
    )
    assert (
        "FROM cgr.dev/chainguard/python:latest@sha256:"
        "1dc2f617fc4430893004717cace9029b854e1dfb78b3a71220be6f2eb20a4c7f"
        in content
    )
    assert "pip install --no-cache-dir '.[phase1]'" in content
    assert "FACTORY_API_HOST=0.0.0.0" in content
    assert "FACTORY_API_PORT=8080" in content
    assert 'ENTRYPOINT ["/opt/venv/bin/factory-api"]' in content
    assert "USER 65532:65532" in content
    assert "COPY scripts/qualify_models.py ./qualify_models.py" in content
    assert "COPY config/pilot.yaml ./pilot.yaml" in content


def test_factory_build_context_excludes_local_state_and_credentials() -> None:
    dockerignore = REPOSITORY_ROOT / ".dockerignore"

    assert dockerignore.is_file()
    patterns = set(dockerignore.read_text(encoding="utf-8").splitlines())
    assert {
        ".git",
        ".venv",
        "venv",
        "tests",
        "artifacts",
        "__pycache__",
        "*.py[cod]",
        ".env",
        ".env.*",
        "*.pem",
        "*.key",
        "*.tfstate*",
    } <= patterns
