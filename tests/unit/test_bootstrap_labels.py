"""Item-1 contract for the bootstrap GitHub WorkItemPort label catalogue."""

import re
from pathlib import Path

from nokinc_factory.domain.states import WorkItemState

BOOTSTRAP = Path(__file__).parents[2] / "scripts" / "bootstrap.sh"


def _configured_labels() -> set[str]:
    content = BOOTSTRAP.read_text(encoding="utf-8")
    match = re.search(r"LABELS=\(\n(?P<body>.*?)\n\)", content, re.DOTALL)
    assert match is not None
    return {
        line.strip().split("|", 1)[0].removeprefix('"')
        for line in match.group("body").splitlines()
        if line.strip().startswith('"')
    }


def test_bootstrap_provisions_all_work_item_lifecycle_and_control_labels() -> None:
    labels = _configured_labels()
    lifecycle_labels = {
        f"stage:{state.value.lower().replace('_', '-')}" for state in WorkItemState
    }
    control_labels = {
        "story",
        "design",
        "stage:gate-1-approved",
        "stage:gate-2-approved",
        "stage:gate-3-approved",
        "stage:gate-4-approved",
        "frozen-contract",
        "implementation",
        "tier:T0",
        "tier:T1",
        "tier:T2",
    }

    assert lifecycle_labels <= labels
    assert control_labels <= labels
