"""Bootstrap contract preventing CODEOWNERS approval deadlocks."""

from pathlib import Path

ROOT = Path(__file__).parents[2]
FACTORY_CODEOWNERS = ROOT / ".github" / "CODEOWNERS"
BOOTSTRAP = ROOT / "scripts" / "bootstrap.sh"
VERIFY = ROOT / "scripts" / "verify.sh"


def _codeowners() -> dict[str, list[str]]:
    entries: dict[str, list[str]] = {}
    for line in FACTORY_CODEOWNERS.read_text(encoding="utf-8").splitlines():
        path, *owners = line.split()
        entries[path] = owners
    return entries


def test_factory_protected_paths_have_two_named_codeowners() -> None:
    entries = _codeowners()
    expected_owners = ["@chex123", "@triplexapps"]

    assert entries["/tests/acceptance/"] == expected_owners
    assert entries["/docs/factory-spec.md"] == expected_owners
    assert entries["/.github/"] == expected_owners


def test_bootstrap_generates_two_owners_for_every_protected_path() -> None:
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    protected_paths = {
        "/tests/acceptance/",
        "/tests/contract/",
        "/tests/regression/",
        "/docs/factory-spec.md",
        "/.github/",
    }

    for path in protected_paths:
        assert f"{path} @%s @%s" in bootstrap
    assert bootstrap.count("'$ME' '$GATE_REVIEWER'") == 7


def test_verify_fails_when_github_codeowners_lack_an_alternate_reviewer() -> None:
    verify = VERIFY.read_text(encoding="utf-8")

    assert "contents/.github/CODEOWNERS" in verify
    assert "github_owner_count" in verify
    assert '[[ "$github_owner_count" -ge 2 ]]' in verify