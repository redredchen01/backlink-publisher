"""Event substrate (read-side projection of JSON state files).

Public API:
    - ``EventStore`` (U1) — write/read access to ``events.db``
    - ``flush_for`` (U4) — project a JSON source into the event store
    - ``project_run_safe`` (Plan 005 / U4) — fail-safe project-after-run
    - ``ProjectionError`` (U4) — cursor / dispatch failures
    - ``ProjectionResult`` (U4) — counters returned by ``flush_for``
    - ``KINDS`` / ``classify`` — event-kind vocabulary + status classifier
      (dependency-free; safe to import from I/O-free writers)
"""

from . import kinds
from .kinds import KINDS, classify
from .projector import (
    ProjectionError,
    ProjectionResult,
    flush_for,
    project_run_safe,
)
from .store import EventStore

__all__ = [
    "EventStore",
    "ProjectionError",
    "ProjectionResult",
    "flush_for",
    "project_run_safe",
    "kinds",
    "KINDS",
    "classify",
]
