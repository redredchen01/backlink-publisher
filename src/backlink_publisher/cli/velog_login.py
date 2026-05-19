"""`velog-login` CLI — Plan 2026-05-19-001 Unit 3.

Transparent alias for ``bind-channel --channel velog``.

Plan-012 (Velog adapter) originally documented a standalone ``velog-login``
subcommand. This plan unifies all browser-driven binding under ``bind-channel``;
``velog-login`` is preserved as a thin alias so plan-012 references still
resolve. The alias:

  - prints a one-line informational banner to **stderr**
    (``velog-login is an alias for: bind-channel --channel velog``)
  - prepends ``["--channel", "velog"]`` to the operator's argv
  - delegates to ``bind_channel.main``

Operator cannot pass ``--channel`` explicitly via this alias — the channel
is implied by the entry point name. Passing ``--channel`` is rejected with
``UsageError`` (exit_code=1).

Plan-012's Unit 3 (the velog-login CLI implementation) is amended in
Unit 7's plan-012 amendment to point at this alias as the canonical entry.
"""

from __future__ import annotations

import sys
from typing import Sequence

from backlink_publisher._util.errors import UsageError, handle_error
from backlink_publisher.cli import bind_channel


_BANNER = "velog-login is an alias for: bind-channel --channel velog"


def main(argv: Sequence[str] | None = None, *, _browser_runner=None) -> None:
    """Entry point for the ``velog-login`` console script.

    Always prints the alias banner to stderr first so plan-012 readers
    invoking this entry point see the redirect.
    """
    print(_BANNER, file=sys.stderr, flush=True)

    passthrough = list(sys.argv[1:] if argv is None else argv)

    # Reject any explicit --channel — the alias implies it. Detection is
    # exact: argparse-style long option only (operators don't have a short
    # form to abuse here).
    if "--channel" in passthrough:
        try:
            raise UsageError(
                "velog-login: explicit --channel is not allowed; the alias "
                "already implies --channel velog. Use bind-channel directly "
                "if you need a different channel."
            )
        except UsageError as exc:
            handle_error(exc)
            return  # unreachable

    bind_channel.main(
        ["--channel", "velog", *passthrough],
        _browser_runner=_browser_runner,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
