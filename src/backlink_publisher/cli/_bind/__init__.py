"""Browser-driven channel binding internals — Plan 2026-05-19-001.

This package houses the bind-channel CLI's internal modules (driver,
channel recipes, EVENTS frozenset). Public re-exports kept minimal —
callers should import from ``cli._bind.channels`` (CHANNELS / EVENTS /
RECIPES) explicitly rather than relying on top-level re-exports.
"""

from __future__ import annotations
