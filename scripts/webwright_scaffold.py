"""Webwright-powered adapter scaffold generator.

Experimental developer tool — NOT part of the publishing pipeline.
Run via: make scaffold PLATFORM=devto [LOGIN_URL=https://dev.to/enter]

Launches a Webwright session to explore a new publishing platform's
login + article post flow. Produces a Playwright Python script draft
in docs/spikes/scaffold-<platform>-<date>/ for human review.

After review, refactor the draft into a proper adapter following the
R9 extension recipe (see AGENTS.md § "Adding a new publisher adapter").
"""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

# Known login URLs for platforms that also have bind-channel recipes.
# Extend this map as new platforms are added; it is advisory only.
_KNOWN_LOGIN_URLS: dict[str, str] = {
    "velog": "https://velog.io",
    "medium": "https://medium.com/m/signin",
    "blogger": "https://www.blogger.com",
    "devto": "https://dev.to/enter",
    "hashnode": "https://hashnode.com/onboard",
    "substack": "https://substack.com/sign-in",
    "wordpresscom": "https://wordpress.com/log-in",
    "linkedin": "https://www.linkedin.com/login",
}

REPO_ROOT = Path(__file__).parent.parent


def main() -> None:
    platform = os.environ.get("PLATFORM", "").strip()
    if not platform:
        print(
            "Error: PLATFORM is required.\n"
            "Usage: make scaffold PLATFORM=devto [LOGIN_URL=https://...]",
            file=sys.stderr,
        )
        sys.exit(1)

    login_url = os.environ.get("LOGIN_URL", "").strip()
    if not login_url:
        login_url = _KNOWN_LOGIN_URLS.get(platform.lower(), "")
    if not login_url:
        print(
            f"Error: LOGIN_URL not provided and no known URL for platform '{platform}'.\n"
            f"Usage: make scaffold PLATFORM={platform} LOGIN_URL=https://<platform-login-url>",
            file=sys.stderr,
        )
        sys.exit(1)

    task_id = f"scaffold-{platform}-{date.today().isoformat()}"
    output_dir = REPO_ROOT / "docs" / "spikes"
    output_dir.mkdir(parents=True, exist_ok=True)

    task_prompt = (
        f"Explore the login and article publishing flow on the '{platform}' platform "
        f"starting at {login_url}. "
        "Write a Python Playwright script that automates: "
        "(1) navigating to the login page, "
        "(2) completing the login flow (stop at the point a human would enter credentials — "
        "mark that step with a TODO comment), "
        "(3) navigating to the article compose / new-post page, "
        "(4) filling in a minimal test article (title + body placeholder). "
        "Mark every CSS selector or XPath with a TODO comment indicating it needs human "
        "verification against the live site. "
        "Do NOT persist cookies, write storage state, or submit any real posts — "
        "exploration and documentation only. "
        "At the end of the script, add a comment block summarising: "
        "the login URL, the compose URL, and any anti-bot or Cloudflare behaviour observed."
    )

    cmd = [
        sys.executable, "-m", "webwright.run.cli",
        "-t", task_prompt,
        "--start-url", login_url,
        "-o", str(output_dir),
        "--task-id", task_id,
    ]

    print(f"Starting Webwright scaffold session for '{platform}'...")
    print(f"Output directory: {output_dir / task_id}")
    print()

    result = subprocess.run(cmd, cwd=str(REPO_ROOT))

    if result.returncode != 0:
        print(
            f"\nWebwright exited with code {result.returncode}.",
            file=sys.stderr,
        )
        sys.exit(result.returncode)

    artifact_dir = output_dir / task_id
    print(f"\nScaffold session complete.")
    print(f"Review artifacts in: {artifact_dir}")
    print(
        "\nNext steps:\n"
        "  1. Review the generated Playwright script in the output directory\n"
        "  2. Refactor into a proper adapter following AGENTS.md § 'Adding a new publisher adapter'\n"
        "  3. The draft is in .gitignore — promote to src/...adapters/ only after review"
    )


if __name__ == "__main__":
    main()
