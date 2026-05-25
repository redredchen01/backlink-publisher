"""Backlink Equity Ledger — read-only per-target-URL scorecard.

Composes already-recorded data from three stores (events.db, the WebUI
history store, and the anchor-profile store) into one per-target view of
"what does this target page have working for it right now": live vs total
links, dofollow breakdown, exact-match anchor share, platform spread, and
liveness freshness. Pure read-side aggregation — no publishing, no fetching.

Plan: docs/plans/2026-05-25-004-feat-backlink-equity-ledger-plan.md
"""

from .aggregate import build_ledger
from .model import LedgerRow

__all__ = ["LedgerRow", "build_ledger"]
