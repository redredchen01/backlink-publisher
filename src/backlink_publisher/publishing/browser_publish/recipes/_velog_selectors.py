"""Velog editor DOM selectors — Plan 2026-05-21-001 Unit 4a.

Best-guess selectors verified against velog.io/write as of 2026-05-21
(public DOM inspection — no real publish performed). The recipe degrades
gracefully when selectors miss: any element-not-found inside
``publish_flow`` propagates as ``ExternalServiceError`` via the
dispatcher's catch-all, prompting the operator to consult
``docs/spikes/`` or open a live `real_browser_publish_smoke`-marked
test to refresh them.

Selectors are split into a leaf module (no Playwright import) so
``_velog_selectors`` can be patched in tests without dragging in browser
deps. Each constant carries an inline comment with the DOM context.
"""

from __future__ import annotations


# Compose page URL.
COMPOSE_URL = "https://velog.io/write"

# Login-redirect signature — any path matching ``/(?:trending|recent)``-style
# pre-login landing after `/write` redirect means the operator's velog
# session is not active in this Chrome profile.
SIGNIN_REDIRECT_HOST_PREFIX = "https://velog.io/"

# Editor surface — Velog uses a CodeMirror-ish DOM (subject to change).
# ``data-testid`` would be safest if Velog ships them; absent that we
# fall back to placeholder-text + role queries.
TITLE_INPUT = "textarea[placeholder*='제목']"  # Korean "title"
BODY_EDITOR_CONTAINER = "div.CodeMirror-lines, div[contenteditable='true']"
BODY_EDITOR_FOCUSABLE = "div.CodeMirror textarea, div[contenteditable='true']"

# Publish dialog open / confirm buttons. Velog's flow:
#   1. Click "출간하기" (publish) — opens a publish-settings panel
#   2. Optionally adjust series / tags / privacy
#   3. Click confirm "출간하기" inside the panel
OPEN_PUBLISH_DIALOG_BUTTON = "button:has-text('출간하기')"
CONFIRM_PUBLISH_BUTTON_IN_DIALOG = (
    "div[class*='PublishActionButtons'] button:has-text('출간하기'), "
    "section[class*='PublishScreen'] button:has-text('출간하기')"
)

# Post-publish URL pattern. After confirm the page redirects to
# ``https://velog.io/@<handle>/<slug>``; this regex matches that shape so
# the recipe can wait_for_url instead of polling DOM.
POST_PUBLISHED_URL_RE = r"^https://velog\.io/@[^/]+/[^/?#]+(?:[/?#]|$)"

# How long to wait for each phase, in milliseconds. Velog's editor is
# JS-heavy; conservative timeouts prevent transient slowness from
# tripping the recipe.
TITLE_FILL_TIMEOUT_MS = 15_000
BODY_FILL_TIMEOUT_MS = 15_000
PUBLISH_DIALOG_TIMEOUT_MS = 20_000
POST_PUBLISH_REDIRECT_TIMEOUT_MS = 30_000


__all__ = [
    "COMPOSE_URL",
    "SIGNIN_REDIRECT_HOST_PREFIX",
    "TITLE_INPUT",
    "BODY_EDITOR_CONTAINER",
    "BODY_EDITOR_FOCUSABLE",
    "OPEN_PUBLISH_DIALOG_BUTTON",
    "CONFIRM_PUBLISH_BUTTON_IN_DIALOG",
    "POST_PUBLISHED_URL_RE",
    "TITLE_FILL_TIMEOUT_MS",
    "BODY_FILL_TIMEOUT_MS",
    "PUBLISH_DIALOG_TIMEOUT_MS",
    "POST_PUBLISH_REDIRECT_TIMEOUT_MS",
]
