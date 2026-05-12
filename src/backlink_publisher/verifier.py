"""Real-publish verification for backlink-publisher.

After each adapter returns `status="published"` with a `published_url`, the
publish-backlinks dispatcher calls `verify_published()` to independently assert
the article is actually live and contains the article's title + the expected
target-link hrefs. The verification status surfaces as three additive JSONL
fields (`verified`, `verified_at`, `verification_error`) and feeds the final
exit code.

This module is the public surface — channel-specific implementations land in
subsequent units. Unit 1 ships the dispatch skeleton with stubs that return
"not_implemented" outcomes.

Plan: docs/plans/2026-05-12-005-feat-real-publish-verification-plan.md
Brainstorm: docs/brainstorms/2026-05-12-real-publish-verification-requirements.md
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .adapters.base import AdapterResult


# Per-channel retry budgets (origin R9 / R10). Hard-coded inline; do not build
# a configurable abstraction until a third adapter with distinct timing lands.
# Each element is the wait (seconds) before that attempt; first is always 0.
# len() = total attempts.
_HTML_RETRY_WAITS_S: tuple[int, ...] = (0, 5, 10, 15)   # 4 attempts, ≤30s wall-clock
_API_RETRY_WAITS_S: tuple[int, ...] = (0,)              # single attempt

# Error-string template for an exhausted retry budget. Pinned as a module
# constant so the dispatcher's lag-counting predicate (Unit 6) imports it
# rather than matching a literal — prevents cross-unit string drift.
_ERR_TRANSIENT_EXHAUSTED = "transient_exhausted: {n}/{n} attempts"

# Verifier_internal_error prefix. Rows whose verification_error starts with
# this prefix roll up into verified_false for exit-code purposes (P0
# resolution: prevents verifier bugs silently masking real failures).
_ERR_INTERNAL_PREFIX = "verifier_internal_error: "

# Response body cap for the HTML channel (bytes). Over-cap → verified=null,
# verification_error="body_too_large".
_MAX_BODY_BYTES = 2_000_000

# Wall-clock budget per HTML-channel fetch attempt (seconds), enforced inside
# the chunked-read loop. Prevents slow-drip DoS where a malicious server
# returns bytes slower than urllib's per-socket timeout.
_MAX_FETCH_WALL_CLOCK_S = 15

# Maximum redirect hops the HTML channel will follow.
_MAX_REDIRECT_HOPS = 5

# Centralized per-adapter verification metadata. With only two platforms in
# scope, scattering declarations across adapter modules is premature
# abstraction (scope-guardian trim). When a third platform lands, the cost
# of migrating to per-adapter declarations is mechanical.
_ADAPTER_METADATA: dict[str, dict[str, Any]] = {
    "blogger-api": {
        "channel": "api",
        "allowed_hosts": ("*.blogspot.com", "blogger.com"),
        "allowed_path_patterns": (r"^/\d{4}/\d{2}/.+\.html$",),
        "args": lambda row, result: {
            "blog_id": result._provider_meta["blog_id"],
            "post_id": result._provider_meta["post_id"],
        },
    },
    "medium-api": {
        "channel": "html",
        "allowed_hosts": ("medium.com", "*.medium.com"),
        "allowed_path_patterns": (r"^/@[^/]+/[\w\-]+", r"^/p/[\w]+"),
        "args": lambda row, result: {"url": result.published_url},
    },
    "medium-browser": {
        "channel": "html",
        "allowed_hosts": ("medium.com", "*.medium.com"),
        "allowed_path_patterns": (r"^/@[^/]+/[\w\-]+", r"^/p/[\w]+"),
        "args": lambda row, result: {"url": result.published_url},
    },
    "medium-brave": {
        "channel": "html",
        "allowed_hosts": ("medium.com", "*.medium.com"),
        "allowed_path_patterns": (r"^/@[^/]+/[\w\-]+", r"^/p/[\w]+"),
        "args": lambda row, result: {"url": result.published_url},
    },
}


@dataclass(frozen=True)
class VerificationOutcome:
    """Result of a verification attempt.

    `verified`:
        True  — article asserted live with expected title and links
        False — article URL is wrong, gone, blocked, or content is missing
        None  — verification was skipped or could not be determined (transient
                failure, dry-run, non-published status)

    `verified_at`: ISO-8601 timestamp string when `verified` is bool; None
                   when `verified` is None.

    `verification_error`: short reason string. None on success or when no
                          reason applies. Format conventions:
                            - "dry_run"          (skipped, dry-run mode)
                            - "not_implemented"  (stub path; remove in later units)
                            - "host_not_allowed: <host>"
                            - "host_resolved_to_private_ip: <ip>"
                            - "http_404" / "http_410" / "http_451"
                            - "http_503" / "http_500" / ...
                            - "transient_exhausted: N/N attempts"
                            - "empty_body"
                            - "body_too_large"
                            - "non_article_url: <path>"
                            - "title_missing"
                            - "target_link_missing: <url>"
                            - "missing_provider_meta"
                            - "verifier_internal_error: <repr>"
    """

    verified: bool | None
    verified_at: str | None
    verification_error: str | None


def _now_iso() -> str:
    """Current UTC timestamp in ISO-8601 (matches the dispatcher convention)."""
    return datetime.now(timezone.utc).isoformat()


def _resolve_adapter_metadata(adapter_name: str) -> dict[str, Any]:
    """Look up centralized verification metadata for an adapter.

    Raises KeyError with a helpful message listing supported adapters when
    the name is unknown — the dispatcher should map this to verified=false
    with verification_error="missing_provider_meta" (or similar).
    """
    try:
        return _ADAPTER_METADATA[adapter_name]
    except KeyError:
        supported = ", ".join(sorted(_ADAPTER_METADATA))
        raise KeyError(
            f"no verification metadata for adapter {adapter_name!r}; "
            f"supported: {supported}"
        )


def verify_published(
    row: dict[str, Any],
    result: AdapterResult,
    *,
    service: Any = None,
) -> VerificationOutcome:
    """Dispatch entry point.

    Returns a `VerificationOutcome` describing whether the article identified
    by `result.published_url` is actually live with the expected content.

    `row` is the source JSONL payload (carries `links`, `title`, `id`).
    `result` is the `AdapterResult` returned by the adapter's publish call.
    `service` is the adapter-built API client (for the Blogger API channel);
    `None` for HTML-channel adapters.

    Skip rules (R3, R4):
      - `_dry_run=True`     → outcome(None, None, "dry_run")
      - `status != published` → outcome(None, None, None)
      - otherwise dispatch by adapter channel.
    """
    if result._dry_run:
        return VerificationOutcome(
            verified=None, verified_at=None, verification_error="dry_run"
        )
    if result.status != "published":
        return VerificationOutcome(
            verified=None, verified_at=None, verification_error=None
        )

    metadata = _resolve_adapter_metadata(result.adapter)
    channel = metadata["channel"]
    if channel == "api":
        return _verify_blogger_api(row, result, metadata=metadata, service=service)
    if channel == "html":
        return _verify_html_channel(row, result, metadata=metadata)
    # Defensive: unknown channel would have been caught by _resolve_adapter_metadata.
    return VerificationOutcome(
        verified=None,
        verified_at=None,
        verification_error=f"unknown_channel: {channel}",
    )


# --- Channel implementations (stubs land in Unit 1; real logic in Units 2, 3) ---


def _verify_html_channel(
    row: dict[str, Any],
    result: AdapterResult,
    *,
    metadata: dict[str, Any],
) -> VerificationOutcome:
    """HTML channel (Medium). Real implementation in Unit 2."""
    return VerificationOutcome(
        verified=None, verified_at=None, verification_error="not_implemented"
    )


def _verify_blogger_api(
    row: dict[str, Any],
    result: AdapterResult,
    *,
    metadata: dict[str, Any],
    service: Any,
) -> VerificationOutcome:
    """Blogger API channel. Real implementation in Unit 3."""
    return VerificationOutcome(
        verified=None, verified_at=None, verification_error="not_implemented"
    )
