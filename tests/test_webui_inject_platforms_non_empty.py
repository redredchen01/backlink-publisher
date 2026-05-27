"""Regression guard: cold-boot create_app() populates the platforms registry.

`webui_app/__init__.py` has a `# noqa: F401` side-effect import of
`backlink_publisher.publishing.adapters`. Pyflakes flags it as unused; deleting
it (a tempting "cleanup") silently empties `registered_platforms()` when the
WebUI is the first import path, breaking platform dropdowns and the publish
form. This test asserts the side-effect import survives any future pyflakes
sweep.

Mechanism: import a fresh `create_app()` and request the index context
processor; assert at least one platform appears. The test uses a subprocess
to guarantee a cold-import path, so it does NOT inherit the parent process's
already-populated registry.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_inject_platforms_returns_non_empty_on_cold_boot():
    """Cold-boot create_app() and assert registered_platforms() is non-empty.

    Catches the failure mode where someone deletes the side-effect adapter
    import from `webui_app/__init__.py` because pyflakes flagged it.
    """
    script = textwrap.dedent(
        """
        import os, sys, json, tempfile
        # Use a fresh, empty sandbox config dir so the test sees a cold-boot
        # state (no pre-populated adapters from the parent config) while still
        # satisfying the fail-closed resolver (sentinel requires an override).
        # Previously this used os.environ.pop(...) but that triggers the
        # fail-closed branch when BACKLINK_PUBLISHER_TEST_SANDBOX is set.
        os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = tempfile.mkdtemp()
        from webui_app import create_app
        app = create_app()
        with app.app_context(), app.test_request_context("/"):
            # context_processor returns a dict; trigger it via render context.
            ctx = {}
            for processor in app.template_context_processors[None]:
                ctx.update(processor())
            platforms = ctx.get("platforms", [])
        print(json.dumps({"count": len(platforms),
                          "slugs": [p["slug"] for p in platforms]}))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    import json
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["count"] >= 1, (
        f"registered_platforms() returned empty list on cold boot. "
        f"Did someone delete the side-effect adapter import in "
        f"webui_app/__init__.py? Payload: {payload}"
    )
