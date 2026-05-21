"""`bind-channel` CLI entry — Plan 2026-05-19-001 Unit 2.

Drives a headed browser session for a single browser-binding channel.

Usage:
    bind-channel --channel velog --backend auto
    bind-channel --channel medium --backend chrome
    bind-channel --channel blogger --backend playwright

Lifecycle (events emitted on stdout as JSONL):

    {"event": "channel.bind.start",          "channel": "..."}
    {"event": "channel.bind.browser_ready",  "channel": "..."}
    {"event": "channel.bind.login_detected", "channel": "..."}
    {"event": "channel.bind.persisted",      "channel": "...",
        "storage_state_path": "..."}

On failure, the terminal event is:

    {"event": "channel.bind.failed", "channel": "...",
        "error_code": "bound_predicate_timeout" | "playwright_launch_failed" |
                      "storage_path_traversal" | "persist_io_error" |
                      "stream_closed_no_terminal_event"}

Exit codes:
    0 — success
    1 — usage error (unknown channel, argparse failure)
    3 — dependency-class failure (predicate timeout, Playwright missing, etc.)
    5 — unexpected internal error

The CLI is consumed by ``webui_app/services/bind_job.py`` (Unit 4) which
reads stdout line-by-line and maps the terminal event to a Settings UI
status badge + Chinese error message.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from backlink_publisher._util.errors import (
    PipelineError,
    UsageError,
    handle_error,
    handle_unexpected_error,
)
from backlink_publisher.cli._bind import driver
from backlink_publisher.cli._bind.channels import CHANNELS
from backlink_publisher.cli._bind.recipes import RECIPES


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bind-channel",
        description=(
            "Drive a headed Playwright session or Real Chrome session to "
            "bind a publisher channel. "
            "Opens the channel's login URL, waits for the operator to sign "
            "in, exports storage_state (mode 0600) into the config dir, and "
            "flips channel_status to 'bound'."
        ),
    )
    parser.add_argument(
        "--channel",
        required=True,
        # Note: choices= is intentionally NOT used — argparse's invalid-choice
        # exit code (2) collides with our UsageError exit code (1). Channel
        # membership is enforced after parse against the CHANNELS frozenset,
        # which is the single authority and gives a uniform exit_code=1 +
        # JSONL-emitting error path.
        help=(
            "Which channel to bind. One of: " + ", ".join(sorted(CHANNELS))
            + ". Validated against CHANNELS frozenset; unknown values "
              "are rejected before any browser is launched."
        ),
    )
    parser.add_argument(
        "--backend",
        default=None,
        help=(
            "Browser backend to use. One of: "
            + ", ".join(sorted(driver.BIND_BACKENDS))
            + ". Defaults to BACKLINK_PUBLISHER_BIND_BACKEND or auto."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None, *, _browser_runner=None) -> None:
    """Entry point for the ``bind-channel`` console script.

    Args:
        argv: CLI args, defaults to ``sys.argv[1:]``. Passed through to
            argparse.
        _browser_runner: Test seam. When ``None`` (production), the driver
            uses the real Playwright runner. Tests inject a fake.

    Raises:
        SystemExit: always (via ``handle_error`` / argparse / ``sys.exit``).
    """
    parser = _build_parser()
    # Note: argparse will SystemExit(2) on a `--help` or argparse error before
    # ours runs. UsageError below maps unknown-channel rejections to exit 1
    # via handle_error.
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        raise

    # Defense-in-depth: argparse `choices=` already enforces membership, but
    # if someone bypasses argparse (calling main() directly with a fabricated
    # args object in tests) the explicit re-check protects the rest of the
    # pipeline.
    if args.channel not in CHANNELS:
        try:
            raise UsageError(
                f"bind-channel: unknown channel {args.channel!r} "
                f"(allowed: {sorted(CHANNELS)})"
            )
        except UsageError as exc:
            handle_error(exc)
            return  # unreachable; handle_error SystemExits

    channel = args.channel
    os.environ.setdefault("BACKLINK_PUBLISHER_BIND_CHANNEL", channel)
    try:
        backend = driver.resolve_backend(args.backend)
    except UsageError as exc:
        handle_error(exc)
        return  # unreachable

    try:
        driver._emit("channel.bind.start", channel=channel, backend=backend)
        recipe = RECIPES[channel]
        result = driver.run_bind(
            channel=channel,
            recipe=recipe,
            backend=backend,
            _browser_runner=_browser_runner,
        )
    except PipelineError as exc:
        # UsageError, DependencyError, etc — emit terminal failed event for
        # the consumer (webui) before delegating to handle_error.
        driver._emit(
            "channel.bind.failed",
            channel=channel,
            backend=backend,
            error_code=type(exc).__name__,
            message=str(exc),
        )
        handle_error(exc)
        return  # unreachable
    except Exception as exc:  # noqa: BLE001 — last-line defense
        driver._emit(
            "channel.bind.failed",
            channel=channel,
            backend=backend,
            error_code="unexpected",
            message=str(exc),
        )
        handle_unexpected_error(exc)
        return  # unreachable

    if not result.success:
        # Plan 2026-05-19-003 Unit 1: surface BindResult.extras (e.g.,
        # old_account/new_account for identity_mismatch) on the terminal
        # JSONL event so the webui's bind_job can call
        # mark_identity_mismatch with the right discriminator.
        extras = result.extras or {}
        driver._emit(
            "channel.bind.failed",
            channel=channel,
            backend=backend,
            error_code=result.error_code or "unknown",
            **extras,
        )
        # Map error_code to PipelineError subclass for the exit code.
        # All our internal failure codes are dependency-class (exit 3) — the
        # operator needs to take an action (re-install Playwright, re-login,
        # check disk, resolve identity-mismatch in the UI).
        from backlink_publisher._util.errors import DependencyError
        handle_error(
            DependencyError(
                f"bind-channel: {channel} failed "
                f"(error_code={result.error_code})"
            )
        )
        return  # unreachable

    # Happy path — exit 0
    sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
