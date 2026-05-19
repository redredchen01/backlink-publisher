"""Channel recipe registry + single-authority CHANNELS frozenset + EVENTS.

CHANNELS is the *only* place new browser-binding channels are added.
Every entry point (CLI argparse, webui routes, AuthExpiredError ctor,
mark_bound / mark_expired) imports from here and validates membership
before constructing paths or argv — defense in depth against
``channel=../traversal`` injection.

EVENTS (Unit 2) is the closed set of RECON event names emitted by the
``bind-channel`` CLI on stdout as JSONL. The webui's bind_job consumer
imports the same constant; the driver validates ``event_name in EVENTS``
at emit time so typos fail loud here rather than silently in production.
"""

from __future__ import annotations


CHANNELS: frozenset[str] = frozenset({"velog", "medium", "blogger"})
"""The closed set of supported binding channels. Adding a new channel
requires updating this frozenset plus shipping its recipe in
``cli/_bind/recipes/<name>.py``."""


EVENTS: frozenset[str] = frozenset({
    "channel.bind.start",
    "channel.bind.browser_ready",
    "channel.bind.login_detected",
    "channel.bind.persisted",
    "channel.bind.failed",
})
"""The closed set of RECON event names emitted on stdout JSONL by the
``bind-channel`` driver. Each line is ``{"event": <member>, "ts": ISO,
"channel": <name>, ...}``; payload fields are loose, only ``event`` is
contract.

Happy-path ordering:
  1. ``channel.bind.start`` — process started, channel validated
  2. ``channel.bind.browser_ready`` — Playwright launched, login URL open
  3. ``channel.bind.login_detected`` — recipe's bound_predicate matched
  4. ``channel.bind.persisted`` — storage_state written 0600 + mark_bound

Any failure emits ``channel.bind.failed`` with an ``error_code`` payload
field (one of ``bound_predicate_timeout`` / ``playwright_launch_failed``
/ ``storage_path_traversal`` / ``persist_io_error`` /
``stream_closed_no_terminal_event``).
"""


__all__ = ["CHANNELS", "EVENTS"]
