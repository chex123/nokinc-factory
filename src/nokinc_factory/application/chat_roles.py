"""Deterministic routing and bounded browser-supplied conversation context."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChatMode = Literal["auto", "business", "architecture", "coding"]
AgentRole = Literal["business_analyst", "architect", "code_analyst"]
AnalysisProfile = Literal["architecture", "coding"]
MAX_DOER_REVIEW_ROUNDS = 3
MAX_MODEL_CALLS_PER_TURN = MAX_DOER_REVIEW_ROUNDS * 2

_ARCHITECTURE_SIGNALS = re.compile(
    r"\b(architect(?:ure)?|system design|data flow|service boundary|"
    r"component design|integration design|security design)\b",
    re.IGNORECASE,
)
_CODE_SIGNALS = re.compile(
    r"\b(code|function|method|class|bug|test|error|stack trace|endpoint|"
    r"login|token|file|module|implementation|source|repository|repo)\b",
    re.IGNORECASE,
)
_RUNTIME_EVIDENCE_SIGNALS = re.compile(
    r"\b(end[- ]to[- ]end|e2e|runtime|simulate|simulation|"
    r"run (?:it|this|the (?:app|application|service|tests?))|"
    r"(?:does|will) it work|works? in practice|when (?:running|deployed))\b",
    re.IGNORECASE,
)


class ConversationTurn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


def route_chat_role(
    *,
    mode: ChatMode,
    message: str,
    repository: str | None,
) -> tuple[AgentRole, AnalysisProfile | None]:
    if mode == "business":
        return "business_analyst", None
    if mode == "architecture":
        if repository is None:
            raise ValueError("Architect discussion requires a selected repository")
        return "architect", "architecture"
    if mode == "coding":
        if repository is None:
            raise ValueError("Code analysis requires a selected repository")
        return "code_analyst", "coding"

    if _ARCHITECTURE_SIGNALS.search(message):
        if repository is None:
            raise ValueError("Architect discussion requires a selected repository")
        return "architect", "architecture"
    if _CODE_SIGNALS.search(message):
        if repository is None:
            raise ValueError("Code analysis requires a selected repository")
        return "code_analyst", "coding"
    return "business_analyst", None


def requires_runtime_evidence(message: str) -> bool:
    """Identify explicit execution/simulation asks that source citations cannot prove."""
    return bool(_RUNTIME_EVIDENCE_SIGNALS.search(message))