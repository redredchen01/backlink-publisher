"""Telegraph (telegra.ph) API publishing adapter — single API path.

Plan: docs/plans/2026-05-19-002-feat-telegraph-channel-end-to-end-wiring-plan.md
Origin: docs/brainstorms/2026-05-19-telegraph-channel-end-to-end-wiring-requirements.md

**Canonical URL support: N/A** (Plan 2026-05-21-003 Unit 2). Telegraph's
node-tag whitelist (see ``telegraph_node._ALLOWED_TAGS``) does not include
``<link>`` or any head-meta tag; the API renders only structural body
elements. Telegraph therefore cannot carry a ``rel=canonical`` —
``payload.seo.canonical_url`` is ignored by this adapter, by design. Rows
that need syndication-mode canonical should route to a different platform.

Design summary:
* Single adapter (no fallback element) registered via
  ``register("telegraph", TelegraphAPIAdapter)``.  Telegraph has no
  traditional account system, so a browser fallback would solve no
  observed failure (Group 3 cut in brainstorm — P0-Q1).
* 401 INVALID_TOKEN handling = in-adapter "re-create account"
  recovery (NOT dispatcher fallback): rotate via createAccount → archive
  old token → retry once.  Second 401 propagates as
  ``ExternalServiceError``.
* Concurrency: ``fcntl.flock`` around the rotate-write sequence;
  atomic write via tmp + chmod + os.replace.  Document-level rationale
  in plan §Risks "并发 publish 进程同时 401 → 漏号".
* Token persistence: ``~/.config/backlink-publisher/telegraph-token.json``
  (0o600), schema ``{access_token, short_name}``.  Backward-compat:
  one-time auto-migration from legacy
  ``telegraph-phase0-token.json`` (spike-era name) if it's the only
  file present.
* No counter file — WARN log via the standard ``logging`` module is the
  audit trail.  Plan §Threat Model #3 ("silent token rotation") covered
  by ``grep telegraph_token_rotated`` over logs + operator playbook in
  README.
* Markdown → Telegraph Node tree via the existing
  ``markdown_to_telegraph_nodes`` helper.  Anything above the 60 KB
  pre-flight budget is rejected as ``ExternalServiceError`` (Telegraph's
  hard limit is 64 KB; spike uses 60 KB headroom).
"""

from __future__ import annotations

import fcntl
import json
import logging
import mimetypes
import os
import random
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

from backlink_publisher.config import Config
from backlink_publisher._util.errors import (
    BannerUploadError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher.publishing.adapters.telegraph_node import (
    markdown_to_telegraph_nodes,
)
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult


log = logging.getLogger(__name__)

#: Telegraph public API root.  No auth needed for createAccount; access_token
#: scoping is enforced server-side for createPage / editPage.
TELEGRAPH_API = "https://api.telegra.ph"

#: Telegraph upload endpoint host.  Distinct from ``TELEGRAPH_API`` —
#: ``/upload`` lives on the bare ``telegra.ph`` host and is anonymous
#: (no access_token), and Telegraph's CDN serves the resulting files
#: under this same host as ``/file/<sha>.<ext>``.
_TELEGRAPH_UPLOAD_HOST = "https://telegra.ph"

#: HTTP timeout for every Telegraph API call (createAccount / createPage).
#: Spike uses 15s; we keep parity.
_HTTP_TIMEOUT_S = 15

#: Soft pre-flight cap on Node-tree JSON byte size.  Telegraph's hard
#: cap is 64 KB; we reject at 60 KB to leave headroom for server-side
#: normalization (matches spike's PUBLISH_BUDGET_BYTES).
_NODE_BUDGET_BYTES = 60 * 1024

#: Lock acquisition timeout for the rotate-write sequence.  Above this,
#: assume a stuck peer process and abort rather than race.
_LOCK_TIMEOUT_S = 10

#: Error strings Telegraph returns when ``access_token`` is invalid.
#: We match both because Telegraph has used both forms historically.
_INVALID_TOKEN_MARKERS: tuple[str, ...] = (
    "ACCESS_TOKEN_INVALID",
    "INVALID_ACCESS_TOKEN",
    "INVALID_TOKEN",
)

#: Default ``short_name`` when none is configured.  Telegraph requires
#: 1-32 chars; this matches spike default.
_DEFAULT_SHORT_NAME = "backlink-publisher"


# ── Token file paths ─────────────────────────────────────────────────────────


def _token_path(config: Config) -> Path:
    """Canonical token file path (post-rename, no ``-phase0-`` infix)."""
    return config.config_dir / "telegraph-token.json"


def _legacy_token_path(config: Config) -> Path:
    """Legacy ``-phase0-`` path written by ``scripts/telegraph_spike``.

    Read-only fallback for one-time migration.  Adapter NEVER writes here.
    """
    return config.config_dir / "telegraph-phase0-token.json"


def _lock_path(token_path: Path) -> Path:
    return token_path.with_suffix(token_path.suffix + ".lock")


# ── Token I/O ────────────────────────────────────────────────────────────────


def _load_token(config: Config) -> dict[str, str]:
    """Load ``{access_token, short_name}`` from the token file.

    One-time migration: if only the legacy ``telegraph-phase0-token.json``
    is present, copy it forward to the canonical path then delete the
    legacy file.  Logs a single WARN so operators notice the migration.

    Raises:
        DependencyError: token file missing, wrong perms, or unparseable.
    """
    primary = _token_path(config)
    legacy = _legacy_token_path(config)

    if not primary.exists() and legacy.exists():
        # One-time migration.
        try:
            data = json.loads(legacy.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise DependencyError(
                f"Cannot read legacy telegraph-phase0-token.json: {exc}"
            ) from None
        _write_token_atomic(primary, data)
        try:
            legacy.unlink()
        except OSError:
            # Non-fatal: legacy file removal failed but new file is in place.
            log.warning("telegraph_legacy_token_unlink_failed path=%s", legacy)
        log.warning(
            "telegraph_token_migrated from=%s to=%s",
            legacy,
            primary,
        )

    if not primary.exists():
        raise DependencyError(
            f"Telegraph token not found: {primary}\n"
            "Run a publish to auto-create an anonymous Telegraph account, "
            "or place a token file there manually."
        )

    mode = os.stat(primary).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"telegraph-token.json must be 0600 (found {oct(mode)})\n"
            f"Run: chmod 600 {primary}"
        )

    try:
        data = json.loads(primary.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DependencyError(
            f"Cannot parse telegraph token: {exc}"
        ) from None

    if not data.get("access_token"):
        # Catches both missing key and empty-string value — an empty
        # access_token would silently slip through to Telegraph and trigger
        # 401 self-heal as a side effect, masking the real (operator-caused)
        # config error.  Fail loud instead.
        raise DependencyError(
            "telegraph-token.json missing or empty 'access_token' field"
        )
    return data


def _write_token_atomic(path: Path, data: dict[str, str]) -> None:
    """Write ``data`` to ``path`` atomically with 0600 perms.

    tmp file → chmod 0o600 → ``os.replace`` (atomic rename).  Crash
    between any step never leaves the destination in a half-written
    state.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    # Belt-and-suspenders: confirm perms survived rename (replace preserves
    # the source file's mode on POSIX, but verify in case of a future
    # cross-filesystem move).
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        os.chmod(path, 0o600)


# ── Rotation lock ────────────────────────────────────────────────────────────


@contextmanager
def _token_lock(token_path: Path) -> Iterator[None]:
    """Advisory file lock around the rotate-write sequence.

    Two concurrent publish processes hitting 401 simultaneously would
    otherwise both call createAccount and the later writer's account
    would be orphaned on Telegraph's side with no audit trail (plan
    §Risks "并发 publish 进程同时 401 → 漏号").

    The lock file lives next to the token file with a ``.lock`` suffix
    and is created if absent.  Lock is released and the file kept (no
    cleanup race needed).

    Poll uses jittered sleep to avoid thundering-herd wakeups when
    multiple peers hit the deadline together.
    """
    lock_path = _lock_path(token_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise ExternalServiceError(
                        f"Could not acquire telegraph token lock "
                        f"(waited {_LOCK_TIMEOUT_S}s): {lock_path}"
                    )
                time.sleep(random.uniform(0.05, 0.15))
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _archive_orphan_token(token_path: Path) -> Path | None:
    """Move ``token_path`` to ``<path>.orphaned-<UTC iso>``.

    Returns the archive path on success, ``None`` if the source did not
    exist (treated as already-archived / no-op).

    The archive preserves the original access_token so an operator can
    later (a) audit when a rotation happened and (b) file a Telegraph
    support request to recover edit access to pages owned by the
    orphaned account.
    """
    if not token_path.exists():
        return None
    # ISO 8601 UTC with microseconds (``-`` instead of ``:`` for filesystem
    # safety).  Microseconds avoid same-second collisions between two
    # concurrent rotations that would otherwise let the second
    # ``os.replace`` silently overwrite the first archive and lose the
    # orphaned access_token (review finding: adversarial + security +
    # reliability triple-flagged).
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    archive = token_path.with_suffix(token_path.suffix + f".orphaned-{stamp}")
    os.replace(token_path, archive)
    os.chmod(archive, 0o600)
    return archive


# ── Telegraph API helpers ────────────────────────────────────────────────────


def _is_invalid_token_error(body: dict[str, Any]) -> bool:
    """Heuristic match for Telegraph's 401-equivalent response.

    Telegraph returns HTTP 200 with ``{"ok": false, "error": "..."}``
    for application-level errors; access_token failures use one of the
    markers in ``_INVALID_TOKEN_MARKERS``.
    """
    if body.get("ok"):
        return False
    err = str(body.get("error", "")).upper()
    return any(marker in err for marker in _INVALID_TOKEN_MARKERS)


def _create_account(short_name: str) -> str:
    """POST createAccount, return fresh access_token.

    Raises:
        ExternalServiceError: network failure or Telegraph non-ok body.
    """
    try:
        resp = requests.post(
            f"{TELEGRAPH_API}/createAccount",
            data={"short_name": short_name},
            timeout=_HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        raise ExternalServiceError(
            f"Telegraph createAccount network failure: {exc}"
        ) from exc

    if not body.get("ok"):
        raise ExternalServiceError(
            f"Telegraph createAccount rejected: {body.get('error', body)}"
        )
    # Defensive against unexpected response shape (Telegraph API change
    # / proxy rewrite): a malformed ``result`` would otherwise surface as
    # a bare KeyError instead of an actionable ExternalServiceError.
    result = body.get("result") or {}
    new_token = result.get("access_token")
    if not new_token:
        raise ExternalServiceError(
            f"Telegraph createAccount returned malformed body: {body}"
        )
    return new_token


def _create_page(
    access_token: str,
    title: str,
    nodes: list[dict[str, Any]],
    return_content: bool = False,
) -> dict[str, Any]:
    """POST createPage.  Returns the raw Telegraph response body.

    The caller decides whether the body is an invalid-token error
    (see ``_is_invalid_token_error``) vs a hard failure.  We re-raise
    on network errors but **not** on application-level ``ok=false``;
    that lets ``publish()`` branch on token-error vs other errors.
    """
    payload = {
        "access_token": access_token,
        "title": title,
        "content": json.dumps(nodes, ensure_ascii=False),
        "return_content": "true" if return_content else "false",
    }
    try:
        resp = requests.post(
            f"{TELEGRAPH_API}/createPage",
            data=payload,
            timeout=_HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise ExternalServiceError(
            f"Telegraph createPage network failure: {exc}"
        ) from exc


# ── Adapter ──────────────────────────────────────────────────────────────────


class TelegraphAPIAdapter(Publisher):
    """Single-path Telegraph publisher with in-adapter 401 recovery."""

    def embed_banner(self, artifact_path: Path, alt: str) -> str | None:
        """Upload banner bytes to Telegraph's anonymous ``/upload``
        endpoint, return ``https://telegra.ph/file/<sha>.<ext>`` URL.

        Plan 2026-05-20-004 Unit 2.  Telegraph's ``/upload`` endpoint
        is anonymous — no ``access_token`` parameter — so this is the
        rare media endpoint that skips the credential-rotation dance
        entirely (see ``[[reference-telegraph-adapter-credential-rotation-pattern]]``).
        The returned URL lives on Telegraph's own CDN; embedding it
        in the post body means the image survives the upstream
        image-gen provider's CDN TTL.

        Raises ``BannerUploadError`` (NOT ``ExternalServiceError``) on
        4xx/5xx, network failure, malformed JSON, missing ``src``
        field, or local file-read error.  ``BannerUploadError`` is the
        contract the publish-time dispatcher (``banner_dispatcher.apply``)
        recognizes for honoring ``config.image_gen.strict`` — wrapping
        as ``ExternalServiceError`` would route this into the wrong
        catch branch and skip the strict gate.

        The ``alt`` argument is unused here (no API field for it; the
        dispatcher prepends ``![alt](url)`` markdown into the body so
        the alt text lands in the Telegraph Node tree downstream).
        """
        del alt  # signal to readers that the field is intentionally unused

        try:
            data = artifact_path.read_bytes()
        except OSError as exc:
            raise BannerUploadError(
                f"telegraph banner read failed: {artifact_path}: {exc}"
            ) from exc

        filename = artifact_path.name or "banner.png"
        guessed_mime, _ = mimetypes.guess_type(filename)
        # Telegraph's /upload validates by file content too, not just the
        # multipart mime — image/png is the safe default for cases where
        # the file has no extension (rare but possible with sha-only names).
        mime = guessed_mime or "image/png"

        try:
            resp = requests.post(
                f"{_TELEGRAPH_UPLOAD_HOST}/upload",
                files={"file": (filename, data, mime)},
                timeout=_HTTP_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            raise BannerUploadError(
                f"telegraph upload network: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise BannerUploadError(
                f"telegraph upload failed: {resp.status_code}"
            )

        try:
            body = resp.json()
        except ValueError as exc:
            # ``requests.Response.json`` raises ``ValueError`` (or
            # ``simplejson.JSONDecodeError`` which inherits ValueError)
            # on unparseable bodies — Telegraph occasionally returns
            # HTML on edge errors, so this is a real failure mode.
            raise BannerUploadError(
                f"telegraph upload malformed body (not JSON): {exc}"
            ) from exc

        # Telegraph success shape: ``[{"src": "/file/<sha>.<ext>"}]``.
        # Error shape: ``{"error": "<message>"}`` as a JSON object.
        if isinstance(body, dict) and body.get("error"):
            raise BannerUploadError(
                f"telegraph upload rejected: {body['error']}"
            )

        if not isinstance(body, list) or not body or not isinstance(body[0], dict):
            raise BannerUploadError(
                f"telegraph upload malformed body shape: {body!r}"
            )

        src = body[0].get("src") or ""
        if not src:
            raise BannerUploadError(
                f"telegraph upload returned empty src: {body!r}"
            )

        # Telegraph's ``src`` is path-relative (``/file/<sha>.<ext>``);
        # prepend the host so the URL is directly usable in a
        # ``![alt](url)`` body prepend.
        return f"{_TELEGRAPH_UPLOAD_HOST}{src}"

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        title = payload.get("title", "").strip() or "Untitled"
        log.info("telegraph_publish_start id=%s title=%r", article_id, title)

        # 1. Convert markdown → Node tree.  Pre-flight 60 KB budget check
        #    before any network I/O.  Wrap conversion errors as
        #    ExternalServiceError so callers see a consistent exception type
        #    instead of HTMLParser internals bubbling out.
        md = payload.get("content_markdown") or payload.get("content_md") or ""
        try:
            nodes, stats = markdown_to_telegraph_nodes(md)
        except Exception as exc:
            raise ExternalServiceError(
                f"Telegraph markdown conversion failed: {exc}"
            ) from exc
        if not nodes:
            raise ExternalServiceError(
                "Telegraph payload is empty after markdown conversion"
            )
        if stats.get("utf8_bytes", 0) > _NODE_BUDGET_BYTES:
            raise ExternalServiceError(
                f"Telegraph payload exceeds 60KB budget "
                f"({stats['utf8_bytes']} bytes); aborting before API call"
            )

        # 2. Load token (with auto-migration of legacy phase0 file + auto
        #    createAccount when no token file is present at all).
        primary = _token_path(config)
        legacy = _legacy_token_path(config)
        if not primary.exists() and not legacy.exists():
            # Bootstrap path — protected by _token_lock to prevent the
            # TOCTOU race where two concurrent publish processes both
            # observe "no token file" and each call createAccount.
            # Without the lock, the second writer's os.replace overwrites
            # the first writer's token; the first process then continues
            # with its in-memory (now-orphaned) token, hits 401, enters
            # self-heal, mints a third account — leaving the first
            # Telegraph account permanently inaccessible with no audit
            # trail.  Re-check existence inside the lock in case a peer
            # bootstrapped while we waited.
            with _token_lock(primary):
                if not primary.exists() and not legacy.exists():
                    short_name = _DEFAULT_SHORT_NAME
                    access_token = _create_account(short_name)
                    _write_token_atomic(
                        primary,
                        {"access_token": access_token, "short_name": short_name},
                    )
                    log.info(
                        "telegraph_token_bootstrapped short_name=%s", short_name
                    )
                else:
                    # Peer process bootstrapped while we waited on the lock;
                    # load the token they just wrote.
                    token_data = _load_token(config)
                    access_token = token_data["access_token"]
                    short_name = token_data.get("short_name", _DEFAULT_SHORT_NAME)
        else:
            # A file exists — load it (incl. legacy migration).  Any
            # DependencyError here (wrong perms / corrupt / missing field)
            # propagates so operators must fix it explicitly rather than
            # us silently minting a replacement account.
            token_data = _load_token(config)
            access_token = token_data["access_token"]
            short_name = token_data.get("short_name", _DEFAULT_SHORT_NAME)

        # 3. Try createPage once; on invalid-token, rotate + retry once.
        body = _create_page(access_token, title, nodes)

        if _is_invalid_token_error(body):
            access_token = self._rotate_and_get_new_token(config, short_name)
            body = _create_page(access_token, title, nodes)
            if _is_invalid_token_error(body):
                raise ExternalServiceError(
                    f"Telegraph rejected token after rotation: "
                    f"{body.get('error')}"
                )

        if not body.get("ok"):
            raise ExternalServiceError(
                f"Telegraph createPage rejected: {body.get('error', body)}"
            )

        # Defensive against unexpected response shape — see same pattern in
        # _create_account.  ``result.url`` is documented but a malformed
        # body would otherwise raise bare KeyError.
        result = body.get("result") or {}
        published_url = result.get("url")
        if not published_url:
            raise ExternalServiceError(
                f"Telegraph createPage returned malformed body: {body}"
            )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "telegraph_publish_done id=%s url=%s elapsed_ms=%d",
            article_id,
            published_url,
            elapsed_ms,
        )

        meta = {
            "anchors": stats.get("anchors", 0),
            "utf8_bytes": stats.get("utf8_bytes", 0),
            "downgrades": stats.get("downgrades", 0),
            "telegraph_path": result.get("path", ""),
        }

        if mode == "draft":
            # Telegraph has no native draft state; expose the URL as the
            # draft_url so the caller can review before announcing it.
            return AdapterResult(
                status="drafted",
                adapter="telegraph-api",
                platform="telegraph",
                draft_url=published_url,
                _provider_meta=meta,
            )
        return AdapterResult(
            status="published",
            adapter="telegraph-api",
            platform="telegraph",
            published_url=published_url,
            _provider_meta=meta,
        )

    def _rotate_and_get_new_token(self, config: Config, short_name: str) -> str:
        """401 recovery: archive old token, createAccount, write new.

        Held inside the file lock so concurrent rotations serialize and
        only one new account is created per actual rotation event.
        """
        token_path = _token_path(config)
        with _token_lock(token_path):
            archive_path = _archive_orphan_token(token_path)
            new_token = _create_account(short_name)
            _write_token_atomic(
                token_path,
                {"access_token": new_token, "short_name": short_name},
            )
            log.warning(
                "telegraph_token_rotated reason=401_self_heal "
                "old_token_archived_to=%s",
                archive_path,
            )
            return new_token


# ── verify_adapter_setup hook (called from adapters/__init__.py) ────────────


def verify_telegraph_setup(config: Config) -> None:
    """Confirm we can either load an existing token or bootstrap one.

    Per plan: do NOT probe createAccount endpoint reachability — that
    couples adapter health to network state and breaks the "low-friction
    anonymous" USP under captive portals / VPN gates.

    Token-absent is OK (publish() will bootstrap on demand).  Token
    present but corrupt / wrong perms is NOT OK — let the operator fix
    it explicitly rather than silently minting a replacement account.
    """
    primary = _token_path(config)
    legacy = _legacy_token_path(config)

    if primary.exists() or legacy.exists():
        # File exists — fully validate.  Any DependencyError propagates.
        _load_token(config)
        return

    # No token at all → ensure we can write one when publish() bootstraps.
    try:
        primary.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DependencyError(
            f"Cannot create telegraph token directory "
            f"{primary.parent}: {exc}"
        ) from None
