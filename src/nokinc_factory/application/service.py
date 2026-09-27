"""Authenticated factory service boundary.

This module owns HTTP authentication and tenant-scoped workflow intake. It does
not claim provider approval, model execution, deployment or release authority.
Those capabilities must be supplied by trusted adapters before the service is
used for consequential work.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from threading import RLock
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nokinc_factory.application.business_analyst import BusinessAnalystResult
from nokinc_factory.application.chat_roles import (
    AgentRole,
    AnalysisProfile,
    ChatMode,
    ConversationTurn,
    requires_runtime_evidence,
    route_chat_role,
)
from nokinc_factory.application.grounded_repository_discussion import (
    GroundedDiscussionResult,
    ModelRunTelemetry,
)
from nokinc_factory.application.web import mount_chat
from nokinc_factory.domain.review_base import Identifier, Moment, ReviewModel, content_digest, utc
from nokinc_factory.domain.states import WorkItemState

Role = Literal["viewer", "operator", "admin"]
EventKind = Literal[
    "INTAKE",
    "GATE_REQUESTED",
    "CHAT_TURN_RESERVED",
    "GROUNDED_ANALYSIS_RECORDED",
    "CHAT_TURN_RECORDED",
]
OutboxStatus = Literal["PENDING", "CLAIMED", "ACKED"]
InboxStatus = Literal["RECEIVED", "PROCESSED"]
GateDecision = Literal["approve", "reject"]
_TOKEN_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_ALLOWED_ROLES = frozenset(("viewer", "operator", "admin"))
_SESSION_COOKIE_NAME = "__Host-factory_id_token"
_LOGGER = logging.getLogger(__name__)


class Principal(BaseModel):
    """An authenticated tenant principal; claims are accepted only after MAC verification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: Identifier
    subject_id: Identifier
    issuer: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=200)
    roles: tuple[Role, ...] = Field(min_length=1, max_length=3)
    issued_at: Moment
    expires_at: Moment

    @field_validator("roles")
    @classmethod
    def _roles_are_distinct_and_known(cls, roles: tuple[Role, ...]) -> tuple[Role, ...]:
        if len(set(roles)) != len(roles) or any(role not in _ALLOWED_ROLES for role in roles):
            raise ValueError("roles must be distinct supported roles")
        return roles


class PrincipalVerifier(Protocol):
    def verify(self, token: str, *, now: datetime) -> Principal: ...


class BrowserLoginAttempt(Protocol):
    @property
    def authorization_url(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def nonce(self) -> str: ...

    @property
    def code_verifier(self) -> str: ...


class BrowserLoginPort(Protocol):
    def start(self) -> BrowserLoginAttempt: ...

    def exchange_code(self, *, code: str, code_verifier: str) -> str: ...

    def verify_id_token(
        self,
        token: str,
        *,
        now: datetime,
        expected_nonce: str,
    ) -> Principal: ...

    def logout_url(self) -> str: ...


class GitHubAppCredentialPort(Protocol):
    def token_for(self, repository: str) -> object: ...


class GitHubRepositoryInfo(Protocol):
    @property
    def full_name(self) -> str: ...

    @property
    def default_branch(self) -> str: ...

    @property
    def visibility(self) -> str: ...

    @property
    def archived(self) -> bool: ...


class GitHubRepositoryReaderPort(Protocol):
    def read(self, repository: str) -> GitHubRepositoryInfo: ...


class GroundedDiscussionPort(Protocol):
    def answer(
        self,
        *,
        repository: str | None = None,
        repositories: tuple[str, ...] = (),
        question: str,
        profile: Literal["architecture", "coding"],
        history: tuple[ConversationTurn, ...] = (),
    ) -> GroundedDiscussionResult: ...


class BusinessAnalystPort(Protocol):
    def answer(
        self,
        *,
        work_item_id: str,
        question: str,
        repository: str | None = None,
        history: tuple[ConversationTurn, ...] = (),
    ) -> BusinessAnalystResult: ...


class HmacTokenIssuer:
    """Small local token boundary for development and controlled service tests.

    Production should replace this with an OIDC/JWKS adapter while preserving
    the Principal contract. The shared secret must come from a credential broker,
    never from model-controlled input or repository text.
    """

    def __init__(self, *, secret: bytes, issuer: str, audience: str) -> None:
        if len(secret) < 16:
            raise ValueError("authentication secret is too short")
        if not issuer.strip() or not audience.strip():
            raise ValueError("issuer and audience are required")
        self._secret = bytes(secret)
        self.issuer = issuer
        self.audience = audience

    @staticmethod
    def _encode(value: object) -> str:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(segment: str) -> object:
        if not segment or not _TOKEN_SEGMENT.fullmatch(segment):
            raise ValueError("invalid token segment")
        padding = "=" * (-len(segment) % 4)
        try:
            raw = base64.urlsafe_b64decode(segment + padding)
            return json.loads(raw, object_pairs_hook=HmacTokenIssuer._unique_object)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid token JSON") from exc

    @staticmethod
    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate token claim")
            result[key] = value
        return result

    def issue(self, *, tenant_id: str, subject_id: str, roles: tuple[Role, ...],
              now: datetime, expires_in_seconds: int) -> str:
        current = utc(now)
        if expires_in_seconds <= 0:
            raise ValueError("token lifetime must be positive")
        payload = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": subject_id,
            "tenant": tenant_id,
            "roles": list(roles),
            "iat": int(current.timestamp()),
            "exp": int((current + timedelta(seconds=expires_in_seconds)).timestamp()),
        }
        header = self._encode({"alg": "HS256", "typ": "JWT"})
        body = self._encode(payload)
        unsigned = f"{header}.{body}".encode("ascii")
        signature = base64.urlsafe_b64encode(
            hmac.new(self._secret, unsigned, sha256).digest()
        ).rstrip(b"=").decode("ascii")
        return f"{header}.{body}.{signature}"

    def verify(self, token: str, *, now: datetime) -> Principal:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("invalid bearer token")
        header_value, payload_value, signature_value = parts
        header = self._decode(header_value)
        payload = self._decode(payload_value)
        if not isinstance(header, dict) or header != {"alg": "HS256", "typ": "JWT"}:
            raise ValueError("unsupported token header")
        if not isinstance(payload, dict):
            raise ValueError("invalid token claims")
        unsigned = f"{header_value}.{payload_value}".encode("ascii")
        expected = base64.urlsafe_b64encode(
            hmac.new(self._secret, unsigned, sha256).digest()
        ).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(expected, signature_value):
            raise ValueError("invalid token signature")
        if payload.get("iss") != self.issuer or payload.get("aud") != self.audience:
            raise ValueError("token issuer or audience mismatch")
        issued = payload.get("iat")
        expires = payload.get("exp")
        if (isinstance(issued, bool) or not isinstance(issued, int)
                or isinstance(expires, bool) or not isinstance(expires, int)):
            raise ValueError("token timestamps must be integers")
        try:
            issued_at = datetime.fromtimestamp(issued, UTC)
            expires_at = datetime.fromtimestamp(expires, UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("token timestamp is invalid") from exc
        current = utc(now)
        if current < issued_at:
            raise ValueError("token was issued in the future")
        if current >= expires_at:
            raise ValueError("token is expired")
        subject = payload.get("sub")
        tenant = payload.get("tenant")
        roles = payload.get("roles")
        if (not isinstance(subject, str) or not isinstance(tenant, str)
            or not isinstance(roles, list)):
            raise ValueError("required token claims are invalid")
        if any(not isinstance(role, str) for role in roles):
            raise ValueError("token roles are invalid")
        return Principal.model_validate({
            "tenant_id": tenant, "subject_id": subject, "issuer": self.issuer,
            "audience": self.audience, "roles": tuple(roles),
            "issued_at": issued_at, "expires_at": expires_at,
        })


class WorkflowItem(ReviewModel):
    work_item_id: Identifier
    tenant_id: Identifier
    created_by: Identifier
    state: WorkItemState = WorkItemState.REFINING
    title: str = Field(min_length=1, max_length=200)
    created_at: Moment


class GroundedAnalysisAudit(ReviewModel):
    analysis_id: Identifier
    analysis_profile: Literal["architecture", "coding"]
    repository: str = Field(min_length=3, max_length=200)
    repositories: tuple[str, ...] | None = Field(
        default=None,
        max_length=8,
        exclude_if=lambda value: value is None,
    )
    status: Literal["ANSWERED", "NEEDS_CLARIFICATION"]
    default_branch: str = Field(min_length=1, max_length=255)
    context_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_runs: tuple[ModelRunTelemetry, ...] = Field(min_length=2, max_length=2)
    elapsed_ms: int = Field(ge=0)


class ChatTurnAudit(ReviewModel):
    agent_role: AgentRole
    analysis_profile: Literal["business", "architecture", "coding"]
    repository: str | None = Field(default=None, min_length=3, max_length=200)
    repositories: tuple[str, ...] | None = Field(
        default=None,
        max_length=8,
        exclude_if=lambda value: value is None,
    )
    status: Literal["ELICITING", "ANSWERED", "NEEDS_CLARIFICATION"]
    input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    history_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    response_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_runs: tuple[ModelRunTelemetry, ...] = Field(min_length=2, max_length=2)
    elapsed_ms: int = Field(ge=0)


class ChatTurnReservation(ReviewModel):
    agent_role: AgentRole
    analysis_profile: Literal["business", "architecture", "coding"]
    repository: str | None = Field(default=None, min_length=3, max_length=200)
    repositories: tuple[str, ...] | None = Field(
        default=None,
        max_length=8,
        exclude_if=lambda value: value is None,
    )
    input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    history_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_call_count: int = Field(default=2, strict=True, ge=2, le=2)


class ChatModelBudgetExceeded(ValueError):
    """The tenant's explicit live model-invocation allowance is exhausted."""


class WorkflowEvent(ReviewModel):
    event_id: Identifier
    tenant_id: Identifier
    work_item_id: Identifier
    actor_id: Identifier
    kind: EventKind
    occurred_at: Moment
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    grounded_analysis: GroundedAnalysisAudit | None = None
    chat_turn_audit: ChatTurnAudit | None = None
    chat_turn_reservation: ChatTurnReservation | None = None

    @model_validator(mode="after")
    def _content_address(self) -> WorkflowEvent:
        if self.kind == "GROUNDED_ANALYSIS_RECORDED":
            if self.grounded_analysis is None:
                raise ValueError("grounded analysis event requires grounded analysis audit")
            if self.payload_digest != self.grounded_analysis.content_digest:
                raise ValueError("grounded analysis event payload digest mismatch")
        elif self.grounded_analysis is not None:
            raise ValueError("grounded analysis audit is only valid on analysis events")
        if self.kind == "CHAT_TURN_RECORDED":
            if self.chat_turn_audit is None:
                raise ValueError("chat event requires a redacted chat-turn audit")
            if self.payload_digest != self.chat_turn_audit.content_digest:
                raise ValueError("chat event payload digest mismatch")
        elif self.chat_turn_audit is not None:
            raise ValueError("chat-turn audit is only valid on chat events")
        if self.kind == "CHAT_TURN_RESERVED":
            if self.chat_turn_reservation is None:
                raise ValueError("chat reservation event requires a reservation")
            if self.payload_digest != self.chat_turn_reservation.content_digest:
                raise ValueError("chat reservation payload digest mismatch")
        elif self.chat_turn_reservation is not None:
            raise ValueError("chat reservation is only valid on reservation events")
        excluded_fields = {"content_digest"}
        if self.grounded_analysis is None:
            excluded_fields.add("grounded_analysis")
        if self.chat_turn_audit is None:
            excluded_fields.add("chat_turn_audit")
        if self.chat_turn_reservation is None:
            excluded_fields.add("chat_turn_reservation")
        expected = content_digest(self.model_dump(
            mode="json",
            exclude=excluded_fields,
        ))
        if self.content_digest and self.content_digest != expected:
            raise ValueError("Workflow event content_digest mismatch")
        object.__setattr__(self, "content_digest", expected)
        return self


class OutboxEntry(ReviewModel):
    event_id: Identifier
    tenant_id: Identifier
    work_item_id: Identifier
    topic: Identifier
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: OutboxStatus = "PENDING"
    attempts: int = Field(default=0, strict=True, ge=0)
    worker_id: Identifier | None = None
    lease_expires_at: Moment | None = None
    created_at: Moment
    updated_at: Moment


class InboxEntry(ReviewModel):
    event_id: Identifier
    tenant_id: Identifier
    source: Identifier
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: InboxStatus = "RECEIVED"
    received_at: Moment
    processed_at: Moment | None = None


class InMemoryWorkflowStore:
    """Thread-safe local store for development; production needs Postgres wiring."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], WorkflowItem] = {}
        self._events: dict[tuple[str, str], list[WorkflowEvent]] = {}
        self._lock = RLock()

    def create_intake(self, *, tenant_id: str, actor_id: str, now: datetime,
                      message: str, repository: str | None = None,
                      repositories: tuple[str, ...] = (),
                      analysis_profile: str | None = None) -> WorkflowItem:
        current = utc(now)
        item = WorkflowItem(
            work_item_id=f"wi-{uuid4().hex}", tenant_id=tenant_id, created_by=actor_id,
            title="Conversation intake", created_at=current,
        )
        event = WorkflowEvent(
            event_id=f"evt-{uuid4().hex}", tenant_id=tenant_id,
            work_item_id=item.work_item_id, actor_id=actor_id, kind="INTAKE",
            occurred_at=current,
            payload_digest=content_digest(
                {
                    "message": message,
                    "repositories": repositories,
                    "analysis_profile": analysis_profile,
                }
                if len(repositories) > 1
                else {"message": message}
                if repository is None
                else {
                    "message": message,
                    "repository": repository,
                    "analysis_profile": analysis_profile,
                }
            ),
        )
        with self._lock:
            self._items[(tenant_id, item.work_item_id)] = item
            self._events[(tenant_id, item.work_item_id)] = [event]
        return item

    def record_grounded_analysis(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        actor_id: str,
        analysis: GroundedAnalysisAudit,
        now: datetime,
    ) -> WorkflowEvent:
        event = WorkflowEvent(
            event_id=f"evt-{uuid4().hex}",
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            actor_id=actor_id,
            kind="GROUNDED_ANALYSIS_RECORDED",
            occurred_at=utc(now),
            payload_digest=analysis.content_digest,
            grounded_analysis=analysis,
        )
        with self._lock:
            key = (tenant_id, work_item_id)
            if key not in self._items:
                raise ValueError("Work item not found")
            self._events[key].append(event)
        return event

    def record_chat_turn(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        actor_id: str,
        audit: ChatTurnAudit,
        now: datetime,
    ) -> WorkflowEvent:
        event = WorkflowEvent(
            event_id=f"evt-{uuid4().hex}",
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            actor_id=actor_id,
            kind="CHAT_TURN_RECORDED",
            occurred_at=utc(now),
            payload_digest=audit.content_digest,
            chat_turn_audit=audit,
        )
        with self._lock:
            key = (tenant_id, work_item_id)
            item = self._items.get(key)
            if item is None or item.created_by != actor_id:
                raise ValueError("Work item not found")
            self._events[key].append(event)
        return event

    def reserve_chat_turn(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        actor_id: str,
        reservation: ChatTurnReservation,
        limit: int,
        now: datetime,
    ) -> WorkflowEvent:
        if limit <= 0:
            raise ChatModelBudgetExceeded("Model-backed chat is disabled by budget policy")
        with self._lock:
            key = (tenant_id, work_item_id)
            item = self._items.get(key)
            if item is None or item.created_by != actor_id:
                raise ValueError("Work item not found")
            used = sum(
                event.kind == "CHAT_TURN_RESERVED"
                for item_key, events in self._events.items()
                if item_key[0] == tenant_id
                for event in events
            )
            if used >= limit:
                raise ChatModelBudgetExceeded("Tenant chat model-turn budget exhausted")
            event = WorkflowEvent(
                event_id=f"evt-{uuid4().hex}",
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                actor_id=actor_id,
                kind="CHAT_TURN_RESERVED",
                occurred_at=utc(now),
                payload_digest=reservation.content_digest,
                chat_turn_reservation=reservation,
            )
            self._events[key].append(event)
            return event

    def get(self, *, tenant_id: str, work_item_id: str) -> WorkflowItem | None:
        with self._lock:
            return self._items.get((tenant_id, work_item_id))

    def list(self, *, tenant_id: str) -> tuple[WorkflowItem, ...]:
        with self._lock:
            return tuple(item for (owner, _), item in self._items.items() if owner == tenant_id)

    def add_gate_request(self, *, tenant_id: str, work_item_id: str, actor_id: str,
                         gate: str, decision: GateDecision,
                         decision_digest: str, now: datetime) -> WorkflowEvent:
        current = utc(now)
        event = WorkflowEvent(
            event_id=f"evt-{uuid4().hex}", tenant_id=tenant_id, work_item_id=work_item_id,
            actor_id=actor_id, kind="GATE_REQUESTED", occurred_at=current,
            payload_digest=content_digest({
                "gate": gate,
                "decision": decision,
                "decision_digest": decision_digest,
            }),
        )
        with self._lock:
            self._events[(tenant_id, work_item_id)].append(event)
        return event

    def trace(self, *, tenant_id: str, work_item_id: str) -> tuple[WorkflowEvent, ...]:
        with self._lock:
            return tuple(self._events.get((tenant_id, work_item_id), ()))


class WorkflowStore(Protocol):
    def create_intake(self, *, tenant_id: str, actor_id: str, now: datetime,
                      message: str, repository: str | None = None,
                      repositories: tuple[str, ...] = (),
                      analysis_profile: str | None = None) -> WorkflowItem: ...

    def record_grounded_analysis(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        actor_id: str,
        analysis: GroundedAnalysisAudit,
        now: datetime,
    ) -> WorkflowEvent: ...

    def record_chat_turn(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        actor_id: str,
        audit: ChatTurnAudit,
        now: datetime,
    ) -> WorkflowEvent: ...

    def reserve_chat_turn(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        actor_id: str,
        reservation: ChatTurnReservation,
        limit: int,
        now: datetime,
    ) -> WorkflowEvent: ...

    def get(self, *, tenant_id: str, work_item_id: str) -> WorkflowItem | None: ...

    def list(self, *, tenant_id: str) -> tuple[WorkflowItem, ...]: ...

    def add_gate_request(self, *, tenant_id: str, work_item_id: str, actor_id: str,
                         gate: str, decision: GateDecision,
                         decision_digest: str, now: datetime) -> WorkflowEvent: ...

    def trace(self, *, tenant_id: str, work_item_id: str) -> tuple[WorkflowEvent, ...]: ...


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=20_000)
    repository: str | None = Field(default=None, min_length=3, max_length=200)
    repositories: tuple[str, ...] = Field(default=(), max_length=4)
    mode: ChatMode | None = None
    analysis_profile: AnalysisProfile | None = None
    work_item_id: str | None = Field(default=None, min_length=3, max_length=200)
    history: tuple[ConversationTurn, ...] = Field(default=(), max_length=12)

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value

    @field_validator("repository")
    @classmethod
    def _legacy_repository_is_one_owner_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = value.split("/")
        if (
            len(parts) != 2
            or any(not part or part != part.strip() or part in {".", ".."} for part in parts)
            or any(delimiter in value for delimiter in ("|", ",", ";"))
        ):
            raise ValueError("repository must be a single owner/name value")
        return value

    @field_validator("repositories")
    @classmethod
    def _repositories_are_distinct_owner_names(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(
            not value.strip()
            or len(value) > 200
            or len(value.split("/")) != 2
            or any(not part or part in {".", ".."} for part in value.split("/"))
            for value in values
        ):
            raise ValueError("repositories must contain owner/name values")
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("repository selection must be distinct")
        return values

    @property
    def selected_repositories(self) -> tuple[str, ...]:
        if self.repositories:
            return self.repositories
        return (self.repository,) if self.repository is not None else ()

    @model_validator(mode="after")
    def _bounded_history_requires_conversation(self) -> ChatRequest:
        if self.repository is not None and self.repositories:
            raise ValueError("use repository or repositories, not both")
        if sum(len(turn.content) for turn in self.history) > 24_000:
            raise ValueError("conversation history exceeds the configured bound")
        if self.history and self.work_item_id is None:
            raise ValueError("conversation history requires a work_item_id")
        if self.history and self.history[-1].role != "assistant":
            raise ValueError("conversation history must end with an assistant turn")
        if any(
            turn.role != ("user" if index % 2 == 0 else "assistant")
            for index, turn in enumerate(self.history)
        ):
            raise ValueError("conversation history roles must alternate")
        return self


class GateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate: str = Field(pattern=r"^gate-[1-4]$")
    decision: GateDecision
    decision_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class IntakeResponse(BaseModel):
    work_item_id: str
    status: Literal["INTAKE_RECORDED", "AGENT_REPLIED"]
    state: WorkItemState
    tenant_id: str
    agent_role: AgentRole | None = None
    analysis_profile: Literal["business", "architecture", "coding"] | None = None
    reply: str | None = None
    analysis: GroundedDiscussionResult | None = None
    business_analysis: BusinessAnalystResult | None = None


class WorkItemResponse(BaseModel):
    work_item_id: str
    state: WorkItemState
    title: str
    created_at: datetime


class StatusResponse(BaseModel):
    items: tuple[WorkItemResponse, ...]


class TraceResponse(BaseModel):
    work_item_id: str
    events: tuple[WorkflowEvent, ...]


class GitHubRepositoryTokenStatus(BaseModel):
    repository: str
    status: Literal["TOKEN_ISSUED", "NOT_AVAILABLE"]


class GitHubAccessResponse(BaseModel):
    repositories: tuple[GitHubRepositoryTokenStatus, ...]


class GitHubRepositorySummary(BaseModel):
    repository: str
    full_name: str
    default_branch: str
    visibility: Literal["public", "private", "internal"]
    archived: bool


class GitHubRepositoryListResponse(BaseModel):
    repositories: tuple[GitHubRepositorySummary, ...]


class GateResponse(BaseModel):
    status: Literal["NOT_AVAILABLE"]
    reason: Literal["APPROVAL_PROVIDER_NOT_CONFIGURED"]
    work_item_id: str


def _item_response(item: WorkflowItem) -> WorkItemResponse:
    return WorkItemResponse(
        work_item_id=item.work_item_id, state=item.state,
        title=item.title, created_at=item.created_at,
    )


def create_app(*, auth_secret: bytes | None = None, issuer: str = "",
               audience: str = "", store: WorkflowStore | None = None,
               principal_verifier: PrincipalVerifier | None = None,
               browser_login: BrowserLoginPort | None = None,
               cookie_auth_origin: str | None = None,
               github_app_broker: GitHubAppCredentialPort | None = None,
               github_repository_reader: GitHubRepositoryReaderPort | None = None,
               grounded_discussion: GroundedDiscussionPort | None = None,
               business_analyst: BusinessAnalystPort | None = None,
               chat_model_turn_limit: int = 0,
               chat_model_tenant_id: str | None = None,
               github_repositories: tuple[str, ...] = (),
               clock: Callable[[], datetime] | None = None) -> FastAPI:
    chosen_store = store or InMemoryWorkflowStore()
    now = clock or (lambda: datetime.now(UTC))
    if auth_secret is not None and principal_verifier is not None:
        raise ValueError("Configure one principal verifier, not both HMAC and OIDC")
    authenticator: PrincipalVerifier | None = principal_verifier
    if authenticator is None and auth_secret is not None:
        authenticator = HmacTokenIssuer(secret=auth_secret, issuer=issuer, audience=audience)
    if browser_login is not None:
        origin_parts = urlsplit(cookie_auth_origin or "")
        if (
            origin_parts.scheme != "https"
            or not origin_parts.hostname
            or origin_parts.username is not None
            or origin_parts.password is not None
            or origin_parts.path not in ("", "/")
            or origin_parts.query
            or origin_parts.fragment
        ):
            raise ValueError("browser login requires a plain HTTPS cookie auth origin")
        cookie_auth_origin = f"https://{origin_parts.netloc}"
    if github_app_broker is not None and (
        not github_repositories
        or len(github_repositories) != len(set(github_repositories))
    ):
        raise ValueError("GitHub App broker requires a distinct repository allowlist")
    if isinstance(chat_model_turn_limit, bool) or chat_model_turn_limit < 0:
        raise ValueError("chat model-turn limit must be a nonnegative integer")
    if chat_model_turn_limit > 0 and not chat_model_tenant_id:
        raise ValueError("a tenant ID is required when a chat model budget is enabled")
    application = FastAPI(title="nokinc-factory")
    mount_chat(application)
    application.state.github_app_broker = github_app_broker
    application.state.github_repository_reader = github_repository_reader
    application.state.grounded_discussion = grounded_discussion
    application.state.business_analyst = business_analyst
    application.state.chat_model_turn_limit = chat_model_turn_limit
    application.state.github_repository_allowlist = github_repositories

    def principal(
        request: Request,
        authorization: str | None = Header(default=None),
        factory_id_token: str | None = Cookie(default=None, alias=_SESSION_COOKIE_NAME),
    ) -> Principal:
        if authenticator is None:
            raise HTTPException(status_code=503, detail="authentication not configured")
        token: str | None = None
        if authorization is not None and authorization.startswith("Bearer "):
            token = authorization[7:]
        elif factory_id_token is not None and browser_login is not None:
            if request.method not in {"GET", "HEAD", "OPTIONS"} and (
                request.headers.get("origin") != cookie_auth_origin
            ):
                raise HTTPException(status_code=403, detail="same-origin request required")
            token = factory_id_token
        if token is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            return authenticator.verify(token, now=now())
        except (ValueError, OverflowError):
            raise HTTPException(status_code=401, detail="invalid authentication") from None

    def operator(current: Principal = Depends(principal)) -> Principal:
        if "operator" not in current.roles and "admin" not in current.roles:
            raise HTTPException(status_code=403, detail="operator role required")
        return current

    if browser_login is not None:
        state_cookie = "__Host-factory_oidc_state"
        nonce_cookie = "__Host-factory_oidc_nonce"
        verifier_cookie = "__Host-factory_oidc_verifier"
        id_token_cookie = _SESSION_COOKIE_NAME

        def clear_login_cookies(response: Response) -> None:
            for name in (state_cookie, nonce_cookie, verifier_cookie):
                response.delete_cookie(
                    name,
                    path="/",
                    secure=True,
                    httponly=True,
                    samesite="lax",
                )

        @application.get("/auth/login", include_in_schema=False)
        def auth_login() -> RedirectResponse:
            attempt = browser_login.start()
            response = RedirectResponse(attempt.authorization_url, status_code=302)
            for name, value in (
                (state_cookie, attempt.state),
                (nonce_cookie, attempt.nonce),
                (verifier_cookie, attempt.code_verifier),
            ):
                response.set_cookie(
                    name,
                    value,
                    max_age=300,
                    path="/",
                    secure=True,
                    httponly=True,
                    samesite="lax",
                )
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response

        @application.get("/auth/callback", include_in_schema=False)
        def auth_callback(
            request: Request,
            code: str | None = None,
            state: str | None = None,
            error: str | None = None,
            saved_state: str | None = Cookie(default=None, alias=state_cookie),
            saved_nonce: str | None = Cookie(default=None, alias=nonce_cookie),
            saved_verifier: str | None = Cookie(default=None, alias=verifier_cookie),
        ) -> Response:
            failure = JSONResponse({"detail": "sign-in failed"}, status_code=400)
            clear_login_cookies(failure)
            if (
                error is not None
                or code is None
                or len(code) > 8192
                or state is None
                or len(state) > 256
                or saved_state is None
                or saved_nonce is None
                or saved_verifier is None
                or len(request.query_params.getlist("code")) != 1
                or len(request.query_params.getlist("state")) != 1
                or not compare_digest(state, saved_state)
            ):
                return failure
            try:
                id_token = browser_login.exchange_code(
                    code=code,
                    code_verifier=saved_verifier,
                )
                callback_time = now()
                identity = browser_login.verify_id_token(
                    id_token,
                    now=callback_time,
                    expected_nonce=saved_nonce,
                )
            except (ValueError, OverflowError):
                return failure
            max_age = int((identity.expires_at - callback_time).total_seconds())
            if max_age <= 0:
                return failure
            response = RedirectResponse("/chat", status_code=303)
            clear_login_cookies(response)
            response.set_cookie(
                id_token_cookie,
                id_token,
                max_age=max_age,
                path="/",
                secure=True,
                httponly=True,
                samesite="lax",
            )
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response

        @application.get("/auth/logout", include_in_schema=False)
        def auth_logout() -> RedirectResponse:
            response = RedirectResponse(browser_login.logout_url(), status_code=303)
            response.delete_cookie(
                id_token_cookie,
                path="/",
                secure=True,
                httponly=True,
                samesite="lax",
            )
            response.headers["Cache-Control"] = "no-store"
            return response

        @application.get("/auth/signed-out", include_in_schema=False)
        def auth_signed_out() -> dict[str, str]:
            return {"status": "signed_out"}

    @application.get("/healthz")
    def healthz() -> dict[str, str]:
        authentication = "configured" if authenticator else "not_configured"
        return {"status": "ok", "authentication": authentication}

    @application.get("/v1/github/access", response_model=GitHubAccessResponse)
    def github_access(current: Principal = Depends(operator)) -> GitHubAccessResponse:
        if github_app_broker is None:
            raise HTTPException(status_code=503, detail="GitHub App integration not configured")
        status_by_repository: list[GitHubRepositoryTokenStatus] = []
        for repository in github_repositories:
            try:
                github_app_broker.token_for(repository)
            except Exception:
                status_by_repository.append(GitHubRepositoryTokenStatus(
                    repository=repository,
                    status="NOT_AVAILABLE",
                ))
            else:
                status_by_repository.append(GitHubRepositoryTokenStatus(
                    repository=repository,
                    status="TOKEN_ISSUED",
                ))
        return GitHubAccessResponse(repositories=tuple(status_by_repository))

    @application.get("/v1/github/repositories", response_model=GitHubRepositoryListResponse)
    def github_repository_list(
        current: Principal = Depends(operator),
    ) -> GitHubRepositoryListResponse:
        if github_repository_reader is None:
            raise HTTPException(status_code=503, detail="GitHub repository reader not configured")
        summaries: list[GitHubRepositorySummary] = []
        try:
            for repository in github_repositories:
                metadata = github_repository_reader.read(repository)
                if metadata.full_name.casefold() != repository.casefold():
                    raise ValueError("GitHub repository identity mismatch")
                summaries.append(GitHubRepositorySummary(
                    repository=repository,
                    full_name=metadata.full_name,
                    default_branch=metadata.default_branch,
                    visibility=metadata.visibility,
                    archived=metadata.archived,
                ))
        except Exception:
            raise HTTPException(
                status_code=502,
                detail="GitHub repository metadata is unavailable",
            ) from None
        return GitHubRepositoryListResponse(repositories=tuple(summaries))

    @application.post(
        "/v1/chat", response_model=IntakeResponse, status_code=status.HTTP_202_ACCEPTED,
    )
    def chat(request: ChatRequest, current: Principal = Depends(principal)) -> IntakeResponse:
        selected_repositories = request.selected_repositories
        repository = selected_repositories[0] if selected_repositories else None
        if request.mode is None:
            mode: ChatMode = (
                request.analysis_profile
                or ("architecture" if repository is not None else "business")
            )
        else:
            mode = request.mode
            if request.analysis_profile is not None:
                legacy_mode = request.analysis_profile
                if mode not in {"auto", legacy_mode}:
                    raise HTTPException(
                        status_code=422,
                        detail="mode conflicts with analysis_profile",
                    )
                if mode == "auto":
                    mode = legacy_mode
        try:
            agent_role, profile = route_chat_role(
                mode=mode,
                message=request.message,
                repository=repository,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

        outside_allowlist = tuple(
            selected for selected in selected_repositories
            if selected not in github_repositories
        )
        if outside_allowlist:
            raise HTTPException(
                status_code=403,
                detail=(
                    "repository is outside the pilot allowlist"
                    if len(selected_repositories) == 1
                    else "one or more repositories are outside the pilot allowlist"
                ),
            )
        model_configured = (
            business_analyst is not None
            if agent_role == "business_analyst"
            else grounded_discussion is not None
        )
        if (
            model_configured
            and chat_model_tenant_id is not None
            and current.tenant_id != chat_model_tenant_id
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "CHAT_MODEL_TENANT_NOT_AUTHORIZED",
                    "work_item_id": request.work_item_id,
                },
            )
        if model_configured and "operator" not in current.roles and "admin" not in current.roles:
            raise HTTPException(status_code=403, detail="operator role required")
        if agent_role != "business_analyst" and grounded_discussion is None:
            raise HTTPException(
                status_code=503,
                detail="grounded repository discussion is unavailable",
            )
        if (
            agent_role == "business_analyst"
            and business_analyst is None
            and request.mode is not None
        ):
            raise HTTPException(
                status_code=503,
                detail="Business Analyst is unavailable",
            )
        if model_configured and chat_model_turn_limit <= 0:
            raise HTTPException(
                status_code=503,
                detail="Model-backed chat is disabled by budget policy",
            )
        if requires_runtime_evidence(request.message):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "RUNTIME_EVIDENCE_NOT_CONFIGURED",
                    "message": (
                        "No isolated local runtime worker is configured; "
                        "no runtime claim was generated."
                    ),
                },
            )

        if request.work_item_id is None:
            item = chosen_store.create_intake(
                tenant_id=current.tenant_id,
                actor_id=current.subject_id,
                now=now(),
                message=request.message,
                repository=repository,
                repositories=(selected_repositories if len(selected_repositories) > 1 else ()),
                analysis_profile=profile,
            )
        else:
            existing_item = chosen_store.get(
                tenant_id=current.tenant_id,
                work_item_id=request.work_item_id,
            )
            if existing_item is None or existing_item.created_by != current.subject_id:
                raise HTTPException(status_code=404, detail="conversation not found")
            item = existing_item

        if not model_configured:
            return IntakeResponse(
                work_item_id=item.work_item_id,
                status="INTAKE_RECORDED",
                state=item.state,
                tenant_id=current.tenant_id,
            )

        history_digest = content_digest([
            {"role": turn.role, "content_digest": content_digest(turn.content)}
            for turn in request.history
        ])
        reservation = ChatTurnReservation(
            agent_role=agent_role,
            analysis_profile=profile or "business",
            repository=repository,
            repositories=(selected_repositories if len(selected_repositories) > 1 else None),
            input_digest=content_digest(request.message),
            history_digest=history_digest,
        )
        try:
            chosen_store.reserve_chat_turn(
                tenant_id=current.tenant_id,
                work_item_id=item.work_item_id,
                actor_id=current.subject_id,
                reservation=reservation,
                limit=chat_model_turn_limit,
                now=now(),
            )
        except ChatModelBudgetExceeded:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "CHAT_MODEL_TURN_BUDGET_EXHAUSTED",
                    "work_item_id": item.work_item_id,
                },
            ) from None

        analysis: GroundedDiscussionResult | None = None
        business_result: BusinessAnalystResult | None = None
        run_status: Literal["ELICITING", "ANSWERED", "NEEDS_CLARIFICATION"]
        failure_stage = "provider"
        try:
            if agent_role == "business_analyst":
                assert business_analyst is not None
                business_result = business_analyst.answer(
                    work_item_id=item.work_item_id,
                    question=request.message,
                    repository=repository,
                    history=request.history,
                )
                reply = business_result.reply
                run_status = business_result.status
                context_digest = business_result.context_digest
                model_runs = business_result.model_runs
                elapsed_ms = business_result.elapsed_ms
            else:
                assert repository is not None
                assert profile is not None
                assert grounded_discussion is not None
                if len(selected_repositories) == 1:
                    analysis = grounded_discussion.answer(
                        repository=selected_repositories[0],
                        question=request.message,
                        profile=profile,
                        history=request.history,
                    )
                else:
                    analysis = grounded_discussion.answer(
                        repositories=selected_repositories,
                        question=request.message,
                        profile=profile,
                        history=request.history,
                    )
                reply = analysis.summary
                run_status = analysis.status
                context_digest = analysis.context_digest
                model_runs = analysis.model_runs
                elapsed_ms = analysis.elapsed_ms

            failure_stage = "result_validation"
            response_digest = content_digest(
                business_result.model_dump(mode="json")
                if business_result is not None
                else analysis.model_dump(mode="json") if analysis is not None else reply
            )
            audit = ChatTurnAudit(
                agent_role=agent_role,
                analysis_profile=profile or "business",
                repository=repository,
                repositories=(selected_repositories if len(selected_repositories) > 1 else None),
                status=run_status,
                input_digest=content_digest(request.message),
                history_digest=history_digest,
                response_digest=response_digest,
                context_digest=context_digest,
                model_runs=model_runs,
                elapsed_ms=elapsed_ms,
            )
            failure_stage = "audit_persistence"
            chosen_store.record_chat_turn(
                tenant_id=current.tenant_id,
                work_item_id=item.work_item_id,
                actor_id=current.subject_id,
                audit=audit,
                now=now(),
            )
        except Exception as error:
            diagnostic_code = getattr(error, "diagnostic_code", None)
            if (
                not isinstance(diagnostic_code, str)
                or re.fullmatch(r"[A-Z][A-Z0-9_]{0,47}", diagnostic_code) is None
            ):
                diagnostic_code = "UNCLASSIFIED"
            _LOGGER.error(
                "chat turn failed stage=%s error_type=%s diagnostic_code=%s",
                failure_stage,
                type(error).__name__,
                diagnostic_code,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "AGENT_RESPONSE_UNAVAILABLE",
                    "work_item_id": item.work_item_id,
                },
            ) from None

        return IntakeResponse(
            work_item_id=item.work_item_id,
            status="AGENT_REPLIED",
            state=item.state,
            tenant_id=current.tenant_id,
            agent_role=agent_role,
            analysis_profile=profile or "business",
            reply=reply,
            analysis=analysis,
            business_analysis=business_result,
        )

    @application.get("/v1/status", response_model=StatusResponse)
    def status_view(current: Principal = Depends(principal)) -> StatusResponse:
        return StatusResponse(items=tuple(
            _item_response(item) for item in chosen_store.list(tenant_id=current.tenant_id)
        ))

    @application.get("/v1/work-items/{work_item_id}", response_model=WorkItemResponse)
    def work_item(work_item_id: str, current: Principal = Depends(principal)) -> WorkItemResponse:
        item = chosen_store.get(tenant_id=current.tenant_id, work_item_id=work_item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="work item not found")
        return _item_response(item)

    @application.get("/v1/work-items/{work_item_id}/trace", response_model=TraceResponse)
    def trace(work_item_id: str, current: Principal = Depends(principal)) -> TraceResponse:
        item = chosen_store.get(tenant_id=current.tenant_id, work_item_id=work_item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="work item not found")
        return TraceResponse(work_item_id=work_item_id,
                             events=chosen_store.trace(tenant_id=current.tenant_id,
                                                       work_item_id=work_item_id))

    @application.post("/v1/work-items/{work_item_id}/gate", response_model=GateResponse,
                      status_code=status.HTTP_202_ACCEPTED)
    def gate(request: GateRequest, work_item_id: str,
             current: Principal = Depends(operator)) -> GateResponse:
        item = chosen_store.get(tenant_id=current.tenant_id, work_item_id=work_item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="work item not found")
        chosen_store.add_gate_request(
            tenant_id=current.tenant_id, work_item_id=work_item_id,
            actor_id=current.subject_id, gate=request.gate, decision=request.decision,
            decision_digest=request.decision_digest, now=now(),
        )
        return GateResponse(
            status="NOT_AVAILABLE", reason="APPROVAL_PROVIDER_NOT_CONFIGURED",
            work_item_id=work_item_id,
        )

    return application


app = create_app(
    auth_secret=(os.getenv("FACTORY_AUTH_SECRET") or "").encode() or None,
    issuer=os.getenv("FACTORY_AUTH_ISSUER", "factory"),
    audience=os.getenv("FACTORY_AUTH_AUDIENCE", "factory-api"),
)
