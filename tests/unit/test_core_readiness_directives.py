"""A01 / R02 review repair: compiler and interpreter directives are not prose."""

import pytest

from nokinc_factory.policy.impact import (
    Evidence,
    FileChange,
    ImpactClass,
    SecuritySensitiveRegistry,
    classify,
)


@pytest.mark.parametrize("prefixed", [False, True], ids=["raw", "diff"])
@pytest.mark.parametrize("sensitive", [False, True], ids=["ordinary", "sensitive"])
@pytest.mark.parametrize(
    ("extension", "before", "after"),
    [
        pytest.param("go", "//go:build linux", "//go:build ignore", id="go-build"),
        pytest.param("go", "// +build linux", "// +build ignore", id="go-legacy-build"),
        pytest.param("go", "//+build linux", "//+build ignore", id="go-legacy-no-space"),
        pytest.param("go", "//\t+build linux", "//\t+build ignore", id="go-legacy-tab"),
        pytest.param("go", "// ordinary explanation", "//go:noescape", id="go-pragma-added"),
        pytest.param(
            "go", "//go:linkname local original.symbol", "// ordinary explanation",
            id="go-pragma-removed",
        ),
        pytest.param("go", "//go:embed old.txt", "//go:embed new.txt", id="go-embed"),
        pytest.param("go", "//go:generate old", "//go:generate new", id="go-generate"),
        pytest.param("go", "//go:future old", "//go:future new", id="go-reserved-prefix"),
        pytest.param("py", "# coding: utf-8", "# coding: latin-1", id="python-coding"),
        pytest.param(
            "py", "# -*- coding: utf-8 -*-", "# -*- coding: latin-1 -*-",
            id="python-coding-cookie",
        ),
        pytest.param(
            "py", "# vim: set fileencoding=utf-8 :", "# vim: set fileencoding=latin-1 :",
            id="python-coding-equals",
        ),
        pytest.param(
            "py", "#!/usr/bin/python3", "#!/usr/bin/python3 -O", id="python-shebang-flags",
        ),
        pytest.param(
            "py", "# ordinary explanation", "# type: ignore", id="python-type-directive",
        ),
    ],
)
def test_comment_directives_do_not_skip_evidence_invalidation(
    extension: str, before: str, after: str, sensitive: bool, prefixed: bool,
) -> None:
    """A comment-shaped edit can change build selection, execution or type checking."""
    directory = "auth" if sensitive else "util"
    change = FileChange(
        path=f"src/{directory}/check.{extension}",
        added_lines=[("+" if prefixed else "") + "\t" + after],
        removed_lines=[("-" if prefixed else "") + before],
    )

    result = classify([change], SecuritySensitiveRegistry())

    assert {Evidence.UNIT_TESTS, Evidence.ACCEPTANCE_TESTS} <= result.invalidated
    if sensitive:
        assert ImpactClass.SECURITY_SENSITIVE in result.classes
        assert {Evidence.SECURITY_REVIEW, Evidence.GATE_2_APPROVAL} <= result.invalidated
    else:
        assert result.classes == {ImpactClass.ORDINARY_IMPLEMENTATION}
    assert ImpactClass.COSMETIC not in result.classes
    assert not change.is_cosmetic


@pytest.mark.parametrize("prefixed", [False, True], ids=["raw", "diff"])
@pytest.mark.parametrize("directive_added", [False, True], ids=["removed", "added"])
def test_go_directive_inside_multiline_comment_entry_is_not_cosmetic(
    directive_added: bool, prefixed: bool,
) -> None:
    marker = ("+" if directive_added else "-") if prefixed else ""
    entry = f"{marker}// explanation\n{marker}\t//go:build ignore"
    change = FileChange(
        path="src/auth/check.go",
        added_lines=[entry] if directive_added else [],
        removed_lines=[] if directive_added else [entry],
    )

    result = classify([change], SecuritySensitiveRegistry())

    assert not change.is_cosmetic
    assert result.classes == {ImpactClass.SECURITY_SENSITIVE}
    assert {
        Evidence.UNIT_TESTS, Evidence.ACCEPTANCE_TESTS,
        Evidence.SECURITY_REVIEW, Evidence.GATE_2_APPROVAL,
    } <= result.invalidated


@pytest.mark.parametrize("prefixed", [False, True], ids=["raw", "diff"])
@pytest.mark.parametrize(
    ("extension", "comment"),
    [
        ("go", "// clarify the calculation"),
        ("go", "// go:build is described here"),
        ("go", "// +builder is an ordinary name"),
        ("go", "// coding: latin-1"),
        ("py", "# tidy up"),
        ("py", "# type hints are useful"),
        ("js", "//go:build linux"),
        ("rb", "# clarify the calculation!"),
        *[
            (extension, "// clarify the calculation")
            for extension in ("js", "jsx", "ts", "tsx")
        ],
    ],
)
def test_ordinary_comments_preserve_cosmetic_contract_on_sensitive_paths(
    extension: str, comment: str, prefixed: bool,
) -> None:
    change = FileChange(
        path=f"src/auth/check.{extension}",
        added_lines=[("+" if prefixed else "") + "  " + comment],
        removed_lines=[("-" if prefixed else "") + comment],
    )

    result = classify([change], SecuritySensitiveRegistry())

    assert change.is_cosmetic
    assert result.classes == {ImpactClass.COSMETIC}
    assert result.invalidated == set()


@pytest.mark.parametrize("prefixed", [False, True], ids=["raw", "diff"])
@pytest.mark.parametrize(
    "line", ['"use strict";', '"use client";', "#!/usr/bin/node --jitless", "/*#__PURE__*/"],
)
def test_javascript_execution_directives_already_fail_closed(line: str, prefixed: bool) -> None:
    change = FileChange(
        path="src/auth/check.js", added_lines=[("+" if prefixed else "") + line],
    )

    result = classify([change], SecuritySensitiveRegistry())

    assert not change.is_cosmetic
    assert result.classes == {ImpactClass.SECURITY_SENSITIVE}
    assert {Evidence.UNIT_TESTS, Evidence.ACCEPTANCE_TESTS, Evidence.SECURITY_REVIEW} <= (
        result.invalidated
    )


@pytest.mark.parametrize("prefixed", [False, True], ids=["raw", "diff"])
@pytest.mark.parametrize("sensitive", [False, True], ids=["ordinary", "sensitive"])
@pytest.mark.parametrize("directive_added", [False, True], ids=["removed", "added"])
@pytest.mark.parametrize("spacing", ["", " \t"], ids=["compact", "whitespace"])
@pytest.mark.parametrize(
    ("extension", "prefix", "directive"),
    [
        ("py", "#", "mypy: ignore-errors"),
        ("py", "#", "mypy:\t disallow-untyped-defs=False"),
        ("py", "#", "pyright: strict"),
        ("py", "#", "pyright: ignore[reportGeneralTypeIssues]"),
        ("py", "#", "ruff: noqa"),
        ("py", "#", "flake8: noqa"),
        ("py", "#", "noqa: F401"),
        ("py", "#", "NOQA"),
        ("py", "#", "pylint: disable=all"),
        ("rb", "#", "frozen_string_literal: true"),
        ("rb", "#", "frozen_string_literal:false"),
        ("rb", "#", "frozen_string_literal \t:\ttrue"),
        ("rb", "#", "-*- frozen_string_literal: false -*-"),
        ("rb", "#", "-*- coding: utf-8; frozen_string_literal: true -*-"),
        ("rb", "#", "rubocop:disable all"),
        ("rb", "#", "rubocop:enable all"),
        ("rb", "#", "rubocop:todo all"),
        ("rb", "#", "frozen_string_literals: true"),
        ("rb", "#", "frozen_string_literal_extra: true"),
        ("rb", "#", "frozen_string_literal is documented here"),
        ("rb", "#", "rubocopish:disable all"),
        ("rb", "#", "rubocop:disablement"),
        ("rb", "#", "\u00a0frozen_string_literal: false"),
        ("rb", "#", "frozen-string-literal: true"),
        ("rb", "#", '"frozen_string_literal": true'),
        ("rb", "#", "prefix frozen_string_literal: true"),
        ("rb", "#", "FROZEN_STRING_LITERAL: true"),
        ("rb", "#", "coding: utf-8"),
        ("rb", "#", "encoding=ASCII-8BIT"),
        ("rb", "#", "warn_indent: true"),
        ("rb", "#", "warn-indent: false"),
        ("rb", "#", "shareable_constant_value: literal"),
        ("rb", "#", "shareable-constant-value: literal"),
        ("rb", "#", "!/usr/bin/env ruby"),
        ("rb", "#", "\u00a0rubocop:disable all"),
        *[
            (extension, "//", f"@ts-{directive}")
            for extension in ("js", "jsx", "ts", "tsx")
            for directive in ("nocheck", "check", "ignore", "expect-error")
        ],
        ("ts", "///", "@ts-nocheck"),
        ("js", "//", "@ts-expect-error: explanation"),
        ("js", "//", "eslint-disable"),
        ("ts", "//", "eslint-disable-next-line @typescript-eslint/no-explicit-any"),
        ("tsx", "//", "eslint-disable-line no-undef"),
        ("jsx", "//", "eslint-enable"),
        # TypeScript 6.0.3 confirms these are active, not inactive lookalikes.
        pytest.param("tsx", "//", "@ts-ignored", id="confirmed-ts-ignored"),
        pytest.param("jsx", "//", "@ts-expect-errors", id="confirmed-ts-expect-errors"),
        pytest.param("ts", "//", "\u00a0@ts-nocheck", id="confirmed-ts-nbsp-nocheck"),
        ("js", "//", "@ts-nochecking"),
        ("ts", "//", "@ts-checklist"),
        ("js", "//", "eslint-disabled"),
        ("ts", "//", "eslint-disable-next-lines"),
        ("jsx", "//", "discuss @ts-check"),
        ("js", "///", "\ufeff@ts-ignore"),
        ("tsx", "//", "\u2003@ts-expect-error"),
        ("ts", "//", "@future-checker"),
        ("jsx", "//", "a @ b"),
        ("js", "//", "\u00a0eslint-disable-line"),
        ("tsx", "//", "explanation eslint-enable"),
    ],
)
def test_directive_shaped_comments_invalidate_evidence(
    extension: str, prefix: str, directive: str, spacing: str,
    directive_added: bool, sensitive: bool, prefixed: bool,
) -> None:
    """Active pragmas and ambiguous markers on either side must bypass the cosmetic shortcut."""
    marker = ("+" if directive_added else "-") if prefixed else ""
    entry = f"{marker}{prefix} explanation\n{marker}\t {prefix}{spacing}{directive}\n{marker}  "
    directory = "auth" if sensitive else "src/util"
    change = FileChange(
        path=f"{directory}/check.{extension}",
        added_lines=[entry] if directive_added else [],
        removed_lines=[] if directive_added else [entry],
    )

    result = classify([change], SecuritySensitiveRegistry())

    assert not change.is_cosmetic
    expected_class = (
        ImpactClass.SECURITY_SENSITIVE if sensitive else ImpactClass.ORDINARY_IMPLEMENTATION
    )
    assert result.classes == {expected_class}
    expected_evidence = {Evidence.UNIT_TESTS, Evidence.ACCEPTANCE_TESTS}
    if sensitive:
        expected_evidence |= {Evidence.SECURITY_REVIEW, Evidence.GATE_2_APPROVAL}
    assert result.invalidated == expected_evidence


@pytest.mark.parametrize("prefixed", [False, True], ids=["raw", "diff"])
@pytest.mark.parametrize(
    ("extension", "comment"),
    [
        ("py", "# mypy settings are documented here"),
        ("py", "# mypyish: ignore-errors"),
        ("py", "# mypy_pragma: ignore-errors"),
        ("py", "# pyrightish: strict"),
        ("py", "# ruffle: noqa"),
        ("py", "# flake80: noqa"),
        ("py", "# pylinter: disable=all"),
        ("py", "# noquality: checked"),
        ("py", "# noqa_extra"),
        ("py", "# frozen_string_literal: true"),
        ("rb", "# mypy: ignore-errors"),
        ("rb", "# noqa: F401"),
        ("go", "// @ts-nocheck"),
        ("cs", "// @ts-ignore"),
        ("java", "// eslint-disable"),
        ("ts", "// mypy: ignore-errors"),
    ],
)
def test_unrelated_comments_preserve_language_boundaries(
    extension: str, comment: str, prefixed: bool,
) -> None:
    change = FileChange(
        path=f"src/auth/check.{extension}",
        added_lines=[("+" if prefixed else "") + "\t " + comment],
        removed_lines=[("-" if prefixed else "") + comment],
    )

    result = classify([change], SecuritySensitiveRegistry())

    assert change.is_cosmetic
    assert result.classes == {ImpactClass.COSMETIC}
    assert result.invalidated == set()


@pytest.mark.parametrize("prefixed", [False, True], ids=["raw", "diff"])
@pytest.mark.parametrize("before,after", [("true", "false"), ("false", "true")])
def test_ruby_freezing_toggle_invalidates_runtime_evidence(
    before: str, after: str, prefixed: bool,
) -> None:
    change = FileChange(
        path="src/util/check.rb",
        added_lines=[("+" if prefixed else "") + f"# frozen_string_literal: {after}"],
        removed_lines=[("-" if prefixed else "") + f"# frozen_string_literal: {before}"],
    )

    result = classify([change], SecuritySensitiveRegistry())

    assert not change.is_cosmetic
    assert result.classes == {ImpactClass.ORDINARY_IMPLEMENTATION}
    assert result.invalidated == {Evidence.UNIT_TESTS, Evidence.ACCEPTANCE_TESTS}