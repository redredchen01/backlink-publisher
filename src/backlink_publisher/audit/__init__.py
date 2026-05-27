"""Dual-state divergence auditor — read-only diagnostic over the two publish
stores (``publish-history.json`` vs ``events.db`` ``articles``).

Pure read side: no writes, no schema migration, no WAL checkpoint against the
live ``events.db``. See ``readers`` (snapshot + load) and ``diff`` (R1/R3
divergence detection). Plan 2026-05-26-001.
"""

from __future__ import annotations

from .diff import DivergenceRecord, find_divergences
from .readers import AuditReadError, DedupAuditRow, StoreSnapshot, read_snapshot

__all__ = [
    "AuditReadError",
    "StoreSnapshot",
    "DedupAuditRow",
    "read_snapshot",
    "DivergenceRecord",
    "find_divergences",
]
