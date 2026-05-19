"""Channel recipes — Plan 2026-05-19-001 Unit 2.

A *recipe* is a value (frozen dataclass) declaring three things per channel:

  - ``login_url``: HTTPS URL the headed browser opens
  - ``bound_predicate``: callable(page) that blocks until login is detected
  - ``cookie_host_filter``: pure host-match predicate used by the driver to
    decide which cookies/origins from storage_state should be persisted

Recipes are values, **not** subclasses. Choreography (form-filling, multi-step
navigation) belongs in ``bound_predicate``'s body, run inside the headed
session driven by the operator. The driver is the only writer of disk state.

Adding a fourth channel means: (1) extend ``CHANNELS`` in
``cli._bind.channels``; (2) add the recipe instance to this module's
``RECIPES`` dict; (3) ship a ``bound_predicate`` that uses the standard
Playwright wait primitives. No driver changes required.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol


class _PageLike(Protocol):
    """Subset of the Playwright Page API used by ``bound_predicate``.

    Recipes type-hint their predicate against this Protocol so the recipe
    module does not import Playwright at module-import time (Playwright is
    lazy-imported inside ``driver.run_bind`` only).
    """


BoundPredicate = Callable[[Any], None]
HostFilter = Callable[[str], bool]
# Plan 2026-05-19-005 Unit 1 (Medium cookies-only hardcut): optional hook the
# driver invokes after ``_persist_storage_state`` succeeds, BEFORE
# ``mark_bound``. Receives the config dir and the just-written storage_state
# path; returns the path the driver should record in ``channel_status_store``
# (or ``None`` to keep the original storage_state path).
#
# Used by the medium recipe to derive cookies-only ``medium-cookies.json`` +
# ``medium-meta.json`` from the just-written storage_state, then unlink the
# now-redundant storage_state.json and return the cookies.json path as the
# new canonical bound credential. Other recipes leave this ``None``.
PostPersistHook = Callable[[Path, Path], Optional[Path]]


@dataclass(frozen=True)
class ChannelRecipe:
    """A single browser-binding recipe.

    Immutable — instances are module-level singletons in ``RECIPES`` and must
    not be mutated at runtime (e.g. for tests overriding behavior, build a
    new ChannelRecipe value instead).
    """

    login_url: str
    bound_predicate: BoundPredicate
    cookie_host_filter: HostFilter
    post_persist: PostPersistHook | None = None


# ───────── public registry — keys must == CHANNELS exactly ─────────


from .velog import RECIPE as _VELOG_RECIPE
from .medium import RECIPE as _MEDIUM_RECIPE
from .blogger import RECIPE as _BLOGGER_RECIPE


RECIPES: dict[str, ChannelRecipe] = {
    "velog": _VELOG_RECIPE,
    "medium": _MEDIUM_RECIPE,
    "blogger": _BLOGGER_RECIPE,
}


__all__ = ["ChannelRecipe", "BoundPredicate", "HostFilter", "PostPersistHook", "RECIPES"]
