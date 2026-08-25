"""The CLI surface is a contract with the demo script. Keep it stable."""

import pytest

from nokinc_factory.cli import build_parser, main


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["chat"], "chat"),
        (["gate", "12", "--approve"], "gate"),
        (["status"], "status"),
        (["trace", "12"], "trace"),
    ],
)
def test_demo_commands_parse(argv: list[str], expected: str) -> None:
    assert build_parser().parse_args(argv).command == expected


def test_gate_requires_approve_or_reject() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["gate", "12"])


def test_unbuilt_command_exits_cleanly() -> None:
    """A half-built CLI must fail with a pointer, not a traceback."""
    assert main(["status"]) == 2
