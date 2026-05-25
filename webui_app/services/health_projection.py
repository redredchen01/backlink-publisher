"""WebUI adapter for the read-time projection backstop (Plan 2026-05-25-006 / U1).

Thin indirection over ``events.reconcile.project_on_read`` so routes and tests
can drive/mock the projection without reaching into the events package —
mirroring the other ``webui_app/services`` adapters.
"""

from __future__ import annotations

from backlink_publisher.events.reconcile import ReadProjectionResult


def project_on_read() -> ReadProjectionResult:
    """Run the load-time projection backstop. Never raises (see reconcile)."""
    # Lazy import so ``unittest.mock.patch`` against
    # ``backlink_publisher.events.reconcile.project_on_read`` still takes effect
    # after this module has been imported.
    from backlink_publisher.events import reconcile

    return reconcile.project_on_read()
