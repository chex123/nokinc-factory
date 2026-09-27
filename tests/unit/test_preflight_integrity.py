"""A01 regressions: content binding is not trusted provenance (Spec Parts 1–2)."""

import hashlib
import json
from collections.abc import Callable
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from nokinc_factory.domain.preflight import (
    CandidateChange,
    CandidateChangeKind,
    CandidateFile,
    PreflightCandidate,
    TaskContext,
    candidate_change,
    candidate_digest,
    candidate_file,
)


def _context() -> TaskContext:
    return TaskContext.create(
        provider="github", repository="acme/factory", work_item_id="6", title="Integrity",
        body="Capture raw bytes.", labels=("story", "a01"),
        source_url="https://github.com/acme/factory/issues/6",
    )


def _candidate() -> PreflightCandidate:
    return PreflightCandidate.create(
        base_sha="base", head_sha="head", task_context=_context(),
        committed=candidate_change(CandidateChangeKind.COMMITTED, (), b""),
        staged=candidate_change(CandidateChangeKind.STAGED, (), b""),
        unstaged=candidate_change(CandidateChangeKind.UNSTAGED, ("src/file.txt",), b"raw patch\n"),
        untracked_files=(candidate_file("new.txt", b"new\n"),),
    )


def _inputs(candidate: PreflightCandidate) -> dict[str, object]:
    return {
        name: getattr(candidate, name)
        for name in type(candidate).model_fields if name != "digest"
    }


def _digest(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    ("select", "field", "value"),
    [
        (lambda item: item, "head_sha", "other"),
        (lambda item: item.task_context, "body", "different request"),
        (lambda item: item.committed, "patch_base64", "bmV3"),
        (lambda item: item.staged, "paths", ("other.txt",)),
        (lambda item: item.unstaged, "kind", CandidateChangeKind.COMMITTED),
        (lambda item: item.untracked_files[0], "content_base64", "bmV3"),
    ],
)
def test_preflight_objects_are_deeply_frozen(
    select: Callable[[PreflightCandidate], BaseModel], field: str, value: object,
) -> None:
    candidate = _candidate()
    with pytest.raises(ValidationError, match="frozen"):
        setattr(select(candidate), field, value)
    assert isinstance(candidate.task_context.labels, tuple)
    assert isinstance(candidate.unstaged.paths, tuple)
    assert isinstance(candidate.untracked_files, tuple)


@pytest.mark.parametrize("model", [CandidateChange, CandidateFile])
@pytest.mark.parametrize("encoded", ["not base64!", "YQ==\n", "YQ===", "YR==", "Yg=="])
def test_children_verify_strict_canonical_base64_and_decoded_digest(
    model: type[CandidateChange] | type[CandidateFile], encoded: str,
) -> None:
    child = (
        candidate_file("file.txt", b"a") if model is CandidateFile
        else candidate_change(CandidateChangeKind.STAGED, ("file.txt",), b"a")
    )
    payload = child.model_dump()
    payload["content_base64" if model is CandidateFile else "patch_base64"] = encoded
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_binary_marker_must_match_decoded_bytes() -> None:
    payload = candidate_file("binary.dat", b"\x00raw\xff").model_dump()
    payload["is_binary"] = False
    with pytest.raises(ValidationError):
        CandidateFile.model_validate(payload)


@pytest.mark.parametrize(
    "path", ["", "/abs", "../outside", "a/../b", "a/./b", "a//b", "a/", "C:/x",
             "a\\b", "x:stream", ".git/config", "a/.GIT/config", "a\x00b", "a\nb"],
)
@pytest.mark.parametrize("kind", ["file", "change"])
def test_child_paths_must_be_canonical_safe_repository_relative_paths(path: str, kind: str) -> None:
    with pytest.raises(ValidationError):
        if kind == "file":
            candidate_file(path, b"raw")
        else:
            candidate_change(CandidateChangeKind.STAGED, (path,), b"raw")


@pytest.mark.parametrize("paths", [("a", "a"), ("z", "a")])
def test_deserialized_change_paths_are_unique_and_sorted(paths: tuple[str, ...]) -> None:
    payload = candidate_change(CandidateChangeKind.STAGED, (), b"").model_dump()
    payload["paths"] = paths
    with pytest.raises(ValidationError):
        CandidateChange.model_validate(payload)


@pytest.mark.parametrize("field", ["committed", "staged", "unstaged"])
def test_change_category_must_match_its_parent_slot(field: str) -> None:
    payload = _candidate().model_dump()
    payload[field]["kind"] = (
        CandidateChangeKind.STAGED if field == "committed" else CandidateChangeKind.COMMITTED
    )
    # A freshly recomputed outer hash must not legitimize a mislabeled child.
    with pytest.raises(ValueError):
        values = _inputs(_candidate())
        values[field] = CandidateChange.model_validate(payload[field])
        cast(Callable[..., str], candidate_digest)(**values)


@pytest.mark.parametrize("boundary", [candidate_digest, PreflightCandidate.create])
@pytest.mark.parametrize(
    ("slot", "update"),
    [
        ("task_context", {"body": "tampered"}),
        ("task_context", {"labels": ["a01", "story"]}),
        ("committed", {"kind": "committed"}),
        ("staged", {"patch_base64": "YQ=="}),
        ("unstaged", {"paths": ["src/file.txt"]}),
        ("untracked_files", {"content_base64": "YQ=="}),
        ("untracked_files", {"path": "../outside"}),
    ],
)
def test_consumers_revalidate_model_copy_updates(
    boundary: Callable[..., object], slot: str, update: dict[str, object],
) -> None:
    candidate = _candidate()
    values = _inputs(candidate)
    child = candidate.untracked_files[0] if slot == "untracked_files" else getattr(candidate, slot)
    forged = child.model_copy(update=update)
    values[slot] = (forged,) if slot == "untracked_files" else forged
    with pytest.raises(ValueError):
        boundary(**values)


@pytest.mark.parametrize("slot", ["task_context", "staged", "untracked_files"])
def test_candidate_instance_revalidation_checks_nested_construct_bypasses(slot: str) -> None:
    candidate = _candidate()
    if slot == "task_context":
        child: object = candidate.task_context.model_copy(update={"body": "tampered"})
    elif slot == "staged":
        child = candidate.staged.model_copy(update={"content_digest": "sha256:" + "0" * 64})
    else:
        child = (candidate.untracked_files[0].model_copy(update={"is_binary": True}),)
    forged = candidate.model_copy(update={slot: child})
    with pytest.raises(ValidationError):
        PreflightCandidate.model_validate(forged)


@pytest.mark.parametrize(
    "update",
    [
        {"repository": "acme/other"}, {"work_item_id": "7"}, {"provider": " github"},
        {"source_url": "https://github.com/acme/other/issues/6"},
        {"source_url": "https://user@github.com/acme/factory/issues/6"},
        {"source_url": "https://github.com/acme/factory/issues/6?"},
        {"source_url": "https://github.com/acme/factory/issues/6#"},
        {"source_url": "https://github.com:444/acme/factory/issues/6"},
        {"source_url": "https://github.com/acme/factory/issues/06"},
        {"source_url": "https://github.com/acme/factory/pull/6"},
        {"labels": ("story", "a01")},
    ],
)
def test_task_identity_and_canonical_labels_cannot_be_rehashed_into_validity(
    update: dict[str, object],
) -> None:
    payload = _context().model_dump(exclude={"content_digest"})
    payload.update(update)
    payload["content_digest"] = _digest(payload)
    forged = _context().model_copy(update=payload)
    with pytest.raises(ValueError):
        cast(Callable[..., str], candidate_digest)(
            **(_inputs(_candidate()) | {"task_context": forged}),
        )


def test_duplicate_untracked_paths_cannot_bind_two_contents_to_one_path() -> None:
    values = _inputs(_candidate())
    values["untracked_files"] = (candidate_file("same", b"a"), candidate_file("same", b"b"))
    with pytest.raises(ValueError):
        cast(Callable[..., PreflightCandidate], PreflightCandidate.create)(**values)


def test_preflight_serialization_and_known_content_digests_do_not_change() -> None:
    candidate = _candidate()
    assert candidate.task_context.content_digest == (
        "sha256:d13f5fffc921ffa1d0cb3f15c29d04fb283107fc60a6dbcc63148707e3ed9d1e"
    )
    assert candidate.digest == (
        "sha256:46254bc0f6162c56406c1a4dda4163b8c493094be106d8994c1b5af9d0305ab8"
    )
    assert PreflightCandidate.model_validate_json(candidate.model_dump_json()) == candidate
    assert set(candidate.model_dump()) == {
        "base_sha", "head_sha", "task_context", "committed", "staged", "unstaged",
        "untracked_files", "digest", "raw_worktree",
    }
    # The optional v2 extension must not change either v1 golden hash above.
    assert candidate.raw_worktree is None


def test_validated_rebuild_changes_context_binding_without_mutating_original() -> None:
    original = _context()
    values = original.model_dump(exclude={"content_digest"}) | {"body": "New request."}
    replacement = TaskContext.create(**values)
    assert original.body == "Capture raw bytes."
    assert replacement.content_digest != original.content_digest


def test_github_enterprise_source_identity_remains_supported() -> None:
    values = _context().model_dump(exclude={"content_digest"})
    values["source_url"] = "https://git.example.test/acme/factory/issues/6"
    context = TaskContext.create(**values)
    assert context.source_url == values["source_url"]