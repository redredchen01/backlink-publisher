"""`phase0-seal` CLI — Telegraph Phase 0 ship SHA seal operator-side tool.

Subcommands:
    init        Write seal notes for each worktree HEAD after G1 Pass.
    show        Print current seal (markdown allowlist or JSON).
    verify      Compare seal SHAs to current worktree HEADs.
    reseal      Refresh seal SHAs while preserving verdict_ref + sealed_at.
    verify-hook Hook-side validator (invoked by .git/hooks/pre-push).

Unit 2 lands the dispatcher skeleton; each subcommand handler currently
raises NotImplementedError. Subsequent units fill them in:
    Unit 3 → init (incl. --manual-verdict + post-push verify)
    Unit 4 → show, verify, reseal
    Unit 5 → verify-hook
"""

from __future__ import annotations

import argparse
import sys


# Exit-code namespace (R8 documents the contract for hook + R7a):
EXIT_OK = 0           # success
EXIT_MISUSE = 1       # subcommand-specific misuse (e.g., seal already exists)
EXIT_WORKTREE = 2     # worktree missing / dirty / detached / evidence-file-out-of-repo
EXIT_VERDICT = 3      # gh auth fail / comment validation fail / allowlist load fail
EXIT_NOT_IMPLEMENTED = 99  # unit not yet landed (removed once impl lands)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phase0-seal",
        description="Telegraph Phase 0 ship SHA seal — operator-side CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    init_p = sub.add_parser(
        "init",
        help="Create seal notes after observing G1 Pass routine comment",
    )
    src = init_p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--verdict-comment",
        metavar="URL",
        help="Full URL of the G1 Pass PR comment posted by the routine bot",
    )
    src.add_argument(
        "--manual-verdict",
        action="store_true",
        help="Fallback for routine outage; pair with --evidence-log",
    )
    init_p.add_argument(
        "--verdict-pr",
        type=int,
        help="PR # the verdict comment belongs to (required for --verdict-comment)",
    )
    init_p.add_argument(
        "--evidence-log",
        metavar="PATH",
        help="Relative path to committed evidence file (required for --manual-verdict)",
    )
    init_p.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip the render-and-confirm prompt before writing notes",
    )
    init_p.set_defaults(handler=_handle_init)

    # show
    show_p = sub.add_parser("show", help="Print current seal block(s)")
    show_p.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (markdown applies R15b field allowlist; default markdown)",
    )
    show_p.add_argument(
        "--unit",
        metavar="UNIT",
        help="Restrict output to one unit (e.g., unit2); default = all 4",
    )
    show_p.set_defaults(handler=_handle_show)

    # verify
    verify_p = sub.add_parser("verify", help="Compare seal SHAs to current worktree HEADs")
    verify_p.add_argument(
        "--check-comment",
        action="store_true",
        help="Also re-fetch verdict comment via gh and re-validate author/marker",
    )
    verify_p.set_defaults(handler=_handle_verify)

    # reseal
    reseal_p = sub.add_parser(
        "reseal",
        help="Update seal SHAs to current worktree HEADs; preserves verdict_ref + sealed_at",
    )
    reseal_p.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip the old→new diff prompt before writing",
    )
    reseal_p.set_defaults(handler=_handle_reseal)

    # verify-hook (Unit 5)
    hook_p = sub.add_parser(
        "verify-hook",
        help="Hook-side validator; invoked by .git/hooks/pre-push (Unit 5)",
    )
    hook_p.add_argument(
        "--stdin-lines",
        action="store_true",
        help="Read all stdin lines (multi-ref push); validate each that matches Telegraph pattern",
    )
    hook_p.set_defaults(handler=_handle_verify_hook)

    return parser


# ---------------------------------------------------------------------------
# Stub handlers (raise NotImplementedError; replaced in subsequent units).
# ---------------------------------------------------------------------------


def _handle_init(args: argparse.Namespace) -> int:
    raise NotImplementedError("phase0-seal init: not yet implemented (lands in Unit 3)")


def _handle_show(args: argparse.Namespace) -> int:
    raise NotImplementedError("phase0-seal show: not yet implemented (lands in Unit 4)")


def _handle_verify(args: argparse.Namespace) -> int:
    raise NotImplementedError("phase0-seal verify: not yet implemented (lands in Unit 4)")


def _handle_reseal(args: argparse.Namespace) -> int:
    raise NotImplementedError("phase0-seal reseal: not yet implemented (lands in Unit 4)")


def _handle_verify_hook(args: argparse.Namespace) -> int:
    raise NotImplementedError("phase0-seal verify-hook: not yet implemented (lands in Unit 5)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Argparse dispatcher.

    Returns an exit code rather than calling sys.exit() so tests can call
    main() in-process and inspect the return value.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args) or EXIT_OK
    except NotImplementedError as exc:
        print(f"phase0-seal: {exc}", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
