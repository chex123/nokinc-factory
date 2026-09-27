"""The factory CLI -- the demo surface.

Four commands carry the whole demonstration:

    factory chat              conversation -> Business Ready story -> issue
    factory gate <n> --approve    advance a gate
    factory status                where every in-flight story is
    factory trace <n>             sentence -> issue -> PR -> commit -> digest -> spans

`trace` is the closing shot. It is also the cheapest thing in the system to
build, because the chain is just IDs propagated into commit trailers, image
labels and span attributes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from nokinc_factory.adapters.gate_evidence_io import read_evidence
from nokinc_factory.adapters.maestro import MaestroToolchain
from nokinc_factory.adapters.review_archive import read_review_archive
from nokinc_factory.adapters.subprocess_toolchain import SubprocessToolchain
from nokinc_factory.adapters.toolchain_config import load_toolchain
from nokinc_factory.application.review_inspection import inspect_review, trace_review
from nokinc_factory.domain.gate_evidence import BaselineContract, SuiteRun
from nokinc_factory.domain.mobile import MaestroSpec
from nokinc_factory.policy.baseline import verify_baseline, verify_candidate
from nokinc_factory.ports.toolchain import GateName, GateStatus


def _cmd_chat(args: argparse.Namespace) -> int:
    """Elicit a Business Ready story, then create the work item.

    Loops until the Domain Expert returns a story rather than blocking questions.
    Refusing to produce a story is a successful outcome -- see agents/domain_expert.
    """
    if not args.message or not args.message.strip():
        print("chat requires --message", file=sys.stderr)
        return 2
    model = args.model or os.getenv("FACTORY_MODEL", "").strip()
    if not model:
        print(json.dumps({
            "status": "NOT_AVAILABLE",
            "reason": "MODEL_PROVIDER_NOT_CONFIGURED",
            "authorizes_merge": False,
        }))
        return 2
    try:
        from nokinc_factory.agents.domain_expert import Conversation, build_agent

        result = build_agent(model).run_sync(
            args.message,
            deps=Conversation(work_item_id=args.work_item_id),
        )
    except Exception:
        print(json.dumps({
            "status": "NOT_AVAILABLE",
            "reason": "MODEL_EXECUTION_UNAVAILABLE",
            "authorizes_merge": False,
        }))
        return 2
    print(result.output.model_dump_json(indent=2))
    return 0


def _cmd_gate(args: argparse.Namespace) -> int:
    """Approve a gate and trigger whatever the next stage is.

    G1 approved -> run the Architect, create the design item.
    G2 approved -> create the [TESTS] issue and assign the coding agent.
    """
    print(json.dumps({
        "status": "NOT_AVAILABLE",
        "reason": "APPROVAL_PROVIDER_NOT_CONFIGURED",
        "work_item_id": args.work_item,
        "authorizes_merge": False,
    }))
    return 2


def _cmd_status(args: argparse.Namespace) -> int:
    """Inspect explicit archived evidence; a live operational backend is not implied."""
    if args.review_session is None:
        print("Operational backend not configured; use --review-session for advisory inspection.",
              file=sys.stderr)
        return 2
    try:
        session = read_review_archive(Path(args.review_session))
        result = inspect_review(session, now=datetime.now(UTC))
    except (ValueError, OSError, RecursionError):
        print("Invalid or unavailable review archive; no live authority established.",
              file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2))
    return 0


def _cmd_trace(args: argparse.Namespace) -> int:
    """Inspect the recorded review chain, explicitly flagging absent release evidence."""
    if args.review_session is None:
        print("Operational backend not configured; provide --review-session for advisory trace.",
              file=sys.stderr)
        return 2
    try:
        session = read_review_archive(Path(args.review_session))
        if session.seed.scope.work_item_id != args.work_item:
            print("Work-item identity does not match the review archive.", file=sys.stderr)
            return 2
        result = trace_review(session, work_item_id=args.work_item)
    except (ValueError, OSError, RecursionError):
        print("Invalid or unavailable review archive; release trace is not established.",
              file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2))
    return 0


def _cmd_mobile_test(args: argparse.Namespace) -> int:
    """Run advisory native flows on an already provisioned isolated worker.

    This never builds/installs an app, approves evidence, or retries failures.
    Local mobile results cannot satisfy protected-branch or release authority.
    """
    try:
        spec = MaestroSpec(
            platform=args.platform, device_id=args.device,
            expected_tests=tuple(args.expected_test), flow_directory=args.flow_directory,
            timeout_seconds=args.timeout,
        )
    except ValidationError:
        print("Invalid mobile test configuration", file=sys.stderr)
        return 2
    adapter = MaestroToolchain(
        spec, artifacts_root=Path(args.artifacts_root) if args.artifacts_root else None,
    )
    result = adapter.run(GateName.MOBILE_E2E, args.repo)
    print(result.model_dump_json(indent=2))
    return {GateStatus.PASS: 0, GateStatus.FAIL: 1, GateStatus.NOT_AVAILABLE: 2}[result.status]


def _cmd_verify_tests(args: argparse.Namespace) -> int:
    """Evaluate declared run evidence without minting runner or approval authority."""
    try:
        contract = read_evidence(args.contract, BaselineContract)
        run = read_evidence(args.run, SuiteRun) if args.run is not None else None
        if args.mode == "baseline":
            if args.candidate_sha is not None:
                raise ValueError("Candidate identity is not a baseline argument")
            before = read_evidence(args.before, SuiteRun) if args.before is not None else None
            result = verify_baseline(contract, before, run)
        else:
            if args.candidate_sha is None or args.before is not None:
                raise ValueError("Candidate mode needs a SHA and no baseline run")
            result = verify_candidate(contract, args.candidate_sha, run)
    except (ValueError, OSError, RecursionError, OverflowError):
        print("Invalid or unavailable test evidence; no authority established.", file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2))
    return {"PASS": 0, "FAIL": 1, "NOT_AVAILABLE": 2, "NOT_APPLICABLE": 3}[result.status]


def _cmd_run_gate(args: argparse.Namespace) -> int:
    """Run one target-declared deterministic gate as advisory evidence."""
    try:
        root = Path(args.repo).resolve()
        spec = load_toolchain(root / args.toolchain)
        result = SubprocessToolchain(
            spec, timeout_seconds=args.timeout, output_bytes=args.output_bytes,
        ).run(GateName(args.gate), str(root))
    except (OSError, ValueError, RecursionError):
        print(json.dumps({
            "status": "NOT_AVAILABLE",
            "reason": "TOOLCHAIN_CONTRACT_UNAVAILABLE",
            "authorizes_merge": False,
        }))
        return 2
    print(result.model_dump_json(indent=2))
    return {GateStatus.PASS: 0, GateStatus.FAIL: 1, GateStatus.NOT_AVAILABLE: 2}[result.status]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory", description="Nokinc Software Factory")
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="elicit a story from a conversation")
    chat.add_argument("--repo", help="target repository", default=None)
    chat.add_argument("--message", help="conversation turn", default=None)
    chat.add_argument("--model", help="qualified model identifier", default=None)
    chat.add_argument("--work-item-id", default="local-chat")
    chat.set_defaults(func=_cmd_chat)

    gate = sub.add_parser("gate", help="approve or reject a gate")
    gate.add_argument("work_item", help="work item id")
    group = gate.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", action="store_true")
    gate.set_defaults(func=_cmd_gate)

    status = sub.add_parser("status", help="in-flight work items and their gates")
    status.add_argument("--review-session", help="local review archive; advisory evidence only")
    status.set_defaults(func=_cmd_status)

    trace = sub.add_parser("trace", help="traceability chain for a work item")
    trace.add_argument("work_item")
    trace.add_argument("--review-session",
                       help="local review archive; not a verified release chain")
    trace.set_defaults(func=_cmd_trace)

    mobile = sub.add_parser("mobile-test", help="advisory Maestro gate on a provisioned worker")
    mobile.add_argument("--repo", default=".")
    mobile.add_argument("--platform", choices=("ios", "android"), required=True)
    mobile.add_argument("--device", required=True)
    mobile.add_argument("--expected-test", action="append", required=True)
    mobile.add_argument("--flow-directory", default=".maestro")
    mobile.add_argument("--timeout", type=int, default=600)
    mobile.add_argument("--artifacts-root")
    mobile.set_defaults(func=_cmd_mobile_test)

    verify = sub.add_parser("verify-tests", help="validate test evidence, not authorization")
    verify.add_argument("mode", choices=("baseline", "candidate"))
    verify.add_argument("--contract", type=Path, required=True)
    verify.add_argument("--before", type=Path)
    verify.add_argument("--run", type=Path)
    verify.add_argument("--candidate-sha")
    verify.set_defaults(func=_cmd_verify_tests)

    run_gate = sub.add_parser("run-gate", help="run a declared deterministic target gate")
    run_gate.add_argument("gate", choices=tuple(gate.value for gate in GateName))
    run_gate.add_argument("--repo", default=".")
    run_gate.add_argument("--toolchain", default=".factory/toolchain.yaml")
    run_gate.add_argument("--timeout", type=float, default=600.0)
    run_gate.add_argument("--output-bytes", type=int, default=64 * 1024)
    run_gate.set_defaults(func=_cmd_run_gate)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except NotImplementedError as exc:
        print(f"not built yet: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
