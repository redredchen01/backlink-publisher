"""Adapter Contract Canary — per-platform health + config (Plan 2026-05-27-001 Unit 1).

Persists per-platform canary health (the minimal advisory-debounce fields)
and reads per-platform ``[canary.<platform>]`` post config. See
``store.py`` for the import-path decision.
"""

from __future__ import annotations
