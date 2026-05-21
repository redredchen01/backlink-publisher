"""Publish validated backlink payloads via adapter dispatcher."""

from __future__ import annotations

import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_HTTP_5XX_RE = re.compile(r"\b5[0-9]{2}\b")

from backlink_publisher.publishing.adapters import publish as adapter_publish, verify_adapter_setup
from .. import checkpoint, config_echo
from backlink_publisher.config import load_config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    BannerUploadError,
    DependencyError,
    ExternalServiceError,
    emit_error,
)
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher.linkcheck.http import MAX_CONCURRENT as _LINKCHECK_MAX_CONCURRENT, check_url
from backlink_publisher._util.logger import publish_logger
from backlink_publisher.publishing.registry import registered_platforms
from ..schema import reject_unsupported_platform, supported_platforms, validate_publish_payload
from backlink_publisher.linkcheck.verify import verify_published
from contextlib import contextmanager


def _release_acquired_leases(store: Any, acquired: list[str], pid: int) -> None:
    for plat in acquired:
        try:
            store.release_lease(plat, pid)
        except Exception as e:
            publish_logger.warning(f"Failed to release lease on {plat!r}: {e}")


def _acquire_publish_leases(platforms: set[str], dry_run: bool) -> None:
    if dry_run or not platforms:
        return

    import atexit
    from backlink_publisher.events.store import EventStore
    store = EventStore()
    pid = os.getpid()
    acquired = []

    for plat in sorted(platforms):
        if store.acquire_lease(plat, pid, ttl_seconds=3600):
            acquired.append(plat)
        else:
            _release_acquired_leases(store, acquired, pid)
            lease_details = store.get_lease(plat)
            owner_info = f"PID {lease_details['owner_pid']}" if lease_details else "unknown"
            emit_error(
                f"error: another publish process ({owner_info}) is currently active for platform {plat!r}. "
                "Aborting to prevent concurrent publishing conflicts.",
                exit_code=3
            )

    atexit.register(_release_acquired_leases, store, acquired, pid)


#: First-run banner sentinel — written after the banner fires so subsequent
#: runs stay quiet. Bumping the version-tag forces a re-warn on future flag
#: changes per plan 2026-05-14-001 R10.
_GATE_BANNER_SENTINEL = (
    Path.home() / ".cache" / "backlink-publisher" / "v0.3-gate-banner-seen"
)
_GATE_BANNER_TEXT = (
    "publish-backlinks now performs a publish-time reachability re-check "
    "on every row before dispatch. Use --skip-publish-time-check to "
    "restore prior behavior. This message will not repeat (sentinel: "
    f"{_GATE_BANNER_SENTINEL})."
)


def _maybe_emit_gate_banner(skip_flag: bool) -> None:
    """Emit the one-shot upgrade WARN if the gate is on and not yet shown."""
    if skip_flag or _GATE_BANNER_SENTINEL.exists():
        return
    publish_logger.warn(_GATE_BANNER_TEXT)
    try:
        _GATE_BANNER_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
        _GATE_BANNER_SENTINEL.touch(exist_ok=True)
    except OSError:
        # If we can't write the sentinel the banner fires again next run —
        # noisy but never silent failure. Acceptable.
        pass


def _check_row_reachability(row: dict[str, Any]) -> tuple[bool, str | None]:
    """Return ``(True, None)`` if every URL in the row is reachable, else
    ``(False, failing_url)`` on the first observed failure.

    Per plan 2026-05-14-001 R9: per-row URLs dispatched in parallel via
    a ThreadPoolExecutor (reusing ``linkcheck.MAX_CONCURRENT`` budget) so
    worst-case per-row latency stays bounded by a single ``check_url`` call
    (~3s) rather than scaling with URL count.
    """
    urls = [row.get("target_url", "")]
    for link in row.get("links", []):
        if isinstance(link, dict):
            url = link.get("url")
            if url:
                urls.append(url)
    urls = [u for u in urls if u]
    if not urls:
        return True, None

    workers = min(_LINKCHECK_MAX_CONCURRENT, len(urls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(check_url, u): u for u in urls}
        first_failure: str | None = None
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                ok, _err = fut.result()
            except Exception:  # noqa: BLE001 — treat any exception as unreachable
                ok = False
            if not ok and first_failure is None:
                first_failure = url
                # Cancel remaining pending futures; finished ones run to completion.
                for other in futures:
                    if not other.done():
                        other.cancel()
                break
    if first_failure is not None:
        return False, first_failure
    return True, None


def _make_banner_emit() -> Any:
    """Return a callable that records banner-embed events.

    Plan 2026-05-20-004 Unit 1.  Lazily instantiates an ``EventStore``
    on first call so module import stays side-effect-free (tests that
    monkey-patch the config dir need the env var resolved at use time,
    not import time).  Also logs each event at the INFO level via
    ``publish_logger`` so operators tailing the WebUI log can see
    banner activity without opening the SQLite store.

    Returns a closure rather than a method so passing through to
    ``adapter_publish(banner_emit=...)`` keeps the signature minimal.
    """
    store_holder: dict[str, Any] = {}

    def _emit(kind: str, payload: dict[str, Any]) -> None:
        publish_logger.info(
            f"banner-embed: {kind} {payload}",
            extra={"banner_event": kind, **payload},
        )
        if "store" not in store_holder:
            from backlink_publisher.events.store import EventStore
            store_holder["store"] = EventStore()
        try:
            store_holder["store"].append(kind, payload)
        except Exception as exc:
            # EventStore write failures must NOT break the publish path.
            publish_logger.warning(
                f"banner-event EventStore.append({kind!r}) failed: {exc}"
            )

    return _emit


def _error_class(exc: Exception) -> str:
    from backlink_publisher.publishing.adapters.retry import classify_exception
    return classify_exception(exc).value


def _check_token_drift(initial_revs: dict[str, int]) -> None:
    from backlink_publisher.config import snapshot_token_revs
    current = snapshot_token_revs()
    for plat, init_rev in initial_revs.items():
        if current.get(plat, 0) != init_rev:
            emit_error(
                f"error: configuration for platform {plat!r} was updated mid-run. "
                "Aborting to prevent using revoked credentials.",
                exit_code=45
            )


def _do_verify(
    no_verify: bool,
    dry_run: bool,
    result: Any,
    row: dict[str, Any],
) -> tuple[bool, str]:
    """Run post-publish verification if enabled. Returns (ok, reason)."""
    if no_verify or dry_run:
        return True, ""
    verify_url = result.published_url or result.draft_url
    if not verify_url:
        return True, ""
    needs_extended_wait = getattr(result, "post_publish_delay_seconds", 0) > 0
    max_wait = 30 if needs_extended_wait else 10
    required_links = [lnk["url"] for lnk in row.get("links", []) if lnk.get("required")]
    vr = verify_published(
        verify_url,
        title=row.get("title", ""),
        required_link_urls=required_links,
        max_wait=max_wait,
    )
    return vr.ok, vr.reason


# ── Shared helpers ──────────────────────────────────────────────────────────


def _build_failure_row(
    status: str,
    row: dict[str, Any],
    platform: str,
    error: str,
    ts: str,
    *,
    adapter: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """Build a publish-failure output row. ``extra`` kwargs overlay additional fields."""
    out: dict[str, Any] = {
        "id": row.get("id", ""),
        "platform": platform,
        "status": status,
        "title": row.get("title", ""),
        "draft_url": "",
        "published_url": "",
        "created_at": ts,
        "adapter": adapter,
        "error": error,
    }
    out.update(extra)
    return out


def _try_update_ckpt_failed(
    run_id: str | None,
    row_id: str,
    error: str,
    error_class: str,
) -> str | None:
    """Try writing a failed checkpoint item. Returns ``None`` on error to disable further updates."""
    if run_id is None:
        return None
    try:
        checkpoint.update_item(run_id, row_id, "failed", error=error, error_class=error_class)
    except Exception as ckpt_exc:
        print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
        return None
    return run_id


def _load_throttle_config() -> tuple[int, int]:
    """Read MEDIUM_THROTTLE_MIN/MAX env vars with defaults 60/300."""
    return (
        int(os.environ.get("MEDIUM_THROTTLE_MIN", "60")),
        int(os.environ.get("MEDIUM_THROTTLE_MAX", "300")),
    )


def _sleep_with_throttle(throttle_min: int, throttle_max: int, context: str = "") -> None:
    """Sleep a random interval in [throttle_min, throttle_max] with a log line."""
    sleep_secs = random.uniform(throttle_min, throttle_max)
    label = f" ({context})" if context else ""
    publish_logger.info(f"throttle: sleeping {sleep_secs:.0f}s{label}")
    time.sleep(sleep_secs)


def _record_publish_failure(
    outputs: list[dict[str, Any]],
    row: dict[str, Any],
    platform: str,
    ts: str,
    run_id: str | None,
    exc: Exception,
    err_class: str,
    err_msg: str,
) -> str | None:
    """Append a failure row, log the error, and attempt checkpoint update."""
    outputs.append(_build_failure_row("failed", row, platform, err_msg, ts, adapter=platform))
    new_run_id = _try_update_ckpt_failed(run_id, row.get("id", ""), err_msg, err_class)
    publish_logger.error(
        f"publish failed: {exc}",
        extra={"id": row.get("id"), "platform": platform},
    )
    return new_run_id


def _medium_throttle_sleep(
    row_idx: int,
    last_success_idx: int,
    platform: str,
    throttle_min: int,
    throttle_max: int,
    *,
    dry_run: bool,
) -> None:
    if dry_run or row_idx == 0:
        return
    if last_success_idx != row_idx - 1 or platform != "medium":
        return
    _sleep_with_throttle(throttle_min, throttle_max, "next Medium post")


def _record_resume_failure(
    run_id: str,
    item: dict[str, Any],
    exc: Exception,
    err_class: str,
    err_msg: str,
) -> None:
    checkpoint.update_item(run_id, item["id"], "failed", error=err_msg, error_class=err_class)
    publish_logger.error(
        f"publish failed: {exc}",
        extra={"id": item["id"], "platform": item.get("platform", "")},
    )


def item_to_publish_output(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a done checkpoint item to the standard publish output shape."""
    article_urls = item.get("article_urls")
    if not isinstance(article_urls, list) or not article_urls:
        article_urls = [u for u in (
            item.get("published_url") or "",
            item.get("draft_url") or "",
        ) if u]
    return {
        "id": item.get("id", ""),
        "platform": item.get("platform", ""),
        "status": item.get("status", ""),
        "title": item.get("title", ""),
        "article_urls": article_urls,
        "draft_url": "",
        "published_url": item.get("published_url") or "",
        "created_at": item.get("completed_at", ""),
        "adapter": item.get("adapter") or "",
        "error": None,
    }


def _run_resume(args: Any) -> None:
    """Handle --resume <run_id>: load checkpoint, process pending/failed items, emit union output.

    Runs post-publish verification for newly published items unless --no-verify is set.
    """
    run_id = args.resume

    try:
        ckpt = checkpoint.load_checkpoint(run_id)
    except (ValueError, FileNotFoundError) as exc:
        emit_error(str(exc), exit_code=2)
        return

    config = load_config()
    banner_emit = _make_banner_emit()  # Plan 2026-05-20-004 Unit 1

    # verify adapter setup for platforms in checkpoint
    platforms_in_ckpt = {item["platform"] for item in ckpt["items"] if item.get("platform")}
    _acquire_publish_leases(platforms_in_ckpt, getattr(args, "dry_run", False))
    for plat in platforms_in_ckpt:
        if plat in supported_platforms():
            try:
                verify_adapter_setup(plat, config)
            except DependencyError as exc:
                emit_error(str(exc), exit_code=3)

    # R13: hard re-validate each pending/failed item against the current
    # gate (R2 body + R5 anchor) before resuming dispatch. Items whose
    # payload was created under the buggy language_matches and are now
    # contractually 'failed' get reclassified with a retro_* error_class
    # and skipped from this resume run. --list-runs displays the
    # error_class so the operator can see the reclassification.
    # See plan 2026-05-14-001 Unit 7.
    from .validate_backlinks import _enhance_payload  # local import to avoid cycle
    retro_lang = 0
    retro_anchor = 0
    for item in ckpt["items"]:
        if item["status"] not in ("pending", "failed"):
            continue
        # Skip items already classified retro_* (idempotent --resume).
        if item.get("error_class") in (
            checkpoint.RETRO_LANGUAGE_FAILED,
            checkpoint.RETRO_ANCHOR_FAILED,
        ):
            continue
        # Run validate-time gate against the stored payload. We pass the
        # live config — branded_pool TOCTOU is accepted for v1 per plan
        # Risks & Dependencies.
        re_validated = _enhance_payload(dict(item["payload"]), config)
        if re_validated["validation"]["status"] != "failed":
            continue
        errors_list = re_validated["validation"]["errors"]
        # First error wins the classification — body language errors are
        # listed before anchor errors per Unit 3's _enhance_payload order.
        if errors_list and errors_list[0].startswith("body language"):
            err_class = checkpoint.RETRO_LANGUAGE_FAILED
            retro_lang += 1
        else:
            err_class = checkpoint.RETRO_ANCHOR_FAILED
            retro_anchor += 1
        checkpoint.update_item(
            run_id,
            item["id"],
            "failed",
            error="; ".join(errors_list),
            error_class=err_class,
        )
        # Mutate the in-memory item too so the to_process filter below
        # sees the updated error_class.
        item["status"] = "failed"
        item["error_class"] = err_class

    if retro_lang or retro_anchor:
        publish_logger.info(
            f"resume: re-validated checkpoint — reclassified "
            f"{retro_lang} retro_language_failed + {retro_anchor} retro_anchor_failed"
        )

    to_process = [
        item for item in ckpt["items"]
        if item["status"] in ("pending", "failed")
        and item.get("error_class") not in (
            checkpoint.RETRO_LANGUAGE_FAILED,
            checkpoint.RETRO_ANCHOR_FAILED,
        )
    ]

    # warn on http_5xx items
    for item in to_process:
        if item.get("error_class") == "http_5xx":
            print(
                f"WARNING: item {item['id']} failed with HTTP 5xx — "
                f"post may already be live on {item['platform']}. Verify before resuming.",
                file=sys.stderr,
            )

    # all done: emit union and mark complete
    if not to_process:
        all_done = [item_to_publish_output(i) for i in ckpt["items"] if i["status"] == "done"]
        write_jsonl(all_done)
        sys.stdout.flush()
        checkpoint.mark_complete(run_id)
        raise SystemExit(0)

    # R8: find last done Medium item and compute elapsed
    throttle_min, throttle_max = _load_throttle_config()
    resume_elapsed_skip_throttle = False
    for item in ckpt["items"]:
        # Throttle bookkeeping for the resume path. Platform-keyed because at
        # checkpoint replay time we only have the persisted platform name, not
        # the AdapterResult.post_publish_delay_seconds value. Pairs with the
        # `if platform == "medium":` gate below — both are out-of-R9-scope
        # follow-ups (plan 2026-05-18-009 Unit 2 known-limitation).
        if item["status"] == "done" and item.get("platform") == "medium":
            try:
                last_ts = datetime.fromisoformat(item["completed_at"])
                elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
                if elapsed >= 300:
                    resume_elapsed_skip_throttle = True
                else:
                    resume_elapsed_skip_throttle = False
            except (ValueError, TypeError):
                pass

    first_medium_in_resume = True
    last_medium_success_idx = -1
    unverified_ids: set[str] = set()

    from backlink_publisher.config import snapshot_token_revs
    initial_token_revs = snapshot_token_revs()

    for item_idx, item in enumerate(to_process):
        row = item["payload"]
        platform = ckpt.get("platform") or row.get("platform", "")
        mode = ckpt.get("mode") or row.get("publish_mode", "draft")

        if platform == "medium":
            if first_medium_in_resume:
                if not resume_elapsed_skip_throttle:
                    _sleep_with_throttle(throttle_min, throttle_max, "resume first Medium post")
                first_medium_in_resume = False
            elif last_medium_success_idx == item_idx - 1:
                _sleep_with_throttle(throttle_min, throttle_max, "next Medium post")

        publish_logger.info(
            f"resume publishing: {platform} id={item['id']}",
            extra={"id": item["id"], "platform": platform},
        )

        try:
            _check_token_drift(initial_token_revs)
            result = adapter_publish(
                payload={**row, "platform": platform},
                mode=mode,
                config=config,
                dry_run=False,
                banner_emit=banner_emit,
            )
        except AuthExpiredError as exc:
            # Plan 2026-05-19-001 Unit 6: flip channel status to expired so
            # /settings shows the re-bind affordance, write the checkpoint
            # row with error_class="auth_expired", then exit 3 (same exit
            # semantic as the DependencyError parent class).
            try:
                from webui_store.channel_status import mark_expired
                mark_expired(exc.channel)
            except Exception as flip_exc:
                publish_logger.warning(
                    f"mark_expired({exc.channel!r}) failed: {flip_exc}"
                )
            try:
                checkpoint.update_item(
                    run_id, item["id"], "failed",
                    error=str(exc),
                    error_class="auth_expired",
                )
            except Exception as ckpt_exc:
                print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
            publish_logger.error(
                f"auth expired: {exc}",
                extra={"id": item["id"], "platform": platform},
            )
            emit_error(str(exc), exit_code=3)
            return
        except BannerUploadError as exc:
            # Plan 2026-05-20-004 Unit 1: strict-mode banner upload
            # failure → fail just this row, not the whole run.  Caught
            # BEFORE the generic DependencyError handler because
            # BannerUploadError IS a DependencyError subclass.
            _record_resume_failure(
                run_id, item, exc,
                "banner_upload", f"banner upload failed: {exc}",
            )
            continue
        except DependencyError as exc:
            emit_error(str(exc), exit_code=3)
            return
        except ExternalServiceError as exc:
            _record_resume_failure(
                run_id, item, exc,
                _error_class(exc), f"service error: {exc}",
            )
            continue
        except Exception as exc:
            _record_resume_failure(
                run_id, item, exc,
                "unexpected", f"unexpected error: {exc}",
            )
            continue

        completed_at = datetime.now(timezone.utc).isoformat()
        checkpoint.update_item(
            run_id, item["id"], "done",
            published_url=result.published_url,
            adapter=result.adapter,
            completed_at=completed_at,
        )
        if result.post_publish_delay_seconds > 0:
            last_medium_success_idx = item_idx

        # Post-publish verification
        row = item["payload"]
        verify_ok, verify_reason = _do_verify(
            getattr(args, "no_verify", False), False, result, row
        )
        if not verify_ok:
            unverified_ids.add(item["id"])
            publish_logger.warn(
                f"verification failed: id={item['id']} reason={verify_reason}",
                extra={"id": item["id"], "adapter": result.adapter},
            )

        publish_logger.info(
            f"published: id={item['id']} status={result.status}",
            extra={"id": item["id"], "status": result.status},
        )

    # build union output in original checkpoint order
    updated_ckpt = checkpoint.load_checkpoint(run_id)
    all_done = []
    for i in updated_ckpt["items"]:
        if i["status"] == "done":
            out = item_to_publish_output(i)
            if i["id"] in unverified_ids:
                out["status"] += "_unverified"
            all_done.append(out)
    write_jsonl(all_done)
    sys.stdout.flush()

    still_unfinished = [i for i in updated_ckpt["items"] if i["status"] in ("pending", "failed")]
    if not still_unfinished:
        checkpoint.mark_complete(run_id)
        if unverified_ids:
            for uid in unverified_ids:
                print(f"verification failed: id={uid}", file=sys.stderr)
            raise SystemExit(5)
        raise SystemExit(0)
    else:
        for f in still_unfinished:
            print(f"publish failed: {f.get('error', 'unknown error')}", file=sys.stderr)
        raise SystemExit(4)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="publish-backlinks",
        description="Publish validated backlink payloads.",
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Input JSONL file (default: stdin)",
    )
    parser.add_argument(
        "--platform",
        choices=registered_platforms(),
        default=None,
        help="Target platform (overrides per-row platform)",
    )
    parser.add_argument(
        "--mode",
        choices=["draft", "publish"],
        default="draft",
        help="Publish mode (default: draft)",
    )
    parser.add_argument(
        "--opencli-profile",
        default=None,
        help="Deprecated. Has no effect (OpenCLI removed).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print command plans without executing",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        default=False,
        help="Deprecated. Has no effect.",
    )
    parser.add_argument(
        "--log-level",
        default="WARN",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="Log verbosity (default: WARN)",
    )
    parser.add_argument(
        "--resume",
        default=None,
        metavar="RUN_ID",
        help="Resume an interrupted batch run from checkpoint",
    )
    parser.add_argument(
        "--list-runs",
        action="store_true",
        default=False,
        help="List incomplete checkpoint runs and exit",
    )
    parser.add_argument(
        "--cleanup",
        default=None,
        metavar="RUN_ID",
        help="Delete a specific checkpoint and exit",
    )
    parser.add_argument(
        "--cleanup-all",
        action="store_true",
        default=False,
        help="Delete all complete checkpoints and exit",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        default=False,
        help="Skip post-publish content verification (default: verify after each publish)",
    )
    parser.add_argument(
        "--skip-publish-time-check",
        action="store_true",
        default=False,
        help=(
            "Skip publish-time URL reachability re-check (default: re-check "
            "each row's target_url and links before dispatch). Per plan "
            "2026-05-14-001 R10: this is independent of validate-time's "
            "--no-validate-url-check; setting one does not affect the other."
        ),
    )
    args = parser.parse_args(argv)

    from backlink_publisher._util.logger import set_log_level
    set_log_level(args.log_level)

    # Mutual exclusion: only one of --resume, --list-runs, --cleanup, --cleanup-all
    exclusive = [args.resume, args.list_runs, args.cleanup, args.cleanup_all]
    if sum(bool(x) for x in exclusive) > 1:
        emit_error(
            "error: --resume, --list-runs, --cleanup, and --cleanup-all are mutually exclusive",
            exit_code=2,
        )

    # ── Housekeeping short-circuits ───────────────────────────────────────────

    if args.list_runs:
        runs = checkpoint.list_incomplete()
        if not runs:
            print("No incomplete runs.")
        else:
            print(f"{'RUN_ID':<32}  {'STARTED':<26}  {'PENDING':>7}  {'FAILED':>7}")
            print("-" * 76)
            for run in runs:
                pending = sum(1 for i in run["items"] if i["status"] == "pending")
                failed = sum(1 for i in run["items"] if i["status"] == "failed")
                print(f"{run['run_id']:<32}  {run.get('started_at', ''):<26}  {pending:>7}  {failed:>7}")
        raise SystemExit(0)

    if args.cleanup:
        try:
            checkpoint.delete(args.cleanup)
            print(f"Deleted checkpoint: {args.cleanup}")
        except (ValueError, FileNotFoundError) as exc:
            emit_error(str(exc), exit_code=2)
        raise SystemExit(0)

    if args.cleanup_all:
        count = checkpoint.delete_complete()
        print(f"Deleted {count} complete checkpoint(s).")
        raise SystemExit(0)

    # ── Resume path ───────────────────────────────────────────────────────────

    if args.resume:
        _run_resume(args)
        return  # _run_resume raises SystemExit; this is unreachable

    publish_logger.info("publish-backlinks started", extra={
        "platform": args.platform,
        "mode": args.mode,
        "dry_run": args.dry_run,
    })

    # One-shot upgrade WARN if the new gate is active and hasn't been
    # acknowledged yet (sentinel file). Suppressed for dry-run since
    # dry-run skips the gate anyway.
    if not args.dry_run:
        _maybe_emit_gate_banner(args.skip_publish_time_check)

    try:
        rows = list(read_jsonl(args.input))
    except SystemExit as exc:
        raise SystemExit(exc.code)

    publish_logger.info(f"processing {len(rows)} payloads")

    config = load_config()

    # Config Echo Chamber (Round-3 #7): emit a 4-line banner so operators
    # see which config was actually resolved + env overrides + SHA.
    config_echo.emit_banner(config, "publish-backlinks")

    # Pre-flight: validate all payloads and check for unsupported platforms
    for idx, row in enumerate(rows, start=1):
        platform = args.platform or row.get("platform", "")
        platform_msg = reject_unsupported_platform(platform)
        if platform_msg is not None:
            emit_error(f"row {idx}: {platform_msg}", exit_code=2)
        errs = validate_publish_payload(row)
        if errs:
            for e in errs:
                print(f"row {idx}: {e}", file=sys.stderr)
            raise SystemExit(2)

    # Verify adapter setup (unless dry-run)
    if not args.dry_run:
        platforms_in_use = {
            args.platform or row.get("platform", "") for row in rows
        }
        _acquire_publish_leases(platforms_in_use, False)
        for plat in platforms_in_use:
            if plat not in supported_platforms():
                continue
            try:
                verify_adapter_setup(plat, config)
            except DependencyError as exc:
                emit_error(str(exc), exit_code=3)

    # Create checkpoint (after pre-flight passes, skipped for dry-run)
    run_id: str | None = None
    if not args.dry_run:
        try:
            run_id, _ = checkpoint.create_checkpoint(
                rows,
                platform=args.platform,
                mode=args.mode,
                flags={
                    "skip_publish_time_check": args.skip_publish_time_check,
                },
            )
            print(f"publish-backlinks: run_id={run_id}", file=sys.stderr, flush=True)
        except Exception as exc:
            print(
                f"[WARN] checkpoint not created — this run cannot be resumed: {exc}",
                file=sys.stderr,
            )

    outputs: list[dict[str, Any]] = []
    ts = datetime.now(timezone.utc).isoformat()
    success_count = 0
    fail_count = 0
    banner_emit = _make_banner_emit()  # Plan 2026-05-20-004 Unit 1
    skipped_unreachable_count = 0
    last_medium_success_idx: int = -1

    throttle_min, throttle_max = _load_throttle_config()

    from backlink_publisher.config import snapshot_token_revs
    initial_token_revs = snapshot_token_revs()

    for row_idx, row in enumerate(rows):
        _medium_throttle_sleep(
            row_idx, last_medium_success_idx,
            args.platform or row.get("platform", ""),
            throttle_min, throttle_max,
            dry_run=args.dry_run,
        )

        platform = args.platform or row.get("platform", "")
        mode = args.mode or row.get("publish_mode", "draft")

        # R8/R9: publish-time per-row reachability re-check immediately
        # before adapter_publish. Skipped for dry-run (no real publish to
        # protect) and when --skip-publish-time-check is set. On any URL
        # failure the row is skipped (status=skipped_unreachable), the
        # checkpoint item stays in 'pending' so --resume can retry, and
        # the batch continues to the next row.
        if not args.dry_run and not args.skip_publish_time_check:
            ok, failing_url = _check_row_reachability(row)
            if not ok:
                row_id = row.get("id", "")
                publish_logger.warn(
                    f"[publish-backlinks] row_id={row_id} "
                    f"status=skipped_unreachable url={failing_url}"
                )
                outputs.append(_build_failure_row(
                    "skipped_unreachable", row, platform,
                    f"target unreachable at publish-time: {failing_url}",
                    ts,
                    failing_url=failing_url,
                ))
                skipped_unreachable_count += 1
                # Leave checkpoint item in 'pending' so --resume retries.
                continue

        if platform not in supported_platforms():
            outputs.append(_build_failure_row(
                "failed", row, platform,
                f"unsupported platform: {platform}",
                ts, adapter=platform,
            ))
            fail_count += 1
            continue

        # Dry run
        if args.dry_run:
            result = adapter_publish(
                payload={**row, "platform": platform},
                mode=mode,
                config=config,
                dry_run=True,
            )
            outputs.append({
                "id": row.get("id", ""),
                "platform": platform,
                "status": result.status,
                "title": row.get("title", ""),
                "draft_url": result.draft_url,
                "published_url": result.published_url,
                "created_at": ts,
                "adapter": result.adapter,
                "error": None,
                "_dry_run": True,
                "_command": result._command,
            })
            success_count += 1
            publish_logger.debug(
                f"dry-run: {platform} id={row.get('id', '')}",
                extra={"id": row.get("id"), "platform": platform},
            )
            continue

        publish_logger.info(
            f"publishing: {platform} id={row.get('id', '')}",
            extra={"id": row.get("id"), "platform": platform, "mode": mode},
        )

        try:
            _check_token_drift(initial_token_revs)
            result = adapter_publish(
                payload={**row, "platform": platform},
                mode=mode,
                config=config,
                dry_run=False,
                banner_emit=banner_emit,
            )
        except AuthExpiredError as exc:
            # Plan 2026-05-19-001 Unit 6: see resume-mode handler above for
            # the same shape — flip → checkpoint → log → exit 3.
            try:
                from webui_store.channel_status import mark_expired
                mark_expired(exc.channel)
            except Exception as flip_exc:
                publish_logger.warning(
                    f"mark_expired({exc.channel!r}) failed: {flip_exc}"
                )
            if run_id is not None:
                try:
                    checkpoint.update_item(
                        run_id, row.get("id", ""), "failed",
                        error=str(exc),
                        error_class="auth_expired",
                    )
                except Exception as ckpt_exc:
                    print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
            publish_logger.error(
                f"auth expired: {exc}",
                extra={"id": row.get("id"), "platform": platform},
            )
            emit_error(str(exc), exit_code=3)
            return  # unreachable but satisfies type checker
        except BannerUploadError as exc:
            # Plan 2026-05-20-004 Unit 1: strict-mode banner upload
            # failure → fail just this row, not the whole run.
            # Caught BEFORE the generic DependencyError handler
            # because BannerUploadError IS a DependencyError subclass.
            fail_count += 1
            run_id = _record_publish_failure(
                outputs, row, platform, ts, run_id, exc,
                "banner_upload", f"banner upload failed: {exc}",
            )
            continue
        except DependencyError as exc:
            emit_error(str(exc), exit_code=3)
            return  # unreachable but satisfies type checker
        except ExternalServiceError as exc:
            fail_count += 1
            run_id = _record_publish_failure(
                outputs, row, platform, ts, run_id, exc,
                _error_class(exc), f"service error: {exc}",
            )
            continue
        except Exception as exc:
            fail_count += 1
            run_id = _record_publish_failure(
                outputs, row, platform, ts, run_id, exc,
                "unexpected", f"unexpected error: {exc}",
            )
            continue

        outputs.append(result.to_publish_output(row, ts))
        if result.error:
            fail_count += 1
        else:
            success_count += 1
            if result.post_publish_delay_seconds > 0:
                last_medium_success_idx = row_idx

            # Post-publish verification
            verify_ok, verify_reason = _do_verify(
                args.no_verify, args.dry_run, result, row
            )
            if not verify_ok:
                outputs[-1]["status"] += "_unverified"
                publish_logger.warn(
                    f"verification failed: id={row.get('id', '')} reason={verify_reason}",
                    extra={"id": row.get("id"), "adapter": result.adapter},
                )

            if run_id is not None:
                try:
                    checkpoint.update_item(
                        run_id, row.get("id", ""), "done",
                        published_url=result.published_url,
                        article_urls=[u for u in (result.published_url, result.draft_url) if u],
                        adapter=result.adapter,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )
                except Exception as ckpt_exc:
                    print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
                    run_id = None
            publish_logger.info(
                f"published: id={row.get('id', '')} status={result.status}",
                extra={"id": row.get("id"), "status": result.status},
            )

    # Only output successful results to stdout
    successful = [r for r in outputs if r.get("error") is None]
    failed = [r for r in outputs if r.get("error") is not None]
    unverified = [r for r in successful if r.get("status", "").endswith("_unverified")]

    # Silent-Drop Tripwire reconciliation — emitted BEFORE any exit guard so
    # both happy and failure paths surface the input→output delta.
    # `input_payloads` is the total payloads read; `output_rows` is rows
    # written to stdout (= successful); other buckets count drops by reason.
    publish_logger.recon(
        "publish_reconciliation",
        input_payloads=len(rows),
        output_rows=len(successful),
        delta=len(rows) - len(successful),
        dropped={
            "failed": len(failed),
            "unverified": len(unverified),
        },
        dropped_ids={
            "failed": [r.get("id", "") for r in failed],
            "unverified": [r.get("id", "") for r in unverified],
        },
    )

    if successful:
        write_jsonl(successful)

    if failed:
        for f in failed:
            print(f"publish failed: {f['error']}", file=sys.stderr)
        raise SystemExit(4)

    if not args.dry_run and not successful:
        emit_error("no payloads were published", exit_code=5)

    if unverified:
        for u in unverified:
            print(
                f"verification failed: id={u.get('id', '')} status={u.get('status', '')}",
                file=sys.stderr,
            )
        raise SystemExit(5)

    publish_logger.info(
        f"publish complete: {success_count} succeeded, {fail_count} failed, "
        f"{skipped_unreachable_count} skipped_unreachable",
        extra={
            "success": success_count,
            "failed": fail_count,
            "skipped_unreachable": skipped_unreachable_count,
        },
    )


if __name__ == "__main__":
    main()
