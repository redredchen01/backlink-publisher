"""Publisher ABC + table-driven dispatcher — Plan 2026-05-18-001 Unit 7,
extended by Plan 2026-05-18-009 R9 (CLI/schema decoupling).

Replaces the ``if plat == "blogger" / elif "medium"`` chain in
``adapters/__init__.py:publish()`` with a registry the dispatch logic
walks once per call. Adding a new platform means:

  1. Implement ``Publisher.publish(payload, mode, config) -> AdapterResult``
  2. Call ``register("<platform>", NewAdapterCls)``

No changes to the dispatcher, the CLI argparse layer, or
``schema.supported_platforms`` — all of those read
``registered_platforms()`` dynamically post-R9. See
``AGENTS.md → Adding a new publisher adapter`` for the contributor
walkthrough that cites ``BloggerAPIAdapter`` at each step.

Fallback semantics (preserved from the legacy chain):

- The registry stores an ordered list of adapter classes per platform.
- ``dispatch`` walks the chain in order, instantiating each adapter and
  calling ``.publish(...)``.
- ``DependencyError`` from one adapter → fall through to the next
  (the legacy "no Medium token → try browser" path).
- ``ExternalServiceError`` from any adapter → propagate up immediately
  (preserves the legacy "401 / 429 / network failure does NOT fall
  through" semantics).
- An adapter can declare itself unavailable for a given environment by
  overriding ``Publisher.available(cls, config)`` — used by
  ``MediumBraveAdapter`` to gate itself to macOS.

Adapter-declared throttle metadata (post-R9c): adapters set
``AdapterResult.post_publish_delay_seconds`` to declare a required
post-publish wait (Medium adapters set ``30``). The CLI's verify-poll
window and inter-row throttle bookkeeping key off this field rather than
matching adapter strings against a hardcoded ``_MEDIUM_ADAPTERS`` set.

This is the minimum dispatcher generalisation; per Plan D5 we do not
rewrite adapter internals, and per Plan D8 the only method on the ABC
is ``publish`` (``verify_adapter_setup`` stays a module function).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from .adapters.base import AdapterResult


class Publisher(ABC):
    """Abstract base for a single-platform publisher.

    Subclasses must implement ``publish``. They may optionally override
    ``available`` to declare environment prerequisites (e.g. macOS-only).
    """

    @abstractmethod
    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        """Publish the payload. Return an ``AdapterResult`` on success.

        Raise:
        - ``DependencyError`` if a prerequisite is missing (no token, no
          browser, no AppleScript host) — dispatcher will try the next
          adapter in the chain.
        - ``ExternalServiceError`` if the remote service returned an
          error (401, 429, 5xx, network failure) — dispatcher will NOT
          fall through; the error propagates immediately.
        """

    @classmethod
    def available(cls, config: Config) -> bool:
        """Return False to skip this adapter in the dispatch chain.

        Default ``True`` — most adapters do not need environment gating.
        Use cases: macOS-only adapters (``MediumBraveAdapter``), feature
        flags, license checks.
        """
        return True


# platform → ordered list of adapter classes to try.
# Populated by ``adapters/__init__.py`` at import time (see ``_install``).
_REGISTRY: dict[str, list[type[Publisher]]] = {}


# Negative-knowledge registry: platforms empirically verified as nofollow
# (or otherwise unsuitable as dofollow backlink sources) by prior PR
# attempts. ``register("devto", ...)`` raises ``RegistryError`` at import
# time — un-rejection path is to delete the entry from this map in the
# same PR as the re-``register()`` call (see Plan 2026-05-20-009 R12).
#
# Each value is a free-form rationale string ≥80 chars stripped, mirroring
# the ``monolith_budget.toml`` rationale convention. The value-shape stays
# a plain ``dict[str, str]`` rather than a dataclass because only the
# rationale string has a programmatic consumer (the failure message);
# ``rejected_at`` is recoverable from ``git log`` and ``dofollow=False``
# is implicit (this is a rejection map).
_REJECTED_PLATFORMS: dict[str, str] = {
    "devto": (
        "Dev.to applies rel=\"nofollow ugc\" to outbound links since ~2022 "
        "per platform policy; every external <a> is decorated server-side "
        "regardless of account tier or post format — backlinks here carry "
        "zero PageRank transfer. Reverted in PR #109 after PR #108 ship."
    ),
    "mastodon": (
        "Mastodon hardcodes rel=\"nofollow noopener noreferrer\" on outbound "
        "links across all instances; the attribute is federation-default and "
        "cannot be disabled per-post or per-account. Outbound links provide "
        "no SEO authority transfer. Reverted in PR #109."
    ),
    "wordpresscom": (
        "WordPress.com free tier applies rel=\"nofollow\" to outbound links; "
        "paid Business/Commerce tiers enable dofollow but require a paid "
        "subscription not justified at solo-operator scale. Free-tier ship "
        "would emit nofollow-only backlinks. Reverted in PR #109."
    ),
}


def register(platform: str, *publishers: type[Publisher]) -> None:
    """Register the fallback chain for one platform. Last call wins.

    Order matters: the first registered class is tried first.
    """
    _REGISTRY[platform] = list(publishers)


def registered_platforms() -> list[str]:
    """Return the list of platforms with at least one adapter registered."""
    return sorted(_REGISTRY)


def dispatch(
    payload: dict[str, Any],
    mode: str,
    config: Config,
    dry_run: bool = False,
    *,
    banner_emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> AdapterResult:
    """Walk the registered fallback chain for ``payload["platform"]``.

    Mirrors the legacy ``publish()`` behaviour byte-for-byte: dry-run
    sentinel result, ``DependencyError`` falls through, ``ExternalServiceError``
    propagates, unknown platform raises ``ExternalServiceError``.

    Banner embed (Plan 2026-05-20-004 Unit 1): when ``banner_emit`` is
    supplied AND the payload carries a non-degraded ``banner`` field
    (``banner["path"]`` not None), each available adapter in the chain
    gets a chance to embed via ``adapter.embed_banner`` before its
    ``publish()`` runs.  See ``banner_dispatcher.apply`` for the
    branch semantics.  ``banner_emit`` is the event sink (kind,
    payload) and defaults to ``None`` which suppresses banner work
    entirely (back-compat for callers that don't set up banners).
    """
    plat = payload.get("platform", "")

    if dry_run:
        return AdapterResult(
            status="draft",
            adapter=f"{plat}-api",
            platform=plat,
            _dry_run=True,
            _command=f"publish to {plat} --mode {mode} (dry-run)",
        )

    chain = _REGISTRY.get(plat)
    if not chain:
        raise ExternalServiceError(f"unsupported platform: {plat}")

    banner_dict = payload.get("banner") if banner_emit is not None else None
    do_banner = banner_dict is not None and banner_dict.get("path") is not None
    strict = bool(do_banner and config.image_gen and config.image_gen.strict)

    last_dep_error: DependencyError | None = None
    for cls in chain:
        if not cls.available(config):
            continue
        try:
            adapter = cls()
            if do_banner:
                # Lazy import avoids a top-level cycle (banner_dispatcher
                # lives in the same publishing package and is leaf-level,
                # but importing it during registry init is unnecessary
                # for the >99% of dispatch calls that have no banner).
                from . import banner_dispatcher

                new_body = banner_dispatcher.apply(
                    adapter,
                    banner=banner_dict,
                    body=payload.get("content_markdown", ""),
                    platform=plat,
                    strict=strict,
                    emit=banner_emit,  # type: ignore[arg-type]  # do_banner gates non-None
                )
                if new_body != payload.get("content_markdown"):
                    payload = {**payload, "content_markdown": new_body}
            return adapter.publish(payload, mode, config)
        except DependencyError as e:
            # Adapter declared itself missing a prerequisite → try next.
            last_dep_error = e
            continue
        # ExternalServiceError propagates without catch (legacy semantics).

    if last_dep_error is not None:
        raise last_dep_error
    raise DependencyError(
        f"No available adapter for platform {plat!r} — every entry in the "
        f"chain returned available()=False."
    )
