"""backlink-publisher root package.

Plan 2026-05-20-006 removed the legacy import bridge that used to
redirect flat module names (e.g. ``backlink_publisher.errors``,
``backlink_publisher.adapters.*``) to the refactored locations. All
callers now import from the canonical paths directly:

  - ``backlink_publisher.anchor.*``
  - ``backlink_publisher.content.*``
  - ``backlink_publisher.linkcheck.*``
  - ``backlink_publisher._util.*``
  - ``backlink_publisher.publishing.adapters.*``
"""
from __future__ import annotations
