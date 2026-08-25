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
import sys
from collections.abc import Sequence


def _cmd_chat(args: argparse.Namespace) -> int:
    """Elicit a Business Ready story, then create the work item.

    Loops until the Domain Expert returns a story rather than blocking questions.
    Refusing to produce a story is a successful outcome -- see agents/domain_expert.
    """
    raise NotImplementedError("story 6 -- see docs/BACKLOG.md")


def _cmd_gate(args: argparse.Namespace) -> int:
    """Approve a gate and trigger whatever the next stage is.

    G1 approved -> run the Architect, create the design item.
    G2 approved -> create the [TESTS] issue and assign the coding agent.
    """
    raise NotImplementedError("story 4 -- see docs/BACKLOG.md")


def _cmd_status(args: argparse.Namespace) -> int:
    raise NotImplementedError("story 1 -- see docs/BACKLOG.md")


def _cmd_trace(args: argparse.Namespace) -> int:
    """Print the traceability chain for a work item.

    story -> design -> tests PR -> impl PR -> commit -> image digest
          -> spans declared vs spans observed
    """
    raise NotImplementedError("story 11 -- see docs/BACKLOG.md")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory", description="Nokinc Software Factory")
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="elicit a story from a conversation")
    chat.add_argument("--repo", help="target repository", default=None)
    chat.set_defaults(func=_cmd_chat)

    gate = sub.add_parser("gate", help="approve or reject a gate")
    gate.add_argument("work_item", help="work item id")
    group = gate.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", action="store_true")
    gate.set_defaults(func=_cmd_gate)

    status = sub.add_parser("status", help="in-flight work items and their gates")
    status.set_defaults(func=_cmd_status)

    trace = sub.add_parser("trace", help="traceability chain for a work item")
    trace.add_argument("work_item")
    trace.set_defaults(func=_cmd_trace)

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
