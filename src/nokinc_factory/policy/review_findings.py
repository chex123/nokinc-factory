"""ARP-1 finding continuity and original-doer reconciliation, independent of prose.

An omitted open blocker is still unresolved on the next completed inspection.
Only a current independent recheck of a recorded resolution closes it. Advisory
repetition never consumes a repair or triggers the repeated-failure breaker.
Bound stops survive uncredited inspections without applying their rechecks.
Resolved cases need a changed artifact or evidence absent from their accumulated
bound history; deleting, reordering or renaming observations is not novelty.
"""

from nokinc_factory.domain.review_base import distinct, renewed
from nokinc_factory.domain.review_report import Finding, FindingRecheck, RepairReport, ReviewReport
from nokinc_factory.domain.review_session import (
    FindingRecord,
    Invocation,
    ResolutionRecord,
    ReviewReason,
    ReviewSeed,
    ReviewState,
)
from nokinc_factory.policy.review_rules import (
    artifact_binding,
    blocking,
    evidence_binding,
    require,
)


def _stop_reasons(seed: ReviewSeed, report: ReviewReport) -> tuple[ReviewReason, ...]:
    reasons: list[ReviewReason] = []
    criteria = {item.criterion_id for item in seed.contract.criteria}
    if any(item.severity == "CRITICAL" for item in report.findings):
        reasons.append(ReviewReason.CRITICAL)
    if report.verdict == "CONTRACT_GAP" or any(
        item.kind == "CONTRACT_GAP" or item.criterion_id not in criteria for item in report.findings
    ):
        reasons.append(ReviewReason.CONTRACT_GAP)
    mandatory = {item.criterion_id for item in seed.contract.criteria if item.mandatory}
    if report.verdict == "CONTEXT_GAP" or any(
        item.status == "UNKNOWN" and item.criterion_id in mandatory for item in report.criteria
    ):
        reasons.append(ReviewReason.CONTEXT_GAP)
    if report.verdict == "ESCALATE":
        reasons.append(ReviewReason.REVIEW_ESCALATED)
    return tuple(reasons)


def retain_stops(seed: ReviewSeed, state: ReviewState, report: ReviewReport) -> ReviewState:
    """After binding validation, retain stops, never closure or completion credit.

    Uncredited inspections cannot advance old occurrence counters or mutate
    unrelated findings. The attempt ledger retains the entire rejected report.
    """
    reasons = _stop_reasons(seed, report)
    if not reasons:
        return state
    criteria = {item.criterion_id for item in seed.contract.criteria}
    records = {item.finding.fingerprint: item for item in state.findings}
    for finding in report.findings:
        if (finding.severity != "CRITICAL" and finding.kind != "CONTRACT_GAP"
                and finding.criterion_id in criteria):
            continue
        previous = records.get(finding.fingerprint)
        records[finding.fingerprint] = FindingRecord(
            finding=finding, report_digest=report.content_digest, blocking=True,
            occurrences=previous.occurrences if previous is not None else 0,
        )
    return renewed(state, findings=tuple(records[key] for key in sorted(records)),
                   terminal_reasons=tuple(dict.fromkeys((*state.terminal_reasons, *reasons))))


def _known_evidence(state: ReviewState, finding: Finding) -> set[str]:
    """Remember case observations across repairs, not just the latest finding.

    Rebinding known bytes to an already closed artifact is not novelty. This
    comparison grants no stale evidence authority: incoming bindings are checked
    separately, and a change since closure independently permits reopening.
    """
    known = {e.digest for record in state.findings
             if record.finding.fingerprint == finding.fingerprint
             for e in record.finding.evidence}
    for report in state.valid_completed_reports:
        observations: tuple[Finding | FindingRecheck, ...] = (*report.findings, *report.rechecks)
        known.update(e.digest for item in observations
                     if item.fingerprint == finding.fingerprint for e in item.evidence)
    known.update(e.digest for record in state.resolutions
                 if record.resolution.fingerprint == finding.fingerprint
                 for e in record.resolution.evidence)
    return known


def reconcile_findings(seed: ReviewSeed, state: ReviewState, report: ReviewReport
                       ) -> tuple[tuple[FindingRecord, ...], tuple[ReviewReason, ...]]:
    records = {item.finding.fingerprint: item for item in state.findings}
    dispositions = {item.criterion_id: item.status for item in report.criteria}
    reported = {item.fingerprint for item in report.findings}
    for recheck in report.rechecks:
        existing = records.get(recheck.fingerprint)
        resolutions = [item for item in state.resolutions
                       if item.resolution.fingerprint == recheck.fingerprint]
        require(existing is not None and existing.closed_by is None and bool(resolutions),
                ReviewReason.INVALID_CLOSURE)
        assert existing is not None
        resolution = resolutions[-1]
        require(resolution.resolution.content_digest == recheck.resolution_digest
            and resolution.resolution.report_digest == existing.report_digest
                and resolution.artifact_digest == report.binding.artifact_digest
                and recheck.check == existing.finding.required_recheck,
                ReviewReason.INVALID_CLOSURE)
        if recheck.status == "PASS":
            require(recheck.fingerprint not in reported
                    and (not existing.blocking
                         or dispositions.get(existing.finding.criterion_id) == "PASS"),
                    ReviewReason.INVALID_CLOSURE)
            records[recheck.fingerprint] = renewed(
                existing, closed_by=report.content_digest,
                closed_artifact_digest=report.binding.artifact_digest)
    for finding in report.findings:
        previous = records.get(finding.fingerprint)
        occurrences = previous.occurrences if previous is not None else 0
        if previous is not None and previous.closed_by is not None:
            changed = (previous.closed_artifact_digest != report.binding.artifact_digest
                       or bool({e.digest for e in finding.evidence}
                               - _known_evidence(state, finding)))
            if not changed:
                continue
            occurrences = 0
        records[finding.fingerprint] = FindingRecord(
            finding=finding, report_digest=report.content_digest,
            blocking=blocking(seed, finding) or (previous is not None and previous.blocking),
            occurrences=occurrences,
        )
    reasons: list[ReviewReason] = []
    for fingerprint, item in records.items():
        if item.closed_by is None and item.blocking:
            updated = renewed(item, occurrences=item.occurrences + 1)
            records[fingerprint] = updated
            if updated.occurrences >= seed.policy.same_unresolved_failure_limit:
                reasons.append(ReviewReason.REPEATED_FAILURE)
    reasons.extend(_stop_reasons(seed, report))
    return tuple(records[key] for key in sorted(records)), tuple(dict.fromkeys(reasons))


def reconcile_repair(seed: ReviewSeed, state: ReviewState, invocation: Invocation,
                     report: RepairReport) -> tuple[ResolutionRecord, ...]:
    require(report.invocation_id == invocation.invocation_id
        and report.binding == invocation.binding
        and report.producer == seed.original_doer == invocation.actor,
        ReviewReason.INVALID_REPAIR)
    artifact_binding(seed, report.artifact)
    require(report.artifact.artifact_key == state.artifact.artifact_key
            and report.artifact.producers == state.artifact.producers,
            ReviewReason.INVALID_REPAIR)
    open_items = {item.finding.fingerprint: item for item in state.open_findings}
    distinct(item.fingerprint for item in report.resolutions)
    proposed = {item.fingerprint for item in report.resolutions}
    require({key for key, item in open_items.items() if item.blocking} <= proposed
            and proposed <= open_items.keys(), ReviewReason.INVALID_REPAIR)
    changed = (report.artifact.payload_digest != state.artifact.payload_digest
               or report.artifact.inputs != state.artifact.inputs
               or report.artifact.build_refs != state.artifact.build_refs)
    records = []
    for resolution in report.resolutions:
        finding = open_items[resolution.fingerprint]
        require(resolution.report_digest == finding.report_digest, ReviewReason.INVALID_REPAIR)
        evidence_binding(resolution.evidence, report.artifact.content_digest)
        require(not finding.blocking or resolution.disposition != "DEFERRED_ADVISORY",
                ReviewReason.INVALID_REPAIR)
        if resolution.disposition == "FIXED":
            require(changed, ReviewReason.NO_PROGRESS)
        if resolution.disposition == "REJECTED_WITH_EVIDENCE":
            require(bool({e.digest for e in resolution.evidence}
                         - {e.digest for e in finding.finding.evidence}), ReviewReason.NO_PROGRESS)
        records.append(ResolutionRecord(
            resolution=resolution, repair_invocation_id=invocation.invocation_id,
            artifact_digest=report.artifact.content_digest, producer=report.producer))
    return tuple(records)