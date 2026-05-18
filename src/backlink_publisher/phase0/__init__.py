"""Phase 0 ship-seal subpackage.

Shared validation logic for the Telegraph Phase 0 ship SHA seal mechanism.
Consumed by both `backlink_publisher.cli.phase0_seal` (operator-side CLI)
and `scripts/telegraph_spike/verify_seal.py` (server-side sidecar script).

See docs/plans/2026-05-18-009-feat-telegraph-phase0-ship-seal-plan.md (Unit 2).
"""

from __future__ import annotations
