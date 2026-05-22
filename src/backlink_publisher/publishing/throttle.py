"""Cross-platform publish throttle governor.

Coordinates publish timing across different platforms to avoid bot-like
burst patterns.  Uses per-process in-memory state (no IPC) — each
``publish-backlinks`` run gets its own throttle window.

Usage::

    from backlink_publisher.publishing.throttle import ThrottleGovernor

    ThrottleGovernor.wait_if_needed("blogger", min_interval_s=60)
    # ... publish to blogger ...
    ThrottleGovernor.record_publish("blogger")

The governor tracks the last publish time for each platform and for
"any platform".  When a new platform is about to publish, it ensures:

1. The per-platform minimum interval since that platform's last publish
2. A cross-platform cooldown since ANY platform's last publish

Plan 2026-05-21-001 Unit 4.
"""

from __future__ import annotations

import random
import time

from backlink_publisher._util.logger import opencli_logger as log


# Default minimum intervals in seconds.
# Per-platform: time between two publishes to the SAME platform.
# Cross-platform: time between publishes to DIFFERENT platforms.
_DEFAULT_PER_PLATFORM_S = 60
_DEFAULT_CROSS_PLATFORM_S = 45
_JITTER_FACTOR = 0.15


class ThrottleGovernor:
    """Per-process throttle with jitter.

    Thread-safe for single-threaded usage (Playwright is not thread-safe
    so the whole publish path is single-threaded).
    """

    _last_publish: dict[str, float] = {}       # platform → timestamp
    _last_any: float = 0.0                      # last publish to any platform

    @classmethod
    def wait_if_needed(
        cls,
        platform: str,
        *,
        min_interval_s: int | None = None,
        cross_interval_s: int | None = None,
    ) -> None:
        """Block until the throttle window has passed.

        Args:
            platform: Target platform name.
            min_interval_s: Per-platform minimum gap (default 60).
            cross_interval_s: Cross-platform minimum gap (default 45).
        """
        per = min_interval_s if min_interval_s is not None else _DEFAULT_PER_PLATFORM_S
        cross = cross_interval_s if cross_interval_s is not None else _DEFAULT_CROSS_PLATFORM_S

        now = time.monotonic()

        # Check per-platform delay.
        last_same = cls._last_publish.get(platform, 0.0)
        per_wait = max(0.0, last_same + per - now)

        # Check cross-platform delay.
        cross_wait = max(0.0, cls._last_any + cross - now)

        wait = max(per_wait, cross_wait)
        if wait > 0:
            jitter = wait * random.uniform(0, _JITTER_FACTOR)
            total = wait + jitter
            log.info(
                f"throttle: waiting {total:.1f}s before publishing to "
                f"{platform!r} (per-platform wait {per_wait:.1f}s, "
                f"cross-platform wait {cross_wait:.1f}s)"
            )
            time.sleep(total)

    @classmethod
    def record_publish(cls, platform: str) -> None:
        """Record that a publish just completed for this platform."""
        now = time.monotonic()
        cls._last_publish[platform] = now
        cls._last_any = now

    @classmethod
    def reset(cls) -> None:
        """Clear all timestamps (for testing)."""
        cls._last_publish.clear()
        cls._last_any = 0.0
