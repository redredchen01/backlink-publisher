"""backlink-publisher root package.

Plan 2026-05-18-001 Unit 6 reorganised 16 flat modules into five domain
subpackages. The legacy import paths (``from backlink_publisher.anchor_lang
import …``, ``from backlink_publisher.adapters.base import …`` etc.) still
work via a ``sys.meta_path`` finder that lazily redirects them to the
new locations.

New code should import from the new paths:
  - ``backlink_publisher.anchor.*``
  - ``backlink_publisher.content.*``
  - ``backlink_publisher.linkcheck.*``
  - ``backlink_publisher.publishing.adapters.*``
  - ``backlink_publisher._util.*``

Tests, CLI entry points, and the WebUI ``webui_app/`` package continue to
use the legacy flat paths during the deprecation period; the finder
below makes that transparent.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from typing import Sequence


# Legacy flat name → new dotted module path *under* ``backlink_publisher``.
# Used by ``_LegacyPathFinder`` to rewrite imports like
# ``backlink_publisher.anchor_lang`` → ``backlink_publisher.anchor.lang``
# and ``backlink_publisher.adapters.base`` → ``backlink_publisher.publishing.adapters.base``.
_REEXPORT_MAP: dict[str, str] = {
    # anchor subpackage
    "anchor_lang": "anchor.lang",
    "anchor_metrics": "anchor.metrics",
    "anchor_profile": "anchor.profile",
    "anchor_resolver": "anchor.resolver",
    "anchor_scheduler": "anchor.scheduler",
    # content subpackage
    "content_fetch": "content.fetch",
    "work_scraper": "content.scraper",
    "work_themed_generator": "content.themed_gen",
    # linkcheck subpackage
    # NOTE: 'linkcheck' (without tail) is NOT redirected — the linkcheck/
    # package directory exists and Python's default finder resolves it.
    # ``linkcheck/__init__.py`` does ``from .http import *`` so that
    # ``from backlink_publisher.linkcheck import check_url`` (the legacy
    # form) keeps working. Same logic for anchor/content/_util below where
    # the package name collides with a flat module that was redirected.
    "language_check": "linkcheck.language",
    "verify_publish": "linkcheck.verify",
    # _util subpackage
    "errors": "_util.errors",
    "io_utils": "_util.io",
    "jsonl": "_util.jsonl",
    "logger": "_util.logger",
    "markdown_utils": "_util.markdown",
    "url_utils": "_util.url",
    # publishing subpackage (adapters/ moved wholesale)
    "adapters": "publishing.adapters",
}


class _LegacyPathFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Redirect legacy ``backlink_publisher.<flat>[.sub…]`` imports.

    PEP 562 ``__getattr__`` would handle attribute access but does not
    intercept ``from backlink_publisher.X.Y import Z`` style dotted imports
    — those go through Python's import machinery (finders + loaders), not
    attribute lookup. Tests use both styles, so we install a real finder.

    The finder is lazy: it does not import any subpackage at install time.
    A module is loaded only when something actually imports it.
    """

    _PREFIX = "backlink_publisher."

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: object | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if not fullname.startswith(self._PREFIX):
            return None
        rest = fullname[len(self._PREFIX):]
        head, _sep, _tail = rest.partition(".")
        if head not in _REEXPORT_MAP:
            return None
        return importlib.machinery.ModuleSpec(fullname, self, origin=fullname)

    def create_module(self, spec: importlib.machinery.ModuleSpec):
        # Resolve the real target and reuse its module object so identity
        # checks pass (e.g. ``backlink_publisher.adapters is
        # backlink_publisher.publishing.adapters``).
        fullname = spec.name
        rest = fullname[len(self._PREFIX):]
        head, _sep, tail = rest.partition(".")
        new_head = _REEXPORT_MAP[head]
        new_full = f"{self._PREFIX}{new_head}"
        if tail:
            new_full = f"{new_full}.{tail}"
        module = importlib.import_module(new_full)
        sys.modules[fullname] = module
        return module

    def exec_module(self, module) -> None:
        # The real module was already executed by ``importlib.import_module``
        # inside ``create_module``. Nothing more to do.
        return None


# Install the finder once, ahead of the default finders so it gets first
# crack at legacy names.
_finder = _LegacyPathFinder()
if not any(isinstance(f, _LegacyPathFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _finder)
