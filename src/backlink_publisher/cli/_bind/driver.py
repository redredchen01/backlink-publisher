"""Playwright headed driver for ``bind-channel`` — Plan 2026-05-19-001 Unit 2.

Responsibilities (in order):

  1. Validate the storage_state target path resolves *inside* ``_config_dir()``
     (defense-in-depth against supply-chain adapters injecting paths).
  2. Launch a **headed** Chromium via Playwright (lazy-imported — the module
     itself does not need Playwright at import time, only at ``run_bind``
     time, so the webui can import this module to share ``BindResult``
     without a Playwright transitive cost).
  3. Open the recipe's ``login_url``; emit ``channel.bind.browser_ready``.
  4. Block on ``recipe.bound_predicate(page)`` until the operator finishes
     login. A Playwright timeout here surfaces as
     ``error_code="bound_predicate_timeout"``.
  5. Export Playwright's ``storage_state`` via the context — atomically:
     write to ``<target>.tmp`` then ``os.replace`` to the real path, then
     ``os.chmod`` to 0600. A failure during persist is reported as
     ``error_code="persist_io_error"`` and ``mark_bound`` is **not** called.
  6. Call ``mark_bound(channel, target)`` and emit
     ``channel.bind.persisted``.

The CLI in ``bind_channel.py`` is responsible for emitting
``channel.bind.start`` (before validation) and the terminal
``channel.bind.failed`` event (mapping our ``BindResult.error_code``).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from backlink_publisher._util.errors import UsageError
from backlink_publisher.cli._bind.channels import CHANNELS, EVENTS
from backlink_publisher.config.loader import _config_dir


# Default per-bind timeout. Playwright accepts ms; 5 minutes lets the operator
# finish a social-OAuth flow including 2FA without artificial pressure.
BIND_TIMEOUT_MS = 5 * 60 * 1000


# ───────── exception sentinels (caught by run_bind and CLI) ─────────


class PlaywrightLaunchError(RuntimeError):
    """Raised when the headed Chromium fails to launch (missing browser, no
    display, etc). The caught instance carries an ``error_code`` string for
    the failed event payload."""

    def __init__(self, error_code: str = "playwright_launch_failed") -> None:
        super().__init__(error_code)
        self.error_code = error_code


class BoundPredicateTimeout(RuntimeError):
    """Raised when ``recipe.bound_predicate(page)`` exceeds the bind timeout."""


class PersistIOError(RuntimeError):
    """Raised when writing the storage_state file fails (disk full, EROFS,
    permission denied, etc)."""


class IdentityMismatch(RuntimeError):
    """Raised by a recipe's ``bound_predicate`` when the operator's current
    login is for a different account than the previously-bound one (Plan
    2026-05-19-003 Unit 1 / R6). Caught by ``run_bind`` and surfaced as a
    BindResult with ``error_code='identity_mismatch'`` and an ``extras``
    payload carrying ``old_account`` / ``new_account`` for the CLI to emit
    on the terminal failed event."""

    def __init__(self, *, old_account: str, new_account: str) -> None:
        super().__init__(
            f"identity mismatch: stored last_account={old_account!r}, "
            f"current login={new_account!r}"
        )
        self.old_account = old_account
        self.new_account = new_account


# ───────── public types ─────────


@dataclass(frozen=True)
class BindResult:
    """Terminal outcome of ``run_bind``. Consumed by the CLI's ``main`` to
    decide exit code and final event payload.

    ``extras`` carries auxiliary fields for the terminal ``channel.bind.failed``
    JSONL event (e.g., ``{"old_account": "alice", "new_account": "bob"}`` for
    ``error_code='identity_mismatch'``). Optional; defaults to ``None``.
    """

    success: bool
    channel: str
    storage_state_path: Path | None
    error_code: str | None
    extras: dict[str, Any] | None = None


class BrowserRunner(Protocol):
    """Injection seam for tests — the real implementation is
    ``_PlaywrightBrowserRunner`` (created inside ``run_bind`` on demand)."""

    def launch_and_wait(
        self,
        *,
        recipe,  # ChannelRecipe
        on_browser_ready: Callable[[], None],
        on_login_detected: Callable[[], None],
    ) -> Callable[..., None]:
        """Launch headed Chromium, navigate to recipe.login_url, wait for
        recipe.bound_predicate to match. Returns a ``storage_state_provider``
        callable invoked as ``provider(path=<str|Path>)`` to write the
        storage_state JSON to disk.

        Raises:
            PlaywrightLaunchError: launch failed (missing browser, etc).
            BoundPredicateTimeout: predicate exceeded ``BIND_TIMEOUT_MS``.
        """


# ───────── _emit: the only stdout writer ─────────


def _emit(event: str, **payload: Any) -> None:
    """Write one JSONL line to stdout. ``event`` must be a member of
    ``EVENTS``; typos raise ``AssertionError`` at emit time (fail loud here,
    not silently on the consumer side)."""
    assert event in EVENTS, (
        f"_emit: event {event!r} not in EVENTS — typo? "
        f"allowed: {sorted(EVENTS)}"
    )
    record: dict[str, Any] = {
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **payload,
    }
    print(json.dumps(record, ensure_ascii=False), flush=True)


# ───────── path + persistence helpers ─────────


def _browser_profile_dir() -> Path:
    """Persistent Chromium profile shared across all channels.

    The first bind writes cookies + localStorage + IndexedDB into this
    profile; subsequent binds reuse it. This avoids three concrete pains:

      1. Operator re-doing social-OAuth (Google / GitHub) every bind because
         OAuth providers see a fresh-fingerprint browser each time.
      2. Cloudflare / anti-bot WAFs flagging fresh-Chromium-with-no-history
         as automated and serving CAPTCHA / 403 to every bind attempt.
      3. ``mark_bound`` claiming success while the operator still sees
         "please log in" because cookies didn't survive to the next bind.

    Override via ``BACKLINK_PUBLISHER_BROWSER_PROFILE_DIR`` for test
    isolation. Default: ``<config_dir>/browser-profile``.
    """
    raw = os.environ.get("BACKLINK_PUBLISHER_BROWSER_PROFILE_DIR")
    if raw:
        return Path(raw)
    return _config_dir() / "browser-profile"


def _validate_storage_state_path(path: Path | str) -> Path:
    """Reject any storage_state target that resolves outside ``_config_dir()``.

    Mirrors ``webui_store.channel_status._validate_storage_state_path`` —
    we keep a local copy here (not an import) because driver.py runs in
    the bind-channel subprocess which doesn't need to pay for the
    ``webui_store`` import surface, and the validation is small enough
    that a second authoritative copy is cheaper than a cross-package
    coupling.
    """
    resolved = Path(path).resolve()
    config_root = _config_dir().resolve()
    try:
        resolved.relative_to(config_root)
    except ValueError as exc:
        raise UsageError(
            f"bind-channel: storage_state_path {str(path)!r} must resolve "
            f"inside {str(config_root)!r}"
        ) from exc
    return resolved


def _persist_storage_state(
    *,
    channel: str,
    target_path: Path,
    storage_state_provider: Callable[..., None],
) -> Path:
    """Atomically write storage_state JSON to ``target_path`` with mode 0600.

    Steps:
      1. Validate ``target_path`` resolves inside ``_config_dir()``.
      2. Write to a sibling temp file via ``tempfile.NamedTemporaryFile``
         (in the same dir so ``os.replace`` is atomic on the same FS).
      3. ``os.chmod`` the temp file to 0600 *before* the rename.
      4. ``os.replace`` to the target path.

    Returns the resolved target path on success.
    """
    if channel not in CHANNELS:
        raise UsageError(
            f"bind-channel: unknown channel {channel!r} "
            f"(allowed: {sorted(CHANNELS)})"
        )

    resolved = _validate_storage_state_path(target_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{channel}-storage-state.",
        suffix=".tmp",
        dir=str(resolved.parent),
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        storage_state_provider(path=str(tmp_path))
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, resolved)
    except Exception as exc:
        # Best-effort cleanup; mask filesystem errors as PersistIOError
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        if isinstance(exc, UsageError):
            raise
        raise PersistIOError(f"failed to persist storage_state: {exc}") from exc

    return resolved


# ───────── run_bind: the orchestrator ─────────


def run_bind(
    *,
    channel: str,
    recipe,  # ChannelRecipe
    _browser_runner: BrowserRunner | None = None,
) -> BindResult:
    """Drive a single binding session.

    Emits the middle three events (``browser_ready`` / ``login_detected``
    / ``persisted``); the caller (``bind_channel.main``) emits ``start``
    and the terminal ``failed`` (on non-success) events.

    On success: calls ``mark_bound(channel, storage_state_path)`` and
    returns ``BindResult(success=True, …)``.
    On failure: does **not** call ``mark_bound``; returns ``BindResult(
    success=False, error_code=<one of EVENTS payload codes>, …)``.
    """
    if channel not in CHANNELS:
        raise UsageError(
            f"bind-channel: unknown channel {channel!r} "
            f"(allowed: {sorted(CHANNELS)})"
        )

    target_path = _config_dir() / f"{channel}-storage-state.json"
    # Validate up front — fail before launching a browser.
    try:
        _validate_storage_state_path(target_path)
    except UsageError:
        return BindResult(
            success=False,
            channel=channel,
            storage_state_path=None,
            error_code="storage_path_traversal",
        )

    runner = _browser_runner or _PlaywrightBrowserRunner()

    try:
        storage_state_provider = runner.launch_and_wait(
            recipe=recipe,
            on_browser_ready=lambda: _emit(
                "channel.bind.browser_ready", channel=channel
            ),
            on_login_detected=lambda: _emit(
                "channel.bind.login_detected", channel=channel
            ),
        )
    except PlaywrightLaunchError as exc:
        return BindResult(
            success=False,
            channel=channel,
            storage_state_path=None,
            error_code=exc.error_code,
        )
    except BoundPredicateTimeout:
        return BindResult(
            success=False,
            channel=channel,
            storage_state_path=None,
            error_code="bound_predicate_timeout",
        )
    except IdentityMismatch as exc:
        # Plan 2026-05-19-003 Unit 1 / R6. Predicate detected the operator is
        # logging in as a different account than the previously-bound one.
        # CLI surfaces this on the terminal failed event via ``extras``; the
        # webui's bind_job calls ``mark_identity_mismatch`` based on the
        # error_code discriminator. Driver does NOT write storage_state
        # (current cookies are for the wrong account) and does NOT call
        # mark_bound.
        return BindResult(
            success=False,
            channel=channel,
            storage_state_path=None,
            error_code="identity_mismatch",
            extras={
                "old_account": exc.old_account,
                "new_account": exc.new_account,
            },
        )

    try:
        persisted = _persist_storage_state(
            channel=channel,
            target_path=target_path,
            storage_state_provider=storage_state_provider,
        )
    except UsageError:
        return BindResult(
            success=False,
            channel=channel,
            storage_state_path=None,
            error_code="storage_path_traversal",
        )
    except PersistIOError:
        return BindResult(
            success=False,
            channel=channel,
            storage_state_path=None,
            error_code="persist_io_error",
        )

    # Plan 2026-05-19-003 Unit 1: if the recipe's predicate wrote a tentative
    # last-account file, atomically promote it now (after storage_state is on
    # disk, before mark_bound). Failure to promote is logged but does not
    # block the bind from being marked successful — last_account is a UX
    # signal for identity_mismatch detection on the NEXT bind cycle, not a
    # security control.
    _promote_last_account_if_pending(channel)

    # Plan 2026-05-19-005 Unit 1: optional recipe-specific post_persist hook.
    # The medium recipe uses this to convert storage_state.json → cookies-only
    # medium-cookies.json + medium-meta.json (for the future MediumGraphQLAdapter
    # consumer) and unlink the now-redundant storage_state.json. Returns the
    # new canonical credential path; if the hook returns None, the driver keeps
    # the original storage_state path. Failure inside post_persist propagates
    # — the bind is NOT marked successful if the hook fails, because consumers
    # downstream of mark_bound rely on the canonical-path contract.
    canonical_path: Path = persisted
    if recipe.post_persist is not None:
        replacement = recipe.post_persist(_config_dir(), persisted)
        if replacement is not None:
            canonical_path = replacement

    # Status flip lives at the end — only AFTER the file is on disk 0600.
    from webui_store.channel_status import mark_bound
    mark_bound(channel, canonical_path)

    _emit(
        "channel.bind.persisted",
        channel=channel,
        storage_state_path=str(canonical_path),
    )

    return BindResult(
        success=True,
        channel=channel,
        storage_state_path=canonical_path,
        error_code=None,
    )


def _promote_last_account_if_pending(channel: str) -> None:
    """Atomically promote ``<config_dir>/<channel>-last-account.tentative`` to
    ``<channel>-last-account.txt`` if the tentative exists.

    Plan 2026-05-19-003 Unit 1. The recipe predicate writes the tentative
    file BEFORE returning success; the driver does the atomic rename AFTER
    storage_state persistence succeeds. This ordering ensures we never
    record an account whose cookies aren't on disk.
    """
    cfg = _config_dir()
    tentative = cfg / f"{channel}-last-account.tentative"
    if not tentative.exists():
        return
    final = cfg / f"{channel}-last-account.txt"
    try:
        os.replace(tentative, final)
    except OSError:
        # Don't mask the successful bind on rename failure. The tentative
        # orphan will be overwritten on the next bind attempt.
        pass


# ───────── real Playwright runner — lazy-imported ─────────


class _PlaywrightBrowserRunner:
    """The production browser runner. Lazy-imports Playwright so the module
    can be imported without it (the webui side wants ``BindResult`` types
    without Playwright as a transitive dependency)."""

    def launch_and_wait(
        self,
        *,
        recipe,
        on_browser_ready: Callable[[], None],
        on_login_detected: Callable[[], None],
    ) -> Callable[..., None]:
        try:
            from playwright.sync_api import sync_playwright
            from playwright.sync_api import TimeoutError as PWTimeoutError
        except ImportError as exc:
            raise PlaywrightLaunchError("playwright_not_installed") from exc

        # We control the entire lifecycle; storage_state_provider closes over
        # the context so the caller can dump after we return.
        #
        # ``launch_persistent_context`` (vs ``launch`` + ``new_context``):
        # the user_data_dir holds cookies + localStorage + IndexedDB across
        # bind runs, so OAuth providers and anti-bot WAFs see the same
        # browser fingerprint they trusted last time. See ``_browser_profile_dir``.
        # ``--disable-blink-features=AutomationControlled`` hides the
        # ``navigator.webdriver`` flag — cheap defense against the most basic
        # automation detection while remaining within Playwright's supported
        # surface (no third-party stealth plugin needed).
        pw = sync_playwright().start()
        try:
            profile_dir = _browser_profile_dir()
            profile_dir.mkdir(parents=True, exist_ok=True)
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:
            try:
                pw.stop()
            except Exception:
                pass
            raise PlaywrightLaunchError("playwright_launch_failed") from exc

        context.set_default_timeout(BIND_TIMEOUT_MS)
        # launch_persistent_context starts with one blank tab — reuse it
        # instead of opening a second one, so the operator sees a single
        # window rather than two.
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(recipe.login_url, wait_until="domcontentloaded")
        except Exception as exc:
            try:
                context.close()
                pw.stop()
            except Exception:
                pass
            raise PlaywrightLaunchError("login_url_unreachable") from exc

        on_browser_ready()

        try:
            recipe.bound_predicate(page)
        except PWTimeoutError as exc:
            try:
                context.close()
                pw.stop()
            except Exception:
                pass
            raise BoundPredicateTimeout() from exc
        except Exception:
            try:
                context.close()
                pw.stop()
            except Exception:
                pass
            raise

        on_login_detected()

        # Closure: when the driver invokes provider(path=...), dump
        # storage_state and tear down the browser. Recipe's
        # cookie_host_filter is applied here to drop cookies/origins outside
        # the channel's expected host set (R16-style defense in depth).
        def _provider(*, path) -> None:
            try:
                raw_state = context.storage_state()
                filtered = _apply_host_filter(raw_state, recipe.cookie_host_filter)
                Path(path).write_text(json.dumps(filtered, ensure_ascii=False))
            finally:
                try:
                    context.close()
                    pw.stop()
                except Exception:
                    pass

        return _provider


def _apply_host_filter(
    storage_state: dict[str, Any],
    host_filter: Callable[[str], bool],
) -> dict[str, Any]:
    """Drop cookies + origins whose host doesn't match the channel's filter.

    This is the second line of defense after the recipe-time HTTPS check
    and Playwright's same-origin behavior. A misbehaving Playwright build
    that picked up cookies from non-channel hosts must not have those
    cookies persisted to the storage_state file.
    """
    cookies = storage_state.get("cookies", []) or []
    origins = storage_state.get("origins", []) or []

    filtered_cookies = [
        c for c in cookies
        if isinstance(c, dict) and host_filter(c.get("domain", ""))
    ]
    filtered_origins = [
        o for o in origins
        if isinstance(o, dict) and host_filter(_origin_host(o.get("origin", "")))
    ]
    return {"cookies": filtered_cookies, "origins": filtered_origins}


def _origin_host(origin: str) -> str:
    """Extract host from ``https://host[:port]`` origin string."""
    if not origin or "://" not in origin:
        return ""
    rest = origin.split("://", 1)[1]
    host = rest.split("/", 1)[0]
    return host.split(":", 1)[0]


__all__ = [
    "BIND_TIMEOUT_MS",
    "BindResult",
    "BoundPredicateTimeout",
    "BrowserRunner",
    "IdentityMismatch",
    "PersistIOError",
    "PlaywrightLaunchError",
    "_browser_profile_dir",
    "_emit",
    "_persist_storage_state",
    "_promote_last_account_if_pending",
    "_validate_storage_state_path",
    "run_bind",
]
