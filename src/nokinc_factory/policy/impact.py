"""Deterministic impact classification. Spec Part 3.

Drives BOTH evidence invalidation and risk tiering. Never an LLM judging
"does this look security-relevant?" -- that reintroduces the problem one level up.

The motivating case has NO interface surface change::

    - if user.id == order.owner:
    -     refund()
    + refund()

NOTE ON THE REGEX PATTERNS -- these are a Phase 0 BOOTSTRAP, not the production
classifier. The spec calls for classification via Repository Intelligence (AST
queries for actual authorization predicates, reference-graph traversal for
dataflow). Regex over diff text is coarse, and it was already wrong once during
development: ``is_?owner`` means "is" plus an optional underscore plus "owner",
so it never matched ``order.owner``.

Until RI-backed classification lands, these patterns favour RECALL over
precision. A false positive costs one extra review. A false negative ships a
deleted authorization check.
"""

from __future__ import annotations

import fnmatch
import re
from enum import StrEnum

from pydantic import BaseModel, Field


class ImpactClass(StrEnum):
    INTERFACE_SURFACE = "interface_surface"
    SECURITY_SENSITIVE = "security_sensitive"
    AUTHZ_CHANGED = "authz_changed"
    PII_DATAFLOW_CHANGED = "pii_dataflow_changed"
    PERSISTENCE_SEMANTICS = "persistence_semantics"
    NEW_EGRESS_OR_STORE = "new_egress_or_store"
    DB_MIGRATION = "db_migration"
    IAC_CHANGED = "iac_changed"
    NEW_DEPENDENCY = "new_dependency"
    ORDINARY_IMPLEMENTATION = "ordinary_implementation"
    COSMETIC = "cosmetic"
    UNCLASSIFIABLE = "unclassifiable"


class Evidence(StrEnum):
    CONTRACT_TESTS = "contract_tests"
    ACCEPTANCE_TESTS = "acceptance_tests"
    UNIT_TESTS = "unit_tests"
    SECURITY_REVIEW = "security_review"
    PRIVACY_REVIEW = "privacy_review"
    GATE_2_APPROVAL = "gate_2_approval"
    ROLLBACK_STRATEGY = "rollback_strategy"
    POLICY_GATE = "policy_gate"
    LICENSE_SCAN = "license_scan"
    SBOM = "sbom"


#: Deterministic invalidation table. Reviewed like code.
INVALIDATES: dict[ImpactClass, frozenset[Evidence]] = {
    ImpactClass.INTERFACE_SURFACE: frozenset(
        {Evidence.CONTRACT_TESTS, Evidence.GATE_2_APPROVAL, Evidence.SECURITY_REVIEW}
    ),
    ImpactClass.SECURITY_SENSITIVE: frozenset(
        {
            Evidence.SECURITY_REVIEW,
            Evidence.GATE_2_APPROVAL,
            Evidence.ACCEPTANCE_TESTS,
            Evidence.UNIT_TESTS,
        }
    ),
    ImpactClass.AUTHZ_CHANGED: frozenset(
        {
            Evidence.SECURITY_REVIEW,
            Evidence.GATE_2_APPROVAL,
            Evidence.ACCEPTANCE_TESTS,
            Evidence.CONTRACT_TESTS,
        }
    ),
    ImpactClass.PII_DATAFLOW_CHANGED: frozenset(
        {Evidence.SECURITY_REVIEW, Evidence.PRIVACY_REVIEW, Evidence.GATE_2_APPROVAL}
    ),
    ImpactClass.PERSISTENCE_SEMANTICS: frozenset(
        {Evidence.GATE_2_APPROVAL, Evidence.ROLLBACK_STRATEGY, Evidence.SECURITY_REVIEW}
    ),
    ImpactClass.NEW_EGRESS_OR_STORE: frozenset(
        {Evidence.GATE_2_APPROVAL, Evidence.SECURITY_REVIEW}
    ),
    ImpactClass.DB_MIGRATION: frozenset(
        {Evidence.GATE_2_APPROVAL, Evidence.ROLLBACK_STRATEGY, Evidence.SECURITY_REVIEW}
    ),
    ImpactClass.IAC_CHANGED: frozenset({Evidence.GATE_2_APPROVAL, Evidence.POLICY_GATE}),
    ImpactClass.NEW_DEPENDENCY: frozenset(
        {Evidence.LICENSE_SCAN, Evidence.SBOM, Evidence.GATE_2_APPROVAL}
    ),
    ImpactClass.ORDINARY_IMPLEMENTATION: frozenset(
        {Evidence.ACCEPTANCE_TESTS, Evidence.UNIT_TESTS}
    ),
    ImpactClass.COSMETIC: frozenset(),
    # Fail closed: unclassifiable invalidates everything and goes to a human.
    ImpactClass.UNCLASSIFIABLE: frozenset(Evidence),
}

_COMMENT_STARTS: tuple[str, ...] = ("#", "//", "/*", "*", '"""', "'''")


class SecuritySensitiveRegistry(BaseModel):
    """Declared, version-controlled, reviewed like code.

    Loaded from ``.factory/security-sensitive.yaml`` in a real deployment.
    """

    paths: list[str] = Field(
        default_factory=lambda: [
            "**/auth/**",
            "**/authz/**",
            "**/crypto/**",
            "**/payments/**",
            "**/pii/**",
            "**/migrations/**",
        ]
    )
    patterns: dict[str, str] = Field(
        default_factory=lambda: {
            "authorization_predicate": (
                r"\b(owner|owns|can_\w+|may_\w+|is_allowed|allowed|has_permission|"
                r"permission|permitted|authoriz\w*|forbidden|denied|acl|rbac|role|"
                r"current_user|principal|subject_id)\b"
            ),
            "sql_construction": r"(execute|executemany|raw|text)\s*\(\s*[f\"']",
            "secret_access": r"\b(get_secret|vault|kms|credential|api_key|token)\b",
            "outbound_network": r"\b(requests\.|httpx\.|urlopen|aiohttp\.|fetch\()",
            "transaction_boundary": r"\b(begin|commit|rollback|transactional)\b",
            "pii_dataflow": r"\b(email|ssn|dob|phone|address|card_number|pan)\b",
        }
    )
    known_extensions: list[str] = Field(
        default_factory=lambda: [
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".java",
            ".go",
            ".cs",
            ".rb",
            ".sql",
            ".tf",
            ".yaml",
            ".yml",
            ".md",
            ".json",
            ".toml",
            ".txt",
        ]
    )

    def path_is_sensitive(self, path: str) -> bool:
        # ``fnmatch('auth/check.py', '**/auth/**')`` is False: ``**/`` is not
        # recursive-glob magic to fnmatch, it still consumes a path segment.
        # Check both the declared pattern and its root-level form so a monorepo
        # without a ``src/`` prefix cannot silently bypass classification.
        normalized = path.lstrip("./")
        for pattern in self.paths:
            candidates = (pattern, pattern[3:]) if pattern.startswith("**/") else (pattern,)
            if any(fnmatch.fnmatch(normalized, candidate) for candidate in candidates):
                return True
        return False

    def matched_patterns(self, diff_text: str) -> set[str]:
        return {
            name for name, rx in self.patterns.items() if re.search(rx, diff_text, re.IGNORECASE)
        }


class FileChange(BaseModel):
    path: str
    added_lines: list[str] = Field(default_factory=list)
    removed_lines: list[str] = Field(default_factory=list)

    @property
    def diff_text(self) -> str:
        return "\n".join(self.added_lines + self.removed_lines)

    @property
    def is_cosmetic(self) -> bool:
        """Comment or docstring only. Coarse -- replace with an AST diff via RI."""
        lines = [
            ln.lstrip("+- \t") for ln in self.added_lines + self.removed_lines if ln.strip(" \t+-")
        ]
        return bool(lines) and all(ln.startswith(_COMMENT_STARTS) for ln in lines)


class ImpactResult(BaseModel):
    classes: set[ImpactClass]
    invalidated: set[Evidence]
    rationale: list[str]

    @property
    def requires_human(self) -> bool:
        return ImpactClass.UNCLASSIFIABLE in self.classes


_PATTERN_TO_CLASS: dict[str, ImpactClass] = {
    "authorization_predicate": ImpactClass.AUTHZ_CHANGED,
    "sql_construction": ImpactClass.PERSISTENCE_SEMANTICS,
    "transaction_boundary": ImpactClass.PERSISTENCE_SEMANTICS,
    "outbound_network": ImpactClass.NEW_EGRESS_OR_STORE,
    "pii_dataflow": ImpactClass.PII_DATAFLOW_CHANGED,
    "secret_access": ImpactClass.SECURITY_SENSITIVE,
}


def classify(
    changes: list[FileChange],
    registry: SecuritySensitiveRegistry,
    *,
    surface_changed: bool = False,
    new_dependencies: bool = False,
) -> ImpactResult:
    """Classify a diff. Unknown means UNCLASSIFIABLE, never safe."""
    classes: set[ImpactClass] = set()
    why: list[str] = []

    if surface_changed:
        classes.add(ImpactClass.INTERFACE_SURFACE)
        why.append("public interface surface changed")
    if new_dependencies:
        classes.add(ImpactClass.NEW_DEPENDENCY)
        why.append("dependency added")

    for change in changes:
        suffix = "." + change.path.rsplit(".", 1)[-1] if "." in change.path else ""
        if suffix not in registry.known_extensions:
            classes.add(ImpactClass.UNCLASSIFIABLE)
            why.append(f"{change.path}: unrecognised file type, failing closed")
            continue

        if change.is_cosmetic:
            classes.add(ImpactClass.COSMETIC)
            why.append(f"{change.path}: comment or docstring only")
            continue

        if registry.path_is_sensitive(change.path):
            classes.add(ImpactClass.SECURITY_SENSITIVE)
            why.append(f"{change.path}: in a security-sensitive path")

        for name in sorted(registry.matched_patterns(change.diff_text)):
            classes.add(_PATTERN_TO_CLASS.get(name, ImpactClass.SECURITY_SENSITIVE))
            why.append(f"{change.path}: matched '{name}'")

        if change.path.endswith(".tf"):
            classes.add(ImpactClass.IAC_CHANGED)
            why.append(f"{change.path}: infrastructure as code")
        normalized_path = "/" + change.path.lstrip("./")
        if "/migrations/" in normalized_path or change.path.endswith(".sql"):
            classes.add(ImpactClass.DB_MIGRATION)
            why.append(f"{change.path}: database migration")

    if not classes:
        classes.add(ImpactClass.ORDINARY_IMPLEMENTATION)
        why.append("no sensitive signal detected")
    if classes - {ImpactClass.COSMETIC}:
        classes.discard(ImpactClass.COSMETIC)

    invalidated: set[Evidence] = set()
    for impact_class in classes:
        invalidated |= INVALIDATES[impact_class]

    return ImpactResult(classes=classes, invalidated=invalidated, rationale=why)
