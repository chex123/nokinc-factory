"""Authoritative TaskContext loading boundary for deterministic preflight."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from nokinc_factory.domain.preflight import TaskContext


class TaskContextError(RuntimeError):
    """Base class for authoritative TaskContext establishment failures."""


class InvalidTaskContextId(TaskContextError):
    """Raised before provider access when a work item id is not canonical."""


class TaskContextNotFound(TaskContextError):
    """Raised when the authoritative provider cannot find the requested item."""


class TaskContextAuthenticationError(TaskContextError):
    """Raised when provider authentication or authorization cannot be established."""


class TaskContextProviderError(TaskContextError):
    """Raised for provider/API/network/serialization failures."""


class TaskContextRepositoryMismatch(TaskContextError):
    """Raised when provider issue data belongs to another repository or issue."""


@runtime_checkable
class TaskContextLoader(Protocol):
    """Load an explicit authoritative work item as review data."""

    def load(self, work_item_id: str) -> TaskContext:
        """Return validated TaskContext or fail closed with a distinct diagnostic."""
        ...