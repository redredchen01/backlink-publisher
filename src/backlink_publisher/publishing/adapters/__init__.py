"""Adapter dispatcher — table-driven registry (Plan Unit 7).

Replaced the if/elif chain in the previous ``publish()`` with a
single ``dispatch()`` call into ``publishing.registry``. The Medium
fallback chain (MediumAPI → MediumBrave on macOS → MediumBrowser
on Playwright) is now expressed as registration order, and the
macOS gate lives on ``MediumBraveAdapter.available()``.

Behaviour preserved verbatim:

  - Blogger: ``BloggerAPIAdapter`` only.
  - Medium:
      1. ``MediumAPIAdapter`` (Integration Token; deprecated by Medium 2023)
      2. ``MediumBraveAdapter`` (AppleScript + Brave; macOS only;
         ``available()`` short-circuits elsewhere)
      3. ``MediumBrowserAdapter`` (Playwright headed Chrome — terminal)
  - ``DependencyError`` from one adapter → try the next.
  - ``ExternalServiceError`` (401 / 429 / network) → propagate, no fall.
  - ``dry_run=True`` → sentinel ``AdapterResult`` without publishing.
  - Unknown platform → ``ExternalServiceError("unsupported platform: …")``.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError
from ..registry import dispatch, register, registered_platforms
from .._verify import DryRunInterceptError, VerifyResult, dry_run_intercept
from .base import AdapterResult
from .blogger_api import BloggerAPIAdapter
from .ghpages import GitHubPagesAPIAdapter
from .devto_api import DevtoAPIAdapter
from .hashnode import HashnodeAPIAdapter
from .instant_web import TelegraphCdpAdapter, WriteAsCdpAdapter
from .medium_api import MediumAPIAdapter
from .medium_brave import MediumBraveAdapter
from .medium_browser import MediumBrowserAdapter
from .notion_api import NotionAPIAdapter
from .telegraph_api import TelegraphAPIAdapter, verify_telegraph_setup
from .velog_graphql import VelogGraphQLAdapter
from .writeas import WriteAsAPIAdapter
from .wordpress_api import WordPressAPIAdapter
from .tumblr_api import TumblrAPIAdapter

# Import the Unit 4a velog browser recipe module so it can populate
# RECIPES["velog"] before the registration line below references it.
# Plan 2026-05-21-001 Unit 4a — registers as auth-missing fallback after
# VelogGraphQLAdapter (DependencyError → fall through; ExternalServiceError
# from API path propagates without fall-through, per registry contract).
from ..browser_publish import BrowserPublishDispatcher
from ..browser_publish.recipes import velog as _velog_recipe  # noqa: F401
from ..browser_publish.recipes import hashnode as _hashnode_recipe  # noqa: F401
from ..browser_publish.recipes import devto as _devto_recipe  # noqa: F401
from ..browser_publish.recipes import mastodon as _mastodon_recipe  # noqa: F401
from ._nofollow_rationales import NOFOLLOW_RATIONALES as _R


# Register the fallback chain per platform. Adding a new platform = one
# more ``register(...)`` call — no dispatcher changes. Each registration
# declares ``dofollow=True|False|"uncertain"`` (R1 / Plan 2026-05-20-009);
# ``False`` and ``"uncertain"`` additionally require ``rationale=`` of
# ≥80 stripped chars (R3, mirrors ``monolith_budget.toml`` discipline).
#
# CDP adapters (``TelegraphCdpAdapter`` / ``WriteAsCdpAdapter``) are
# imported from ``instant_web.py`` so the module is callable from
# regression tests on this branch, but they are NOT added to the
# dispatch chain yet — that wiring ships with Plan 001
# (PR #141 chrome-cdp-multi-channel-publish) which is still open.
#
# ── Platform verify registry ──────────────────────────────────────────
# Replaces the if/elif chain in ``verify_adapter_setup`` and
# ``_verify_live`` with dict lookups. Each platform contributes two
# functions registered below.
_OFFLINE_VERIFY: dict[str, Callable[[Config], None]] = {}
_LIVE_VERIFY: dict[str, Callable[[Config], "VerifyResult"]] = {}


def register_offline_verify(platform: str, fn: Callable[[Config], None]) -> None:
    _OFFLINE_VERIFY[platform] = fn


def register_live_verify(platform: str, fn: Callable[[Config], "VerifyResult"]) -> None:
    _LIVE_VERIFY[platform] = fn


register("blogger", BloggerAPIAdapter, dofollow=True)
register(
    "medium",
    MediumAPIAdapter,
    MediumBraveAdapter,
    MediumBrowserAdapter,
    dofollow=True,
)
register("telegraph", TelegraphAPIAdapter, dofollow=True)
register(
    "velog",
    VelogGraphQLAdapter,
    BrowserPublishDispatcher.for_channel("velog"),
    dofollow=True,
)
register("ghpages", GitHubPagesAPIAdapter, dofollow=True)
register(
    "hashnode",
    HashnodeAPIAdapter,
    BrowserPublishDispatcher.for_channel("hashnode"),
    dofollow=False,
    rationale=_R["hashnode"],
)
register("writeas", WriteAsAPIAdapter, dofollow=True)
register(
    "devto",
    DevtoAPIAdapter,
    BrowserPublishDispatcher.for_channel("devto"),
    dofollow=False,
    rationale=_R["devto"],
)
register(
    "notion",
    NotionAPIAdapter,
    dofollow=False,
    rationale=_R["notion"],
)
register(
    "mastodon",
    BrowserPublishDispatcher.for_channel("mastodon"),
    dofollow=False,
    rationale=_R["mastodon"],
)
register(
    "wordpress",
    WordPressAPIAdapter,
    dofollow=True,
)
register(
    "tumblr",
    TumblrAPIAdapter,
    dofollow=False,
    rationale=(
        "Tumblr applies rel=nofollow to outbound links on free-tier blogs per "
        "Tumblr's platform policy — all HTML <a> elements created via the "
        "official web editor carry nofollow by default. Pro-tier Tumblr accounts "
        "can manually set rel=follow on individual posts but not globally, so "
        "dofollow=False is the conservative default. Backlinks published via "
        "the API still drive referral traffic, topical relevance signal, and "
        "indexation acceleration even without PageRank transfer."
    ),
)


# ── Per-platform offline verify (replaces if/elif chain) ───────────────

def _offline_verify_blogger(config: Config) -> None:
    if not config.blogger_oauth:
        raise DependencyError(
            "Blogger OAuth not configured. "
            "Add [blogger.oauth] to ~/.config/backlink-publisher/config.toml"
        )


def _offline_verify_medium(config: Config) -> None:
    has_token = bool(config.medium_integration_token)
    from backlink_publisher.config import load_medium_token
    has_oauth = bool(load_medium_token())
    from .medium_browser import sync_playwright as _spw
    has_playwright = _spw is not None
    if not (has_token or has_oauth or has_playwright):
        raise DependencyError(
            "Medium adapter not ready: no integration_token, no OAuth token "
            "file, and Playwright is not installed. "
            "Run 'playwright install chromium' or configure a token in /settings."
        )


def _offline_verify_telegraph(config: Config) -> None:
    verify_telegraph_setup(config)


def _offline_verify_velog(config: Config) -> None:
    velog_cfg = config.velog
    cookies_path = (
        velog_cfg.cookies_path if velog_cfg else
        config.config_dir / "velog-cookies.json"
    )
    if not cookies_path.exists():
        raise DependencyError(
            f"velog cookies not found: {cookies_path}\n"
            "Run: velog-login"
        )


def _offline_verify_ghpages(config: Config) -> None:
    if config.ghpages is None or not config.ghpages.repo:
        raise DependencyError(
            "GitHub Pages config missing. Add [ghpages] repo=\"owner/name\" "
            "to ~/.config/backlink-publisher/config.toml"
        )
    if not config.ghpages_token_path.exists():
        raise DependencyError(
            "GitHub Pages PAT not stored. Write "
            f"{{\"token\": \"<pat>\"}} to {config.ghpages_token_path} "
            "(chmod 600). PAT needs Contents:Read+Write on the target repo."
        )


def _offline_verify_hashnode(config: Config) -> None:
    if config.hashnode is None or not config.hashnode.publication_id:
        raise DependencyError(
            "Hashnode config missing. Add [hashnode] publication_id=\"<id>\" "
            "to ~/.config/backlink-publisher/config.toml"
        )
    if not config.hashnode_token_path.exists():
        raise DependencyError(
            "Hashnode PAT not stored. Write "
            f"{{\"token\": \"<pat>\"}} to {config.hashnode_token_path} "
            "(chmod 600). Generate at hashnode.com/settings/developer."
        )


def _offline_verify_writeas(config: Config) -> None:
    if config.writeas is None:
        raise DependencyError(
            "Write.as config missing. Add [writeas] section to "
            "~/.config/backlink-publisher/config.toml"
        )
    if not config.writeas_token_path.exists():
        raise DependencyError(
            "Write.as token not stored. Write "
            f"{{\"token\": \"<access_token>\"}} to {config.writeas_token_path} "
            "(chmod 600). Obtain via POST /api/auth/login or writeas-login CLI."
        )


def _offline_verify_notion(config: Config) -> None:
    if not NotionAPIAdapter.available(config):
        raise DependencyError(
            "Notion integration token or database_id not configured. "
            f"Write {{\"integration_token\": \"secret_...\", \"database_id\": "
            f"\"...\"}} to {config.notion_token_path} (chmod 600). "
            "Create an Integration at https://www.notion.so/my-integrations."
        )


def _offline_verify_devto(config: Config) -> None:
    if not DevtoAPIAdapter.available(config):
        raise DependencyError(
            "Dev.to API key not configured. "
            f"Write {{\"api_key\": \"<key>\"}} to {config.devto_token_path} "
            "(chmod 600). Generate at https://dev.to/settings/extensions."
        )


def _offline_verify_wordpress(config: Config) -> None:
    if config.wordpress is None or not config.wordpress.site:
        raise DependencyError(
            "WordPress.com config missing. Add [wordpress] site=\"<slug>\" "
            "to ~/.config/backlink-publisher/config.toml"
        )
    if not config.wordpress_token_path.exists():
        raise DependencyError(
            "WordPress.com token not stored. "
            f"Write {{\"token\": \"<bearer_token>\"}} to "
            f"{config.wordpress_token_path} "
            "(chmod 600). Generate at wordpress.com/me/security."
        )


def _offline_verify_tumblr(config: Config) -> None:
    if config.tumblr is None:
        raise DependencyError(
            "Tumblr config missing. Add [tumblr] section to "
            "~/.config/backlink-publisher/config.toml"
        )
    if not config.tumblr.blog_identifier:
        raise DependencyError(
            "Tumblr blog_identifier not set. "
            "Add blog_identifier=\"<blog>\" to [tumblr] in config.toml."
        )
    if not config.tumblr.consumer_key:
        raise DependencyError(
            "Tumblr consumer_key not set. "
            "Add consumer_key=\"<key>\" to [tumblr] in config.toml "
            "(get it from https://www.tumblr.com/oauth/apps)."
        )
    if not config.tumblr.consumer_secret:
        raise DependencyError(
            "Tumblr consumer_secret not set. "
            "Add consumer_secret=\"<secret>\" to [tumblr] in config.toml "
            "(get it from https://www.tumblr.com/oauth/apps)."
        )
    if not config.tumblr_token_path.exists():
        raise DependencyError(
            "Tumblr OAuth token not stored. "
            f"Write {{\"oauth_token\": \"...\", \"oauth_token_secret\": \"...\"}} "
            f"to {config.tumblr_token_path} "
            "(chmod 600)."
        )


_OFFLINE_VERIFY: dict[str, Callable[[Config], None]] = {
    "blogger": _offline_verify_blogger,
    "medium": _offline_verify_medium,
    "telegraph": _offline_verify_telegraph,
    "velog": _offline_verify_velog,
    "ghpages": _offline_verify_ghpages,
    "hashnode": _offline_verify_hashnode,
    "writeas": _offline_verify_writeas,
    "notion": _offline_verify_notion,
    "devto": _offline_verify_devto,
    "wordpress": _offline_verify_wordpress,
    "tumblr": _offline_verify_tumblr,
}


def publish(
    payload: dict[str, Any],
    mode: str,
    config: Config,
    dry_run: bool = False,
    *,
    banner_emit: Any = None,
) -> AdapterResult:
    """Public dispatch entry point — preserved as a function for backward
    compatibility (CLI / tests / WebUI all call ``publish(...)``).

    ``banner_emit`` (Plan 2026-05-20-004 Unit 1): optional
    ``Callable[[str, dict], None]`` event sink for banner embed
    events.  ``None`` (default) suppresses banner work — preserves
    byte-identical behavior for callers that don't configure
    ``[image_gen]``.
    """
    return dispatch(payload, mode, config, dry_run=dry_run, banner_emit=banner_emit)


def verify_adapter_setup(
    platform: str,
    config: Config,
    *,
    mode: Literal["offline", "live", "dry-run"] = "offline",
    payload: Optional[dict[str, Any]] = None,
) -> Optional[VerifyResult]:
    """Verify a platform adapter can do its job. Three modes (Plan 2026-05-19-006 U2):

    - ``mode='offline'`` (default): Backward-compatible. Raises ``DependencyError``
      on failure, returns ``None`` on success. The 14+ pre-Unit-2 call sites
      (``cli/publish_backlinks.py:357``, ``cli/_resume.py:126``, test @patch sites)
      rely on this contract and continue to work unchanged.

    - ``mode='live'``: Calls the platform's lightweight verify endpoint (e.g.
      Telegraph ``getAccountInfo``). Returns ``VerifyResult``; never raises for
      auth failures. Used by ``/api/<channel>/verify`` dashboard endpoint.
      Per-channel live impls land per-adapter — Unit 2 ships stubs returning
      ``last_verify_result='never'`` (for known-unbound) or ``'unverifiable_live'``.

    - ``mode='dry-run'``: Runs the publish path under ``dry_run_intercept()``
      which monkey-patches ``requests.Session.send`` to raise. Returns
      ``VerifyResult``; guarantees zero real HTTP. Defense-in-depth per SEC-5
      review: even an adapter that forgets the flag cannot leak a real publish.
      ``payload`` kwarg supplies the would-be publish content.

    Kept as a module function (not on the ABC) per Plan D8.
    """
    if mode == "live":
        return _verify_live(platform, config)
    if mode == "dry-run":
        return _verify_dry_run(platform, config, payload or {})

    # mode == "offline" — backward-compat path
    fn = _OFFLINE_VERIFY.get(platform)
    if fn is None:
        raise DependencyError(f"No adapter configured for platform: {platform}")
    fn(config)


def _verify_live(platform: str, config: Config) -> VerifyResult:
    """Live verify — dispatches to per-platform real-API impls when available,
    falls back to ``unverifiable_live`` for platforms still pending backfill.

    Per-channel real impls land per adapter: Telegraph (Unit 6a) →
    GitHub Pages (Unit 7) → Blogger users.get → Medium /me → Velog currentUser.
    """
    if platform not in registered_platforms():
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"no adapter configured for platform: {platform}"],
        )

    # Probe offline-readiness first — if not even configured, no point pinging API.
    try:
        verify_adapter_setup(platform, config, mode="offline")
    except DependencyError as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[str(e)],
        )

    # Per-platform live verify dispatch.
    fn = _LIVE_VERIFY.get(platform)
    if fn is not None:
        return fn(config)

    # Bound but live-verify-endpoint not yet wired. Surface honestly rather
    # than fake-green. Per-adapter live impls (Medium /me) land in follow-up PRs.
    return VerifyResult(
        ok=True,
        last_verify_result="unverifiable_live",
        blockers=["live verify endpoint not yet implemented for this platform"],
    )


def _verify_telegraph_live(config: Config) -> VerifyResult:
    from .telegraph_api import TELEGRAPH_API, _HTTP_TIMEOUT_S, _INVALID_TOKEN_MARKERS, _load_token

    timeout = min(5, _HTTP_TIMEOUT_S)

    def _extract(raw):
        return raw.get("access_token") if raw else None

    def _endpoint(token):
        return (f"{TELEGRAPH_API}/getAccountInfo", None,
                {"access_token": token, "fields": '["short_name","author_name","page_count"]'})

    def _identity(body):
        if not body.get("ok"):
            err = str(body.get("error", "unknown"))
            if any(marker in err for marker in _INVALID_TOKEN_MARKERS):
                raise ValueError(f"telegraph token rejected: {err}")
            raise RuntimeError(f"telegraph API error: {err}")
        return (body.get("result") or {}).get("short_name")

    return _verify_live_generic(
        config, platform="telegraph",
        token_loader=_load_token,
        token_extractor=_extract,
        token_missing_msg="telegraph token not yet created (publish once to auto-create)",
        endpoint_builder=_endpoint,
        identity_extractor=_identity,
        timeout=timeout,
    )


def _verify_ghpages_live(config: Config) -> VerifyResult:
    from .ghpages import GITHUB_API, _load_token as _load_ghpages_token, _required_headers

    def _extract(raw):
        return raw if isinstance(raw, str) else None

    def _endpoint(token):
        return (f"{GITHUB_API}/user", {}, _required_headers(token))

    def _identity(body):
        return body.get("login") or body.get("name")

    return _verify_live_generic(
        config, platform="ghpages",
        token_loader=_load_ghpages_token,
        token_extractor=_extract,
        token_missing_msg="GitHub token not configured — save PAT to ghpages-token.json",
        endpoint_builder=_endpoint,
        identity_extractor=_identity,
        status_map={401: "token_expired", 403: "never"},
        timeout=5.0,
    )


def _verify_blogger_live(config: Config) -> VerifyResult:
    _token_timeout = 5.0

    def _loader(cfg):
        from backlink_publisher.config import load_blogger_token
        return load_blogger_token(cfg.blogger_token_path)

    def _extract(raw):
        return (raw or {}).get("token")

    def _endpoint(token):
        return ("https://www.googleapis.com/blogger/v3/users/self", {},
                {"Authorization": f"Bearer {token}"})

    def _identity(body):
        return body.get("displayName") or body.get("id")

    return _verify_live_generic(
        config, platform="blogger",
        token_loader=_loader,
        token_extractor=_extract,
        token_missing_msg="blogger access token not stored yet (bind via /settings or publish once)",
        endpoint_builder=_endpoint,
        identity_extractor=_identity,
        status_map={401: "token_expired"},
        timeout=_token_timeout,
    )


_VELOG_VERIFY_TIMEOUT_S = 5
_VELOG_CURRENT_USER_QUERY = (
    "query CurrentUser { "
    "auth { id username email is_trusted profile { id thumbnail display_name } } "
    "}"
)


def _verify_velog_live(config: Config) -> VerifyResult:
    """POST Velog v2 GraphQL ``auth`` to confirm the cookie session is live.

    Plan 2026-05-19-006 Unit 6b — replaces the stub for velog.

    Strict read-only: the on-disk ``velog-cookies.json`` is never mutated.
    Velog's implicit-refresh model (server issues a fresh ``access_token``
    via ``Set-Cookie`` on any authenticated request) is captured by
    ``requests.Session`` in-memory only — we do not persist any updated
    cookies back to disk, matching the publish adapter's behaviour.

    Status mapping:
      - 200 + ``data.auth`` non-null → ``ok``, identity=username,
        dofollow=True (velog is confirmed dofollow per Plan R-Phase4 roster)
      - 200 + ``data.auth`` is null → ``token_expired`` (velog's
        silent-drop signal that the session is no longer authenticated)
      - ``requests.Timeout`` → ``timeout``
      - everything else (HTTP non-200 / parse failure / connection error)
        → ``never``
    """
    import requests
    from .velog_graphql import (
        _VELOG_GRAPHQL_ENDPOINT,
        _VELOG_REQUIRED_HEADERS,
        _load_cookies,
    )

    velog_cfg = config.velog
    cookies_path = (
        velog_cfg.cookies_path if velog_cfg else
        config.config_dir / "velog-cookies.json"
    )

    try:
        cookies = _load_cookies(cookies_path)
    except DependencyError as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[str(e)],
        )

    try:
        resp = requests.post(
            _VELOG_GRAPHQL_ENDPOINT,
            json={"query": _VELOG_CURRENT_USER_QUERY},
            cookies=cookies,
            headers=_VELOG_REQUIRED_HEADERS,
            timeout=_VELOG_VERIFY_TIMEOUT_S,
        )
    except requests.Timeout:
        return VerifyResult(
            ok=False,
            last_verify_result="timeout",
            blockers=[
                f"velog auth probe timed out after {_VELOG_VERIFY_TIMEOUT_S}s"
            ],
        )
    except requests.RequestException as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"velog network failure: {e}"],
        )

    if resp.status_code != 200:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"velog GraphQL returned HTTP {resp.status_code}"],
        )

    try:
        body = resp.json()
    except (ValueError, Exception):
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=["velog returned non-JSON response"],
        )

    current_user = ((body or {}).get("data") or {}).get("auth")
    if current_user is None:
        return VerifyResult(
            ok=False,
            last_verify_result="token_expired",
            blockers=[
                "velog cookie session expired or revoked — run velog-login again"
            ],
        )

    identity = current_user.get("username") or current_user.get("display_name")
    return VerifyResult(
        ok=True,
        identity=identity,
        last_verified_at=_utc_now_iso(),
        last_verify_result="ok",
        dofollow=True,
    )


def _verify_hashnode_live(config: Config) -> VerifyResult:
    from .hashnode import HASHNODE_API, ME_QUERY, _required_headers, _load_token

    def _endpoint(token):
        return (HASHNODE_API, {"query": ME_QUERY}, _required_headers(token))

    def _identity(body):
        me = ((body or {}).get("data") or {}).get("me")
        if me is not None:
            return me.get("username") or me.get("name")
        errors = body.get("errors") or []
        msg = (errors[0].get("message") if errors else "") or "unknown"
        lowered = msg.lower()
        if any(k in lowered for k in ("unauthorized", "auth", "invalid token")):
            raise ValueError(f"Hashnode auth error: {msg}")
        raise RuntimeError(f"Hashnode GraphQL error: {msg}")

    return _verify_live_generic(
        config, platform="hashnode",
        token_loader=_load_token,
        token_extractor=lambda raw: raw if isinstance(raw, str) else None,
        token_missing_msg="Hashnode PAT not configured — save to hashnode-token.json",
        endpoint_builder=_endpoint,
        identity_extractor=_identity,
        status_map={401: "token_expired"},
        timeout=5.0,
    )


def _verify_writeas_live(config: Config) -> VerifyResult:
    from .writeas import _load_token, _required_headers, DEFAULT_API_BASE

    wa_cfg = config.writeas
    api_base = (wa_cfg.api_base if wa_cfg else DEFAULT_API_BASE) or DEFAULT_API_BASE

    def _endpoint(token):
        return (f"{api_base.rstrip('/')}/me", {}, _required_headers(token))

    def _identity(body):
        data = (body or {}).get("data")
        if not data:
            raise RuntimeError("Write.as /me returned empty data")
        return data.get("username") or data.get("email")

    return _verify_live_generic(
        config, platform="writeas",
        token_loader=_load_token,
        token_extractor=lambda raw: raw if isinstance(raw, str) else None,
        token_missing_msg="Write.as token not configured — re-login and save to writeas-token.json",
        endpoint_builder=_endpoint,
        identity_extractor=_identity,
        status_map={401: "token_expired"},
        timeout=5.0,
    )


def _verify_wordpress_live(config: Config) -> VerifyResult:
    """GET /sites/<site> to confirm the WordPress.com bearer token still works.

    Plan 2026-05-21-006 — live-verify stub for WordPress.com.
    Read-only by design: NEVER triggers token rotation. Reads
    ``wordpress-token.json`` (0o600, SEC-3).
    """
    from backlink_publisher.config.loader import _config_dir
    from backlink_publisher.config.tokens import _load_token as _lt
    import requests

    wp_cfg = config.wordpress
    if not (wp_cfg and wp_cfg.site):
        return VerifyResult(
            ok=False, last_verify_result="never",
            blockers=["WordPress.com site not configured — add site=\"<slug>\" to [wordpress] in config.toml"],
        )

    data = _lt(None, "wordpress-token.json")
    token = (data or {}).get("token") if data else None
    if not token:
        return VerifyResult(
            ok=False, last_verify_result="never",
            blockers=["WordPress.com token missing — create wordpress-token.json (chmod 600)"],
        )

    url = f"https://public-api.wordpress.com/rest/v1.1/sites/{wp_cfg.site}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=5.0)
    except requests.Timeout:
        return VerifyResult(
            ok=False, last_verify_result="timeout",
            blockers=[f"WordPress.com /sites/{wp_cfg.site} timed out"],
        )
    except requests.RequestException as exc:
        return VerifyResult(
            ok=False, last_verify_result="never",
            blockers=[f"WordPress.com network failure: {exc}"],
        )

    if resp.status_code == 401:
        return VerifyResult(
            ok=False, last_verify_result="token_expired",
            blockers=["WordPress.com token rejected (HTTP 401) — re-save bearer token"],
        )
    if resp.status_code == 403:
        return VerifyResult(
            ok=False, last_verify_result="token_expired",
            blockers=["WordPress.com forbidden (HTTP 403) — check site slug or token scope"],
        )
    if resp.status_code != 200:
        return VerifyResult(
            ok=False, last_verify_result="never",
            blockers=[f"WordPress.com returned HTTP {resp.status_code}"],
        )

    try:
        body = resp.json()
    except Exception:
        return VerifyResult(
            ok=False, last_verify_result="never",
            blockers=["WordPress.com returned non-JSON response"],
        )

    return VerifyResult(
        ok=True,
        identity=body.get("title") or wp_cfg.site,
        last_verified_at=_utc_now_iso(),
        last_verify_result="ok",
        dofollow=True,
    )


def _verify_tumblr_live(config: Config) -> VerifyResult:
    """GET /v2/blog/<host>/info to confirm the Tumblr OAuth1a token still works.

    Plan 2026-05-21-006 — live-verify stub for Tumblr.
    v2.4 authorization header: ``Authorization: Bearer <oauth_token>``.
    Read-only by design: NEVER writes token files.
    """
    from backlink_publisher.config.tokens import _load_token as _lt
    from requests_oauthlib import OAuth1
    import requests

    tum_cfg = config.tumblr
    if not (tum_cfg and tum_cfg.blog_identifier):
        return VerifyResult(
            ok=False, last_verify_result="never",
            blockers=["Tumblr blog_identifier not configured — add to [tumblr] in config.toml"],
        )

    oauth_data = _lt(None, "tumblr-token.json")
    if not oauth_data:
        return VerifyResult(
            ok=False, last_verify_result="never",
            blockers=["Tumblr OAuth token missing — create tumblr-token.json (chmod 600)"],
        )

    oauth_token = str(oauth_data.get("oauth_token", ""))
    if not oauth_token:
        return VerifyResult(
            ok=False, last_verify_result="token_expired",
            blockers=["Tumblr OAuth token is empty in tumblr-token.json"],
        )

    blog = tum_cfg.blog_identifier
    url = f"https://api.tumblr.com/v2/blog/{blog}/info"
    auth = OAuth1(client_key=tum_cfg.consumer_key or "", client_secret=tum_cfg.consumer_secret or "",
                  resource_owner_key=oauth_token, resource_owner_secret=str(oauth_data.get("oauth_token_secret", "")))
    try:
        resp = requests.get(url, auth=auth, timeout=5.0)
    except requests.Timeout:
        return VerifyResult(
            ok=False, last_verify_result="timeout",
            blockers=[f"Tumblr /v2/blog/{blog}/info timed out"],
        )
    except requests.RequestException as exc:
        return VerifyResult(
            ok=False, last_verify_result="never",
            blockers=[f"Tumblr network failure: {exc}"],
        )

    if resp.status_code == 401:
        return VerifyResult(
            ok=False, last_verify_result="token_expired",
            blockers=["Tumblr OAuth token rejected (HTTP 401) — re-bind"],
        )
    if resp.status_code == 404:
        return VerifyResult(
            ok=False, last_verify_result="never",
            blockers=[f"Tumblr blog not found (HTTP 404): {blog} — check blog_identifier"],
        )
    if resp.status_code != 200:
        return VerifyResult(
            ok=False, last_verify_result="never",
            blockers=[f"Tumblr returned HTTP {resp.status_code}"],
        )

    try:
        body = resp.json()
    except Exception:
        return VerifyResult(
            ok=False, last_verify_result="never",
            blockers=["Tumblr returned non-JSON response"],
        )

    blog_info = ((body or {}).get("response") or {}).get("blog", {})
    return VerifyResult(
        ok=True,
        identity=blog_info.get("title") or blog_info.get("name") or blog,
        last_verified_at=_utc_now_iso(),
        last_verify_result="ok",
        dofollow=False,
    )


_LIVE_VERIFY: dict[str, Callable[[Config], VerifyResult]] = {
    "telegraph": _verify_telegraph_live,
    "ghpages": _verify_ghpages_live,
    "blogger": _verify_blogger_live,
    "velog": _verify_velog_live,
    "hashnode": _verify_hashnode_live,
    "writeas": _verify_writeas_live,
    "wordpress": _verify_wordpress_live,
    "tumblr": _verify_tumblr_live,
}


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _verify_live_generic(
    config: Config,
    *,
    platform: str,
    token_loader: Callable[[Config], Any],
    token_extractor: Callable[[Any], str | None],
    token_missing_msg: str,
    endpoint_builder: Callable[[str], tuple[str, dict | None, dict]],
    identity_extractor: Callable[[dict], str | None],
    status_map: dict[int, str] | None = None,
    timeout: float = 5.0,
) -> VerifyResult:
    """Generic live-verify skeleton shared across platform adapters.

    ``endpoint_builder(token)`` returns ``(url, json_data, headers)``.
    ``json_data=None`` → POST with ``data=`` form-encoded.
    ``json_data={}`` (empty dict) → GET.
    ``json_data`` non-empty dict → POST with ``json=json_data``.
    ``status_map`` maps HTTP status → result string for non-200 responses.
    ``identity_extractor`` raises ``ValueError`` for ``token_expired``,
    ``RuntimeError`` for ``never``.
    """
    import requests

    try:
        raw = token_loader(config)
    except DependencyError as e:
        return VerifyResult(ok=False, last_verify_result="never", blockers=[str(e)])
    except Exception as e:
        return VerifyResult(ok=False, last_verify_result="never", blockers=[f"{platform} token unreadable: {e}"])

    token = token_extractor(raw)
    if not token:
        return VerifyResult(ok=False, last_verify_result="never", blockers=[token_missing_msg])

    url, json_data, headers = endpoint_builder(token)
    try:
        if json_data is None:
            resp = requests.post(url, data=headers, timeout=timeout)
        elif json_data:
            resp = requests.post(url, json=json_data, headers=headers, timeout=timeout)
        else:
            resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.Timeout:
        return VerifyResult(ok=False, last_verify_result="timeout", blockers=[f"{platform} timed out after {timeout}s"])
    except requests.RequestException as e:
        return VerifyResult(ok=False, last_verify_result="never", blockers=[f"{platform} network failure: {e}"])

    if status_map:
        result = status_map.get(resp.status_code)
        if result is not None:
            return VerifyResult(ok=False, last_verify_result=result, blockers=[_status_blocker(platform, resp.status_code)])

    if resp.status_code != 200:
        return VerifyResult(ok=False, last_verify_result="never", blockers=[f"{platform} returned HTTP {resp.status_code}"])

    try:
        body = resp.json()
    except Exception:
        return VerifyResult(ok=False, last_verify_result="never", blockers=[f"{platform} returned non-JSON response"])

    try:
        identity = identity_extractor(body)
    except ValueError as e:
        return VerifyResult(ok=False, last_verify_result="token_expired", blockers=[str(e)])
    except RuntimeError as e:
        return VerifyResult(ok=False, last_verify_result="never", blockers=[str(e)])
    now = _utc_now_iso()
    return VerifyResult(ok=True, identity=identity, last_verified_at=now, last_verify_result="ok", dofollow=True)


def _status_blocker(platform: str, code: int) -> str:
    blockers = {
        401: f"{platform} auth rejected (HTTP 401) — re-bind or re-save token (expired or revoked)",
        403: f"{platform} forbidden (HTTP 403) — check token scope or rate limit",
    }
    return blockers.get(code, f"{platform} returned HTTP {code}")


def _verify_dry_run(
    platform: str, config: Config, payload: dict[str, Any]
) -> VerifyResult:
    """Dry-run mode: build payload via adapter.publish() under intercept.

    The intercept (``dry_run_intercept()``) monkey-patches ``Session.send`` to
    raise ``DryRunInterceptError``, so even if the adapter forgets to honor
    any dry-run flag, the HTTP send is blocked. Adapters using non-``requests``
    HTTP libs (e.g. SDKs / urllib3 direct) are NOT caught — those fall through
    to ``last_verify_result='unverifiable_live'``.

    Unit 2 scope: ship the contract + intercept. Full per-adapter dry-run
    fidelity (anchor validation, content sanity, image rejection preview)
    lands in Unit 6 backfill.
    """
    if platform not in registered_platforms():
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"no adapter configured for platform: {platform}"],
        )

    try:
        with dry_run_intercept():
            # Today: just validate the platform routes via the existing
            # dispatch. Real adapter.publish() invocation under intercept is
            # the Unit 6 deliverable (needs payload-shape validation per
            # adapter). Surface as 'unverifiable_live' to signal "intercept
            # works but per-adapter dry-run not yet wired".
            pass
    except DryRunInterceptError as e:
        # Should never reach here for the no-op body above; future per-adapter
        # logic may.
        return VerifyResult(
            ok=False,
            last_verify_result="payload_invalid",
            blockers=[f"dry-run intercept fired: {e}"],
        )

    return VerifyResult(
        ok=True,
        last_verify_result="unverifiable_live",
        blockers=["per-adapter dry-run not yet implemented (Unit 6 deliverable)"],
    )
