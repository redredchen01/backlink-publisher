"""Browser-publish recipes registry — Plan 2026-05-21-001 Unit 1/2.

``RECIPES`` is keyed by channel slug; values are ``BrowserPublishRecipe``
instances. Unit 3+ adds concrete recipes (hashnode, velog, …) via
``RECIPES["channel"] = …`` in their own module's import-time block.
"""

from __future__ import annotations

from ..chrome_session import BrowserPublishRecipe


RECIPES: dict[str, BrowserPublishRecipe] = {}


__all__ = ["RECIPES"]
