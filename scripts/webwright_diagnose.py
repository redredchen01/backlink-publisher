"""Webwright-powered bind-channel failure diagnostics.

Experimental developer tool — NOT part of the publishing pipeline.
Run via: make diagnose CHANNEL=velog

Launches a Webwright session to reproduce the bind-channel login flow
for the given channel and record failure evidence (screenshots + script
+ summary). Output lands in docs/diagnostics/<channel>-<date>/.

The session is read-only: it must not write storage_state, cookies, or
config files. The generated artifacts are .gitignored; copy specific
screenshots or scripts to a tracked location if you want to preserve them.
"""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def _load_recipes() -> dict:
    """Import RECIPES from the bind recipes module.

    Requires backlink_publisher to be on sys.path (editable install).
    """
    try:
        from backlink_publisher.cli._bind.recipes import RECIPES  # type: ignore
        return RECIPES
    except ImportError as exc:
        print(
            f"Error: cannot import bind recipes — {exc}\n"
            "Make sure you ran: pip install -e '.[dev-webwright]'",
            file=sys.stderr,
        )
        sys.exit(1)


def _check_for_storage_state_writes(output_dir: Path) -> bool:
    """Scan generated .py files for storage_state writes. Returns True if found."""
    found = False
    for py_file in output_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "storage_state" in content and (
            "storage_state(" in content or "storage_state=" in content
        ):
            if not found:
                print(
                    "\nWARNING: generated script(s) contain storage_state references — "
                    "review carefully before using:",
                    file=sys.stderr,
                )
                found = True
            print(f"  {py_file}", file=sys.stderr)
    return found


def main() -> None:
    channel = os.environ.get("CHANNEL", "").strip()
    if not channel:
        print(
            "Error: CHANNEL is required.\n"
            "Usage: make diagnose CHANNEL=velog",
            file=sys.stderr,
        )
        sys.exit(1)

    recipes = _load_recipes()
    if channel not in recipes:
        valid = ", ".join(sorted(recipes.keys()))
        print(
            f"Error: unknown channel '{channel}'.\n"
            f"Valid channels: {valid}",
            file=sys.stderr,
        )
        sys.exit(1)

    login_url: str = recipes[channel].login_url
    task_id = f"{channel}-{date.today().isoformat()}"
    output_dir = REPO_ROOT / "docs" / "diagnostics" / task_id
    output_dir.mkdir(parents=True, exist_ok=True)

    task_prompt = (
        f"Diagnose the browser login flow for the '{channel}' publishing platform "
        f"starting at {login_url}. "
        "Navigate to the login URL and attempt to walk through the OAuth or credential "
        "login flow step by step. At each step: "
        "(1) take a screenshot named '<channel>-step-<N>-<short-description>.png', "
        "(2) note the current URL and any visible page title or heading, "
        "(3) record the state of key interactive elements (buttons, forms, error messages). "
        "If you encounter any block — Cloudflare challenge, rate-limit page, 2FA prompt, "
        "missing element, redirect loop, or network error — document it with a screenshot "
        "and a detailed description of the page state. "
        "IMPORTANT: Do NOT persist cookies, do NOT write storage_state to any file, "
        "do NOT submit any real credentials or form data. This is a read-only diagnostic. "
        "At the end, write a plain-text summary file 'summary.txt' containing: "
        "final URL reached, list of observed blockers with step number, "
        "CSS selectors or element IDs that were missing or changed, "
        "and a recommended fix direction for the bind-channel adapter."
    )

    cmd = [
        sys.executable, "-m", "webwright.run.cli",
        "-t", task_prompt,
        "--start-url", login_url,
        "-o", str(output_dir),
        "--task-id", task_id,
    ]

    print(f"Starting Webwright diagnostic session for channel '{channel}'...")
    print(f"Login URL: {login_url}")
    print(f"Output directory: {output_dir}")
    print()

    result = subprocess.run(cmd, cwd=str(REPO_ROOT))

    # Always scan output even if Webwright exited non-zero (partial artifacts may exist).
    _check_for_storage_state_writes(output_dir)

    if result.returncode != 0:
        print(
            f"\nWebwright exited with code {result.returncode}.",
            file=sys.stderr,
        )
        sys.exit(result.returncode)

    print(f"\nDiagnostic session complete.")
    print(f"Artifacts: {output_dir}")
    print(
        "\nNext steps:\n"
        "  1. Read summary.txt for the failure analysis\n"
        "  2. Review screenshots to understand the exact failure point\n"
        f"  3. Update src/backlink_publisher/cli/_bind/recipes/{channel}.py "
        "or the relevant adapter based on findings\n"
        "  4. Artifacts are .gitignored — copy specific files to a tracked location if needed"
    )


if __name__ == "__main__":
    main()
