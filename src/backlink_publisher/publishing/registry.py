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
- ``AuthExpiredError`` (subclass of ``DependencyError``) → propagate
  immediately; operator must re-bind (Plan 2026-05-20-016 Unit 0b).
- ``DependencyError`` (base class) from one adapter → fall through to
  the next
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
from typing import Any, Callable, Literal, TYPE_CHECKING

from backlink_publisher.config import Config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
    RegistryError,
)
from backlink_publisher.publishing._manifest_types import (
    _BIND_BACKEND_VALUES,
    _VISIBILITY_VALUES,
    BindDescriptor,
    Policy,
    UiMeta,
    Visibility,
)

if TYPE_CHECKING:
    # Importing AdapterResult at module top triggers loading of
    # .adapters/__init__.py, which imports back into this module
    # (dispatch/register/registered_platforms). When THIS module is the
    # first one loaded in the package, the cycle hits a partially
    # initialized state and ImportError fires. Type annotations are
    # PEP 563 lazy via __future__ import above; the only runtime use is
    # the AdapterResult(...) constructor inside dispatch() — that import
    # is local to the function body, which by call time is safe.
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


# platform → ordered list of adapter entries to try. Each entry is either
# a ``Publisher`` subclass (instantiated lazily at dispatch time, the
# legacy pattern used by all API-based adapters) or a ``Publisher``
# instance (the new pattern used by ``BrowserPublishDispatcher.for_channel(...)``
# — see Plan 2026-05-21-001 Unit 2 §D6). Populated by ``adapters/__init__.py``
# at import time (see ``_install``).
_REGISTRY: dict[str, list[type[Publisher] | Publisher]] = {}


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
    # devto: re-registered as nofollow chrome-publish channel in
    #   Plan 2026-05-21-001 Unit 4b (PR #157).
    # mastodon: re-registered as nofollow chrome-publish channel in
    #   Plan 2026-05-21-001 Unit 4c — Fediverse referral traffic +
    #   topical signal value despite the hardcoded nofollow attribute.
    # wordpresscom: un-rejected 2026-05-25 (channel expansion Phase 1) and
    #   re-registered. The 2026-05-26 dofollow audit corrected its claim
    #   from dofollow=True to dofollow="uncertain": this project's
    #   PR #108->#109 observed free-tier rel=nofollow, but a 2026-05 recheck
    #   found nofollow opt-in only — the conflict is resolved by an
    #   operator canary, not by re-rejecting. See _R["wordpresscom"].
}


_DofollowStatus = Literal[True, False, "uncertain"]

# Referral-value sub-grade for nofollow-signal platforms (Plan
# 2026-05-25-001 R1). Distinguishes nofollow links that still carry SEO
# value (high DA + referral traffic + entity signal — e.g. devto/notion/
# mastodon) from zero-value nofollow (anonymous paste sites with no DA
# and no referral). Required (via the gate below) only when ``dofollow``
# is not ``True`` — it is the load-bearing ship/reject decision input for
# nofollow platforms (R13).
_ReferralValue = Literal["high", "low"]


# Parallel-dict storage for the dofollow capability (Plan 2026-05-20-009
# U2). Kept alongside ``_REGISTRY`` rather than folded into its value
# shape so the existing single-key conftest snapshot pattern survives
# (``tests/conftest.py:206-221`` only saves/restores the ``"fake"`` key).
# ``_REFERRAL_VALUE_BY_PLATFORM`` (Plan 2026-05-25-001) is the second
# such capability dict; a third would justify migrating all of them to a
# ``RegistryEntry`` dataclass. IMPORTANT: every parallel dict here must
# be snapshot/restored together in the three registry-isolation fixtures
# (``tests/conftest.py`` ``fake_platform_registered``,
# ``tests/test_adapter_dofollow_gate.py`` ``_isolate_registry``,
# ``tests/test_registry_dofollow_kwargs.py``) or state leaks across tests.
_DOFOLLOW_BY_PLATFORM: dict[str, _DofollowStatus] = {}
_RATIONALE_BY_PLATFORM: dict[str, str] = {}
_REFERRAL_VALUE_BY_PLATFORM: dict[str, _ReferralValue] = {}

# Plan 2026-05-26-002 Unit 2 — auth-type classification for the WebUI binding
# surface. Drives per-channel binding-UI template selection (Unit 3).
#
# This is a STATIC classification constant, NOT a register()-mutated parallel
# dict — it is never written by register(), so it is exempt from the
# snapshot-isolation rule above. Two test-time guards keep it honest
# (``tests/test_auth_type_classification.py``):
#   1. coverage — every ``active_platforms()`` channel has an entry;
#   2. consistency — where a channel also declares ``bind[].backend``, the
#      auth_type agrees with it (``_BACKEND_AUTH_TYPE_COMPAT``), so this map
#      can never silently drift from the existing ``BindBackend`` descriptor.
# (Promoting this to a required ``register()`` kwarg is a viable follow-up
# hardening; the coverage guard already gives the same fail-loud guarantee.)
_AUTH_TYPE_VALUES: frozenset[str] = frozenset({
    "anon", "token", "token_fields", "paste_blob", "userpass", "oauth",
    "live_browser",
})
_AUTH_TYPE_BY_PLATFORM: dict[str, str] = {
    # ANON — no credentials (anonymous publish / auto-bootstrap)
    "telegraph": "anon", "txtfyi": "anon", "rentry": "anon",
    # TOKEN — single secret field
    "devto": "token", "writeas": "token",
    # TOKEN+FIELDS — secret + extra config field(s)
    "ghpages": "token_fields", "beehiiv": "token_fields", "ghost": "token_fields",
    "notion": "token_fields", "wordpresscom": "token_fields",
    "hashnode": "token_fields", "tumblr": "token_fields",
    # PASTE-BLOB — pasted {"cookies":[...]} JSON (cookie-export)
    "csdn": "paste_blob", "habr": "paste_blob", "jianshu": "paste_blob",
    "juejin": "paste_blob", "note": "paste_blob", "pikabu": "paste_blob",
    "segmentfault": "paste_blob", "substack": "paste_blob", "zhihu": "paste_blob",
    # USERPASS — username + password (stored server-side)
    "livejournal": "userpass", "cnblogs": "userpass",
    # OAUTH — redirect flow
    "blogger": "oauth",
    # LIVE-BROWSER — driven browser login (Chrome/Playwright)
    "velog": "live_browser", "medium": "live_browser", "mastodon": "live_browser",
}
# Which auth_types are consistent with a declared ``bind[].backend``. Used by
# the consistency test only (not at runtime). ``cookie`` backend currently
# means a live browser-cookie login (velog/medium/mastodon); the paste-blob
# cookie-export channels carry no bind descriptor.
_BACKEND_AUTH_TYPE_COMPAT: dict[str, frozenset[str]] = {
    "oauth": frozenset({"oauth"}),
    "token-paste": frozenset({"token", "token_fields", "userpass"}),
    "cookie": frozenset({"live_browser"}),
    "chrome": frozenset({"live_browser"}),
    "cdp": frozenset({"live_browser"}),
}

# Plan 2026-05-25-002 Unit 1 — manifest metadata parallel dicts.
#
# Following the established convention in this module (see comment above
# line 158-164): keep parallel dicts rather than migrating to a
# ``RegistryEntry`` dataclass, so existing conftest snapshot fixtures
# (``tests/conftest.py:191``) keep working with a minimal patch.
#
# IMPORTANT: every new dict added here MUST be added to the three
# registry-isolation snapshot fixtures (per the line-158 comment) or
# tests will leak state across runs. The four manifest dicts below
# are snapshotted as one block by ``fake_platform_registered``.
_UI_META_BY_PLATFORM: dict[str, UiMeta] = {}
_BIND_BY_PLATFORM: dict[str, tuple[BindDescriptor, ...]] = {}
_POLICY_BY_PLATFORM: dict[str, Policy] = {}
# ``visibility`` defaults to ``"active"`` when a register() call omits
# the kwarg, so the dict only stores explicit overrides. Lookups via
# ``visibility(name)`` fall back to ``"active"`` for unset platforms.
# This keeps the 8 pre-manifest register calls byte-identical in
# behaviour.
_VISIBILITY_BY_PLATFORM: dict[str, Visibility] = {}


def register(
    platform: str,
    *publishers: type[Publisher] | Publisher,
    dofollow: _DofollowStatus,
    rationale: str | None = None,
    referral_value: _ReferralValue | None = None,
    ui: UiMeta | None = None,
    bind: list[BindDescriptor] | tuple[BindDescriptor, ...] | None = None,
    policy: Policy | None = None,
    visibility: Visibility = "active",
) -> None:
    """Register the fallback chain for one platform. Last call wins.

    Order matters: the first registered entry is tried first. Each
    ``publishers`` entry may be:

    - A ``Publisher`` subclass — the legacy pattern. ``dispatch()`` will
      instantiate it lazily per call (e.g. ``BloggerAPIAdapter``,
      ``MediumAPIAdapter``).
    - A ``Publisher`` instance — the Plan 2026-05-21-001 Unit 2 pattern
      for ``BrowserPublishDispatcher.for_channel("hashnode")`` and
      siblings, where ctor-time recipe binding is needed (D6).

    The mixing is supported per-platform too (a class entry can be followed
    by an instance entry in the same chain).

    ``dofollow`` is a required keyword argument (Literal ``True`` /
    ``False`` / ``"uncertain"``) declaring whether the platform produces
    a dofollow backlink. Missing ``dofollow=`` raises ``TypeError`` at
    import time — the structural gate that replaces the institutional
    "grep _DOFOLLOW_BY_CHANNEL before shipping" rule (memory feedback
    ``feedback_grep_dofollow_map_before_shipping_adapter``). ``rationale``
    is required when ``dofollow`` is anything other than ``True`` (R3 /
    Plan 2026-05-20-009).

    ``referral_value`` (``"high"`` / ``"low"``) is required when
    ``dofollow`` is anything other than ``True`` (Plan 2026-05-25-001) —
    the load-bearing ship/reject decision input for nofollow platforms
    (R13). For ``dofollow=True`` it is optional (defaults to ``None`` =
    "not classified").

    Raises:
        RegistryError: when ``platform`` is listed in
            ``_REJECTED_PLATFORMS`` (un-rejection path: delete the
            entry in the same PR as the new ``register()`` call), OR
            when ``dofollow`` is not ``True`` and the rationale
            is missing / shorter than 80 chars stripped, OR when
            ``dofollow`` is not ``True`` and ``referral_value`` is
            ``None`` (the silent-gap gate — a nofollow platform must
            declare high/low referral value before it can ship).
    """
    if platform in _REJECTED_PLATFORMS:
        prior = _REJECTED_PLATFORMS[platform]
        raise RegistryError(
            f"previously rejected: {platform!r}; prior rationale: {prior!r}. "
            f"To retry, delete this entry from `_REJECTED_PLATFORMS` in the "
            f"same PR as the new `register()` call."
        )
    if dofollow in (False, "uncertain"):
        if rationale is None or len(rationale.strip()) < 80:
            actual = 0 if rationale is None else len(rationale.strip())
            raise RegistryError(
                f"`register({platform!r}, ..., dofollow={dofollow!r})` "
                f"requires `rationale=` with len(rationale.strip()) >= 80 "
                f"(got {actual}). Length-only gate — content is reviewer "
                f"concern; see `monolith_budget.toml` for the precedent."
            )
        if referral_value is None:
            raise RegistryError(
                f"`register({platform!r}, ..., dofollow={dofollow!r})` "
                f"requires `referral_value=` ('high' or 'low') — it is the "
                f"ship/reject decision input for nofollow platforms (Plan "
                f"2026-05-25-001 R13). 'high' = retains DA/referral/entity "
                f"value despite nofollow; 'low' = zero value (reject "
                f"candidate). Leaving it unset is the silent gap this gate "
                f"closes."
            )
    # Runtime-validate the value against _ReferralValue. The Literal type is
    # static-only; a typo like referral_value="HIGH" would otherwise pass the
    # None-check, store an out-of-band value, and silently fall into the
    # report's "unclassified" bucket (review finding — project-standards).
    if referral_value is not None and referral_value not in ("high", "low"):
        raise RegistryError(
            f"`register({platform!r}, ..., referral_value={referral_value!r})` "
            f"— referral_value must be 'high' or 'low' (got {referral_value!r})."
        )
    # Plan 2026-05-25-002 Unit 1 — manifest kwarg validation. ``Literal``
    # is static-only; same precedent as referral_value runtime check above.
    if visibility not in _VISIBILITY_VALUES:
        raise RegistryError(
            f"`register({platform!r}, ..., visibility={visibility!r})` — "
            f"visibility must be one of {sorted(_VISIBILITY_VALUES)} "
            f"(got {visibility!r})."
        )
    if bind is not None:
        bind_tuple = tuple(bind)
        for idx, descriptor in enumerate(bind_tuple):
            if not isinstance(descriptor, BindDescriptor):
                raise RegistryError(
                    f"`register({platform!r}, ..., bind=[...])` — entry "
                    f"#{idx} is {type(descriptor).__name__}, expected "
                    f"BindDescriptor. Use `BindDescriptor(backend=..., ...)`."
                )
            if descriptor.backend not in _BIND_BACKEND_VALUES:
                raise RegistryError(
                    f"`register({platform!r}, ..., bind=[...])` — entry "
                    f"#{idx} has backend={descriptor.backend!r}, must be "
                    f"one of {sorted(_BIND_BACKEND_VALUES)}."
                )
    else:
        bind_tuple = ()
    if ui is not None and not isinstance(ui, UiMeta):
        raise RegistryError(
            f"`register({platform!r}, ..., ui=...)` — expected UiMeta, "
            f"got {type(ui).__name__}."
        )
    if policy is not None and not isinstance(policy, Policy):
        raise RegistryError(
            f"`register({platform!r}, ..., policy=...)` — expected Policy, "
            f"got {type(policy).__name__}."
        )
    _REGISTRY[platform] = list(publishers)
    _DOFOLLOW_BY_PLATFORM[platform] = dofollow
    if referral_value is not None:
        _REFERRAL_VALUE_BY_PLATFORM[platform] = referral_value
    else:
        _REFERRAL_VALUE_BY_PLATFORM.pop(platform, None)
    if rationale is not None:
        _RATIONALE_BY_PLATFORM[platform] = rationale
    else:
        _RATIONALE_BY_PLATFORM.pop(platform, None)
    # Manifest dicts: store only when explicit; pop-on-None mirrors the
    # rationale/referral_value pattern so re-register() without kwargs
    # clears prior values rather than carrying stale state.
    if ui is not None:
        _UI_META_BY_PLATFORM[platform] = ui
    else:
        _UI_META_BY_PLATFORM.pop(platform, None)
    if bind_tuple:
        _BIND_BY_PLATFORM[platform] = bind_tuple
    else:
        _BIND_BY_PLATFORM.pop(platform, None)
    if policy is not None:
        _POLICY_BY_PLATFORM[platform] = policy
    else:
        _POLICY_BY_PLATFORM.pop(platform, None)
    # ``"active"`` is the implicit default; don't store it so the dict
    # only carries non-default overrides (smaller snapshots, clearer diffs).
    if visibility != "active":
        _VISIBILITY_BY_PLATFORM[platform] = visibility
    else:
        _VISIBILITY_BY_PLATFORM.pop(platform, None)


def registered_platforms() -> list[str]:
    """Return the list of platforms with at least one adapter registered."""
    return sorted(_REGISTRY)


def dofollow_status(name: str) -> _DofollowStatus | None:
    """Return the declared dofollow status for ``name``, or ``None`` if
    the platform is not registered with explicit dofollow declaration.

    Plan 2026-05-20-009 R5.
    """
    return _DOFOLLOW_BY_PLATFORM.get(name)


def auth_type(name: str) -> str | None:
    """Return the binding auth-type for ``name`` (one of ``_AUTH_TYPE_VALUES``),
    or ``None`` if the platform has no classification.

    Drives per-channel binding-UI template selection in the WebUI settings
    surface (Plan 2026-05-26-002 Unit 2/3). Authoritative for template
    selection; kept consistent with any declared ``bind[].backend`` by a
    test-time guard (see ``_AUTH_TYPE_BY_PLATFORM``).
    """
    return _AUTH_TYPE_BY_PLATFORM.get(name)


def referral_value(name: str) -> _ReferralValue | None:
    """Return the declared referral-value sub-grade (``"high"`` /
    ``"low"``) for ``name``, or ``None`` if not declared.

    ``None`` for dofollow platforms (sub-grade not applicable) and for
    unregistered platforms. Always non-``None`` for nofollow platforms
    (enforced by the ``register()`` gate). Plan 2026-05-25-001 R1.
    """
    return _REFERRAL_VALUE_BY_PLATFORM.get(name)


def dofollow_rationale(name: str) -> str | None:
    """Return the registration rationale string for ``name``, or ``None``
    if no rationale was supplied (the common case for ``dofollow=True``
    registrations; mandatory for ``False`` / ``"uncertain"`` per R3).

    Plan 2026-05-20-009 R5.
    """
    return _RATIONALE_BY_PLATFORM.get(name)


# ---------------------------------------------------------------------------
# Manifest helpers — Plan 2026-05-25-002 Unit 1
# ---------------------------------------------------------------------------
#
# These six helpers are the reverse-lookup API the downstream layers
# (binding_status.py, webui_app/__init__.py inject_platforms,
# webui_app/helpers/contexts.py, config/_toml_utils.py, templates) will
# call instead of carrying their own hardcoded channel lists.
#
# All helpers are pure-read; thread-safe; cheap (dict.get / list-comp on
# ≤ 20 platforms). No caching needed at this layer — if downstream
# call-frequency demands it (per [[flask-g-cache-pattern]]), the cache
# belongs at the caller, not here.


def ui_meta(name: str) -> UiMeta | None:
    """Return the declared ``UiMeta`` for ``name``, or ``None``.

    ``None`` for platforms registered without ``ui=`` (legacy platforms
    pre-manifest). Callers wanting a fallback should use
    ``ui_meta(name) or UiMeta(display_name=name, domain="", category="")``.
    """
    return _UI_META_BY_PLATFORM.get(name)


def bind_descriptors(name: str) -> tuple[BindDescriptor, ...]:
    """Return the declared bind backends for ``name``, in display order.

    Returns ``()`` for platforms registered without ``bind=`` — the
    legacy state where bind wiring is hardcoded across webui_app /
    helpers / templates rather than declared. Callers iterating to
    auto-build UI cards (Plan Unit 4) treat ``()`` as "fall back to
    legacy per-channel wiring".
    """
    return _BIND_BY_PLATFORM.get(name, ())


def policy(name: str) -> Policy | None:
    """Return the declared ``Policy`` for ``name``, or ``None``.

    ``None`` for legacy registrations. Downstream throttle / retry /
    language machinery keeps its existing defaults when ``policy()`` is
    ``None`` — the manifest is additive metadata, not a behaviour
    rewrite (Plan Scope Boundaries: "不改 publish 業務邏輯").
    """
    return _POLICY_BY_PLATFORM.get(name)


def visibility(name: str) -> Visibility:
    """Return the visibility state for ``name``.

    Always returns a valid ``Visibility`` literal. Unregistered or
    default-active platforms return ``"active"`` — this is the
    load-bearing default that lets Unit 2 swap ``HIDDEN_FROM_UI``
    frozenset to ``visibility(name) in {"hidden","retired"}`` without
    needing a per-platform opt-in.
    """
    return _VISIBILITY_BY_PLATFORM.get(name, "active")


def active_platforms() -> list[str]:
    """Return registered platforms with ``visibility == "active"``.

    Sorted (matches ``registered_platforms`` ordering). Used by Unit 4
    WebUI wiring to populate filter chips and publish-select that
    should *not* show hidden / retired / experimental channels.

    For the variant that includes experimental (e.g. the
    ``--include-experimental`` CLI path or WebUI advanced mode), call
    sites should filter ``registered_platforms()`` themselves with
    ``visibility(name) != "retired"`` — the goal here is the default
    user-facing list, not every possible filter.
    """
    return sorted(
        name for name in _REGISTRY
        if visibility(name) == "active"
    )


def bound_platforms(
    config: Config,
    is_bound: Callable[[Config, str], bool],
) -> list[str]:
    """Return active platforms that are currently bound for ``config``.

    ``is_bound`` is dependency-injected: this module lives in the
    ``publishing`` layer and must not import from ``webui_app``
    (binding-status helpers live up there). Callers in WebUI inject
    ``lambda cfg, name: webui_app.binding_status.get_channel_status(cfg, name).get("bound", False)``
    or the equivalent.

    Filtering is composed: ``active_platforms()`` first (drops hidden /
    retired / experimental), then ``is_bound(config, name)`` for the
    remainder. This guarantees a retired platform never appears in
    publish UI even if its credentials are still on disk.
    """
    return [name for name in active_platforms() if is_bound(config, name)]


def legacy_platforms() -> list[str]:
    """Return registered platforms with NO manifest metadata.

    A platform is "legacy" iff none of ``ui_meta`` / ``bind_descriptors``
    / ``policy`` was supplied at ``register()`` time. ``visibility`` is
    intentionally excluded — leaving it at the default ``"active"`` is
    the expected state for pre-manifest channels.

    Plan Unit 5 surfaces this as a migration progress board (printed to
    contract-test stdout, not failed). Becomes a CI fail gate once all
    8 existing platforms are migrated (deferred to Phase 3 of the plan).
    """
    return sorted(
        name for name in _REGISTRY
        if name not in _UI_META_BY_PLATFORM
        and name not in _BIND_BY_PLATFORM
        and name not in _POLICY_BY_PLATFORM
    )


def dispatch(
    payload: dict[str, Any],
    mode: str,
    config: Config,
    dry_run: bool = False,
    *,
    banner_emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> AdapterResult:
    """Walk the registered fallback chain for ``payload["platform"]``.

    Error semantics: dry-run returns a sentinel result;
    ``AuthExpiredError`` (subclass of ``DependencyError``) propagates
    immediately so operator UX can prompt re-bind (Plan
    2026-05-20-016 Unit 0b); plain ``DependencyError`` from one
    adapter falls through to the next; ``ExternalServiceError``
    propagates; unknown platform raises ``ExternalServiceError``.

    Banner embed (Plan 2026-05-20-004 Unit 1): when ``banner_emit`` is
    supplied AND the payload carries a non-degraded ``banner`` field
    (``banner["path"]`` not None), each available adapter in the chain
    gets a chance to embed via ``adapter.embed_banner`` before its
    ``publish()`` runs.  See ``banner_dispatcher.apply`` for the
    branch semantics.  ``banner_emit`` is the event sink (kind,
    payload) and defaults to ``None`` which suppresses banner work
    entirely (back-compat for callers that don't set up banners).
    """
    from .adapters.base import AdapterResult  # local: breaks module-level circular

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
    for entry in chain:
        # Entry may be a Publisher subclass (legacy) or instance
        # (BrowserPublishDispatcher.for_channel — Plan 2026-05-21-001 U2).
        is_class = isinstance(entry, type)
        publisher_cls = entry if is_class else type(entry)
        if not publisher_cls.available(config):
            continue
        try:
            adapter = entry() if is_class else entry
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
        except AuthExpiredError:
            # Plan 2026-05-20-016 Unit 0b: credentials were valid enough to
            # reach the adapter but have expired — operator must re-bind.
            # Falling through would silently try the next chain entry and
            # hide the expiry; the correct semantics is to propagate so
            # the webui can surface "请重新绑定 <channel>" UX.
            # Order matters: AuthExpiredError IS-A DependencyError (per
            # _util/errors.py), so this except MUST precede the
            # DependencyError catch below — Python catches the first
            # matching except clause.
            raise
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
