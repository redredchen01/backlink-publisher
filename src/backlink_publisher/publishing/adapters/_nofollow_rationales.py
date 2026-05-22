"""Per-platform nofollow rationale strings for register() calls.

Extracted from adapters/__init__.py to keep the dispatch table readable.
Each key is the platform slug; value is the ≥80-char rationale required
by the monolith_budget.toml discipline when ``dofollow=False``.

Maintainer note: update this file when adding a new nofollow platform,
not adapters/__init__.py.
"""

from __future__ import annotations

NOFOLLOW_RATIONALES: dict[str, str] = {
    "devto": (
        "Dev.to applies rel=\"nofollow ugc\" to outbound links since "
        "~2022 per platform policy; every external <a> is decorated "
        "server-side regardless of account tier or post format. "
        "DevtoAPIAdapter (Plan 2026-05-21-003 Phase 2 Unit 7) is the "
        "preferred path for operators with an API key; "
        "BrowserPublishDispatcher is the fallback for operators without "
        "one (DependencyError → fall through per registry contract). "
        "backlinks here still drive referral traffic and topical "
        "relevance signals even though they don't transfer PageRank."
    ),
    "notion": (
        "Notion applies rel=nofollow to outbound hyperlinks on public "
        "pages — all <a> elements in Notion-rendered content carry "
        "nofollow regardless of account type or database visibility. "
        "This adapter's value is entity signal (DA ~75+), content "
        "syndication speed, and indexation acceleration. "
        "Plan 2026-05-21-003 Phase 2 Unit 6."
    ),
    "mastodon": (
        "Mastodon hardcodes rel=\"nofollow noopener noreferrer\" on "
        "outbound links across all instances — federation-default and "
        "not disableable per-post or per-account. Re-registered in "
        "Plan 2026-05-21-001 Unit 4c as a chrome publish channel — "
        "Fediverse referral traffic + topical signal value despite the "
        "nofollow. Single instance per config.toml [mastodon] "
        "instance_url; security policy: use a throwaway account only, "
        "never a personal Mastodon identity."
    ),
}
