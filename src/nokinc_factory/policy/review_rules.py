"""Deterministic ARP-1 qualification and report checks; no inferred authority."""

from datetime import datetime

from nokinc_factory.domain.review import ArtifactRef, ModelIdentity
from nokinc_factory.domain.review_base import distinct
from nokinc_factory.domain.review_report import Evidence, Finding, ReviewReport
from nokinc_factory.domain.review_session import Invocation, ReviewReason, ReviewSeed, ReviewState


class ReviewViolation(ValueError):
    def __init__(self, reason: ReviewReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def require(condition: bool, reason: ReviewReason) -> None:
    if not condition:
        raise ReviewViolation(reason)


def qualified(model: ModelIdentity) -> bool:
    return (model.family is not None and model.qualification_ref is not None
            and bool(model.capability_refs))


def independent(seed: ReviewSeed, artifact: ArtifactRef, reviewer: ModelIdentity) -> None:
    require(qualified(reviewer) and reviewer.role == "REVIEWER",
            ReviewReason.IDENTITY_UNAVAILABLE)
    family_required = seed.contract.risk_tier == "T2" or seed.policy.require_different_family
    for producer in artifact.producers:
        require(qualified(producer) and producer.role == "PRODUCER",
                ReviewReason.IDENTITY_UNAVAILABLE)
        require(reviewer.canonical_model_id != producer.canonical_model_id
                and reviewer.model != producer.model, ReviewReason.IDENTITY_UNAVAILABLE)
        require(not family_required or reviewer.family != producer.family,
                ReviewReason.IDENTITY_UNAVAILABLE)


def artifact_binding(seed: ReviewSeed, artifact: ArtifactRef) -> None:
    require(artifact.scope == seed.scope
            and artifact.contract_digest == seed.contract.content_digest
            and artifact.context_digest == seed.initial_artifact.context_digest
            and artifact.artifact_key in seed.contract.artifact_scope,
            ReviewReason.BINDING_MISMATCH)


def validate_seed(seed: ReviewSeed) -> None:
    require((seed.scope.tenant_id, seed.scope.work_item_id)
            == (seed.contract.tenant_id, seed.contract.work_item_id), ReviewReason.BINDING_MISMATCH)
    artifact_binding(seed, seed.initial_artifact)
    require(seed.original_doer in seed.initial_artifact.producers
            and seed.original_doer.role == "PRODUCER" and qualified(seed.original_doer),
            ReviewReason.IDENTITY_UNAVAILABLE)
    require(bool(seed.reviewers), ReviewReason.IDENTITY_UNAVAILABLE)
    distinct(item.content_digest for item in seed.reviewers)
    for reviewer in seed.reviewers:
        independent(seed, seed.initial_artifact, reviewer)


def within_window(seed: ReviewSeed, state: ReviewState, now: datetime) -> bool:
    return (now >= state.last_at and now >= seed.started_at
            and (now - seed.started_at).total_seconds() < seed.policy.max_elapsed_seconds)


def blocking(seed: ReviewSeed, finding: Finding) -> bool:
    criterion = next((c for c in seed.contract.criteria if c.criterion_id == finding.criterion_id),
                     None)
    return (criterion is None or criterion.mandatory or finding.kind != "ADVISORY"
            or finding.severity != "LOW")


def evidence_binding(evidence: tuple[Evidence, ...], artifact_digest: str) -> None:
    require(all(item.artifact_digest == artifact_digest for item in evidence),
            ReviewReason.MISSING_EVIDENCE)


def needs_repair(state: ReviewState) -> bool:
    """A reconciled case awaits review; a reopened/new report needs its own repair."""
    return any(item.blocking and not any(
        record.resolution.fingerprint == item.finding.fingerprint
        and record.resolution.report_digest == item.report_digest
        and record.artifact_digest == state.artifact.content_digest
        for record in state.resolutions
    ) for item in state.open_findings)


def validate_report_binding(seed: ReviewSeed, state: ReviewState, invocation: Invocation,
                            report: ReviewReport) -> None:
    """Trust no stop until the parsed report and every supplied observation bind.

    Missing coverage is distinct from foreign evidence: an incomplete inspection
    may reveal a real stop, but no part of a misbound report gains authority.
    """
    require(report.invocation_id == invocation.invocation_id
        and report.binding == invocation.binding
            and report.reviewer == invocation.actor, ReviewReason.BINDING_MISMATCH)
    independent(seed, state.artifact, report.reviewer)
    distinct(item.fingerprint for item in report.findings)
    distinct(item.fingerprint for item in report.rechecks)
    for item in report.criteria:
        evidence_binding(item.evidence, report.binding.artifact_digest)
    for finding in report.findings:
        require(finding.artifact_digest == report.binding.artifact_digest,
                ReviewReason.BINDING_MISMATCH)
        evidence_binding(finding.evidence, report.binding.artifact_digest)
    for recheck in report.rechecks:
        evidence_binding(recheck.evidence, report.binding.artifact_digest)


def validate_report(seed: ReviewSeed, state: ReviewState, report: ReviewReport) -> None:
    """Check completion credit only after validate_report_binding has succeeded."""
    expected = {criterion.criterion_id: criterion for criterion in seed.contract.criteria}
    actual = {item.criterion_id: item for item in report.criteria}
    require(report.complete and len(actual) == len(report.criteria)
        and actual.keys() == expected.keys(),
            ReviewReason.INCOMPLETE_CRITERIA)
    for criterion_id, item in actual.items():
        criterion = expected[criterion_id]
        if item.status == "PASS":
            require(set(criterion.required_evidence) <= {e.evidence_id for e in item.evidence},
                    ReviewReason.MISSING_EVIDENCE)
        if criterion.mandatory:
            require(item.status != "NOT_APPLICABLE", ReviewReason.INCOMPLETE_CRITERIA)
            if item.status == "FAIL":
                require(any(f.criterion_id == criterion_id for f in report.findings),
                        ReviewReason.INCOMPLETE_CRITERIA)
    for finding in report.findings:
        require(finding.criterion_id in expected or finding.kind == "CONTRACT_GAP",
                ReviewReason.CONTRACT_GAP)
    if report.verdict == "CHANGES_REQUIRED":
        require(any(blocking(seed, finding) for finding in report.findings)
                or any(item.blocking for item in state.open_findings),
                ReviewReason.UNSUPPORTED_VERDICT)


def eligibility_reasons(seed: ReviewSeed, state: ReviewState) -> tuple[ReviewReason, ...]:
    """Only actual replayed reports may satisfy the latest final-artifact exit."""
    reasons = list(state.terminal_reasons)
    if state.pending is not None:
        reasons.append(ReviewReason.PENDING)
    if len(state.valid_completed_reports) < seed.policy.min_review_passes:
        reasons.append(ReviewReason.MIN_REVIEWS)
    if any(item.blocking for item in state.open_findings):
        reasons.append(ReviewReason.OPEN_BLOCKERS)
    if not state.valid_completed_reports:
        reasons.append(ReviewReason.UNREVIEWED_ARTIFACT)
    else:
        latest = state.valid_completed_reports[-1]
        if latest.binding != state.artifact.binding():
            reasons.append(ReviewReason.UNREVIEWED_ARTIFACT)
        mandatory = {c.criterion_id for c in seed.contract.criteria if c.mandatory}
        if latest.verdict != "ACCEPT" or any(
            item.status != "PASS" for item in latest.criteria if item.criterion_id in mandatory
        ):
            reasons.append(ReviewReason.MANDATORY_CRITERIA)
    return tuple(dict.fromkeys(reasons))