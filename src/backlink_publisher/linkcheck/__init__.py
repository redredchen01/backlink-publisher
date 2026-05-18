"""linkcheck subpackage — Plan 2026-05-18-001 Unit 6.

Legacy ``from backlink_publisher.linkcheck import check_url`` (the old
``linkcheck.py`` API) keeps working via the wildcard re-export below.
New code should import from ``backlink_publisher.linkcheck.http`` etc.
"""

from .http import *  # noqa: F401,F403  — preserves legacy public import path

# Re-export private names that tests monkeypatch via the legacy module
# attribute path (``patch("backlink_publisher.linkcheck._check_url_once",
# ...)``). ``from .http import *`` skips underscored names by convention,
# so we list them explicitly.
from .http import _check_url_once  # noqa: F401  — patchable
