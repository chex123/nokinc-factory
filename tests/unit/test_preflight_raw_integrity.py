"""Versioned raw evidence is additive, immutable, and independently revalidated."""

import hashlib
import json
from collections.abc import Callable

import pytest
from pydantic import ValidationError

import nokinc_factory.domain.preflight as models


def _raw() -> models.RawWorktree:
    raw_type = getattr(models, "RawWorktree", None)
    assert raw_type is not None, "logical Git patches require a separate versioned raw binding"
    return models.RawWorktree.create(
        files=(models.candidate_file("item", b"raw\r\n"),), missing_paths=("deleted",),
        executable_paths=(), git_inputs_digest="sha256:" + "a" * 64,
    )


def _candidate(raw: models.RawWorktree | None) -> models.PreflightCandidate:
    context = models.TaskContext.create(
        provider="github", repository="acme/factory", work_item_id="6", title="Raw",
        body="Bind the actual bytes.", labels=(),
        source_url="https://github.com/acme/factory/issues/6",
    )
    return models.PreflightCandidate.create(
        base_sha="base", head_sha="head", task_context=context,
        committed=models.candidate_change(models.CandidateChangeKind.COMMITTED, (), b""),
        staged=models.candidate_change(models.CandidateChangeKind.STAGED, (), b""),
        unstaged=models.candidate_change(models.CandidateChangeKind.UNSTAGED, (), b""),
        untracked_files=(), raw_worktree=raw,
    )


def test_raw_v2_roundtrip_and_parent_binding() -> None:
    raw = _raw()
    assert raw.version == 2 and raw.profile == "git-builtins-no-helpers"
    assert type(raw).model_validate_json(raw.model_dump_json()) == raw
    with pytest.raises(ValidationError, match="frozen"):
        raw.files[0].path = "other"
    candidate = _candidate(raw)
    assert candidate.digest != _candidate(None).digest
    stale = candidate.model_dump()
    stale["raw_worktree"] = None
    with pytest.raises(ValidationError, match="digest does not match"):
        models.PreflightCandidate.model_validate(stale)


@pytest.mark.parametrize("boundary", [models.candidate_digest, models.PreflightCandidate.create])
def test_untracked_content_cannot_disagree_with_its_raw_copy(
    boundary: Callable[..., object],
) -> None:
    candidate = _candidate(_raw())
    inputs = {name: getattr(candidate, name)
              for name in type(candidate).model_fields if name != "digest"}
    inputs["untracked_files"] = (models.candidate_file("item", b"contradictory bytes"),)
    with pytest.raises(ValueError, match="(?i)raw|untracked"):
        boundary(**inputs)


@pytest.mark.parametrize(
    "update",
    [
        {"version": 3}, {"profile": "trusted"}, {"files": ()},
        {"missing_paths": ("../outside",)}, {"missing_paths": ("item",)},
        {"executable_paths": ("absent",)}, {"git_inputs_digest": "sha256:" + "b" * 64},
    ],
)
def test_raw_evidence_tampering_cannot_pass_the_parent_boundary(update: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _candidate(_raw().model_copy(update=update))


@pytest.mark.parametrize("kind", ["duplicate", "unsorted", "overlap", "ancestor", "executable"])
def test_rehash_cannot_legitimize_an_ambiguous_raw_inventory(kind: str) -> None:
    raw = _raw()
    payload = raw.model_dump(exclude={"content_digest"})
    file = models.candidate_file("item", b"raw").model_dump()
    if kind == "duplicate":
        payload["files"] = (file, file)
    elif kind == "unsorted":
        payload["missing_paths"] = ("z", "a")
    elif kind == "overlap":
        payload["missing_paths"] = ("item",)
    elif kind == "ancestor":
        payload["files"] = (file, models.candidate_file("item/leaf", b"raw").model_dump())
    else:
        payload["executable_paths"] = ("deleted",)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    payload["content_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    with pytest.raises(ValidationError):
        type(raw).model_validate(payload)