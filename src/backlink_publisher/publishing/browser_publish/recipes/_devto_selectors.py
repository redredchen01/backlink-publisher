"""Dev.to editor DOM selectors — Plan 2026-05-21-001 Unit 4b.

Best-guess selectors against dev.to/new (public DOM inspection
2026-05-21; no real publish). Refresh procedure: opt-in
``real_browser_publish_smoke`` marker test.

Dev.to's editor uses a plain ``<textarea>`` with front-matter-style
metadata at the top (title is part of the markdown front-matter on the
"raw" path, OR a dedicated title input on the rich editor). The
"basic" markdown editor at /new exposes a title input + a single body
textarea — that's the surface we drive.
"""

from __future__ import annotations


COMPOSE_URL = "https://dev.to/new"

# Dev.to signin lives at /enter; dispatcher's _SIGNIN_PATTERNS already
# catches /signin / /login / /m/signin. /enter is dev.to-specific so we
# extend the recipe with its own quick check below.
DEVTO_LOGIN_PATH = "/enter"

# Title: prominent input at the top of /new. Often labelled aria-label
# or has a placeholder of "New post title here…".
TITLE_INPUT = (
    "input[placeholder*='post title' i], "
    "input[aria-label*='title' i], "
    "textarea[placeholder*='post title' i]"
)

# Body: markdown textarea. The CodeMirror skin is also possible; both
# selectors covered.
BODY_EDITOR_TEXTAREA = (
    "textarea#article-form-body, "
    "textarea[aria-label*='body' i], "
    "div.CodeMirror textarea"
)

# Tags input: comma-separated string. dev.to surfaces a tag picker but
# accepts free-form text as fallback.
TAGS_INPUT = (
    "input[placeholder*='Add up to' i], "
    "input[aria-label*='tags' i]"
)

# Publish button: bottom of editor. Disabled until title + body filled.
PUBLISH_BUTTON = (
    "button:has-text('Publish'):not([disabled]), "
    "button[type='submit']:has-text('Publish')"
)

# Post-publish URL pattern: dev.to/<username>/<slug>-<id>.
POST_PUBLISHED_URL_RE = (
    r"^https://dev\.to/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+(?:[/?#]|$)"
)

TITLE_FILL_TIMEOUT_MS = 15_000
BODY_FILL_TIMEOUT_MS = 15_000
PUBLISH_BUTTON_TIMEOUT_MS = 20_000
POST_PUBLISH_REDIRECT_TIMEOUT_MS = 30_000


__all__ = [
    "COMPOSE_URL",
    "DEVTO_LOGIN_PATH",
    "TITLE_INPUT",
    "BODY_EDITOR_TEXTAREA",
    "TAGS_INPUT",
    "PUBLISH_BUTTON",
    "POST_PUBLISHED_URL_RE",
    "TITLE_FILL_TIMEOUT_MS",
    "BODY_FILL_TIMEOUT_MS",
    "PUBLISH_BUTTON_TIMEOUT_MS",
    "POST_PUBLISH_REDIRECT_TIMEOUT_MS",
]
