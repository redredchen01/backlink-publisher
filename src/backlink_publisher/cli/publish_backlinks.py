"""Publish validated backlink payloads via adapter dispatcher."""

from __future__ import annotations

import os
import random
import sys
import time
from datetime import datetime, timezone
from typing import Any

_MEDIUM_ADAPTERS = {"medium-api", "medium-browser"}

from ..adapters import publish as adapter_publish, verify_adapter_setup
from ..config import load_config
from ..errors import DependencyError, ExternalServiceError, emit_error
from ..jsonl import read_jsonl, write_jsonl
from ..logger import publish_logger
from ..schema import SUPPORTED_PLATFORMS, validate_publish_payload
from ..verifier import (
    _ERR_INTERNAL_PREFIX,
    _safe_for_log,
    verify_published,
)


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
        choices=["blogger", "medium"],
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
    args = parser.parse_args(argv)

    from ..logger import set_log_level
    set_log_level(args.log_level)

    publish_logger.info("publish-backlinks started", extra={
        "platform": args.platform,
        "mode": args.mode,
        "dry_run": args.dry_run,
    })

    try:
        rows = list(read_jsonl(args.input))
    except SystemExit as exc:
        raise SystemExit(exc.code)

    publish_logger.info(f"processing {len(rows)} payloads")

    config = load_config()

    # Pre-flight: validate all payloads and check for unsupported platforms
    for idx, row in enumerate(rows, start=1):
        platform = args.platform or row.get("platform", "")
        if platform == "linkedin":
            emit_error(
                f"row {idx}: platform 'linkedin' is not supported. "
                f"Supported platforms: blogger, medium",
                exit_code=2,
            )
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
        for plat in platforms_in_use:
            if plat not in SUPPORTED_PLATFORMS:
                continue
            try:
                verify_adapter_setup(plat, config)
            except DependencyError as exc:
                emit_error(str(exc), exit_code=3)

    outputs: list[dict[str, Any]] = []
    ts = datetime.now(timezone.utc).isoformat()
    success_count = 0
    fail_count = 0
    last_medium_success_idx: int = -1
    # Highest non-verification failure exit code seen. DependencyError mid-loop
    # now logs and continues (mirrors the completed adapter-retry plan) instead
    # of raising — this lets max() reach the final verification exit code.
    max_failure_code = 0
    # Verification tallies for the R17 stderr summary and exit-code accounting.
    verified_true_count = 0
    verified_false_count = 0
    verified_null_count = 0
    verifier_internal_error_count = 0
    # Lazy-built Blogger service object shared with the verifier. The
    # publisher's blogger_api.publish() and the verifier both go through
    # adapters.blogger_api._get_service so credentials are not re-fetched.
    _blogger_service_cache: dict[str, Any] = {}

    def _get_blogger_service() -> Any:
        # Only cache successful builds. A transient OAuth/network failure on
        # the first Blogger row must not poison the entire batch — the next
        # row gets another attempt. (Review-fix: review-finding adv-3 /
        # reliability-3 / correctness-2.)
        if "service" in _blogger_service_cache:
            return _blogger_service_cache["service"]
        try:
            from ..adapters.blogger_api import _get_service as _bs
            service = _bs(config)
        except Exception as exc:  # noqa: BLE001 — verifier handles missing service
            publish_logger.warning(
                f"could not build Blogger service for verifier: "
                f"{type(exc).__name__}: {_safe_for_log(str(exc))}"
            )
            return None
        _blogger_service_cache["service"] = service
        return service

    throttle_min = int(os.environ.get("MEDIUM_THROTTLE_MIN", "60"))
    throttle_max = int(os.environ.get("MEDIUM_THROTTLE_MAX", "300"))

    for row_idx, row in enumerate(rows):
        # Throttle: sleep between Medium rows when previous was a successful Medium publish
        if (
            not args.dry_run
            and row_idx > 0
            and last_medium_success_idx == row_idx - 1
        ):
            platform_next = args.platform or row.get("platform", "")
            if platform_next == "medium":
                sleep_secs = random.uniform(throttle_min, throttle_max)
                publish_logger.info(f"throttle: sleeping {sleep_secs:.0f}s before next Medium post")
                time.sleep(sleep_secs)

        platform = args.platform or row.get("platform", "")
        mode = args.mode or row.get("publish_mode", "draft")

        if platform not in SUPPORTED_PLATFORMS:
            outputs.append({
                "id": row.get("id", ""),
                "platform": platform,
                "status": "failed",
                "title": row.get("title", ""),
                "draft_url": "",
                "published_url": "",
                "created_at": ts,
                "adapter": f"{platform}",
                "error": f"unsupported platform: {platform}",
                "verified": None,
                "verified_at": None,
                "verification_error": None,
            })
            fail_count += 1
            max_failure_code = max(max_failure_code, 4)
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
                "verified": None,
                "verified_at": None,
                "verification_error": "dry_run",
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
            result = adapter_publish(
                payload={**row, "platform": platform},
                mode=mode,
                config=config,
                dry_run=False,
            )
        except DependencyError as exc:
            # Log-and-continue (was: emit_error(3) mid-loop). Letting the
            # loop continue lets max() reach the verification exit code 4
            # rather than masking it under a hard exit 3. Mirrors how the
            # already-completed adapter-retry plan treats
            # ExternalServiceError.
            safe_msg = _safe_for_log(str(exc))
            outputs.append({
                "id": row.get("id", ""),
                "platform": platform,
                "status": "failed",
                "title": row.get("title", ""),
                "draft_url": "",
                "published_url": "",
                "created_at": ts,
                "adapter": f"{platform}",
                "error": f"dependency error: {safe_msg}",
                "verified": None,
                "verified_at": None,
                "verification_error": None,
            })
            fail_count += 1
            max_failure_code = max(max_failure_code, 3)
            publish_logger.error(
                f"dependency error: {exc}",
                extra={"id": row.get("id"), "platform": platform},
            )
            continue
        except ExternalServiceError as exc:
            safe_msg = _safe_for_log(str(exc))
            outputs.append({
                "id": row.get("id", ""),
                "platform": platform,
                "status": "failed",
                "title": row.get("title", ""),
                "draft_url": "",
                "published_url": "",
                "created_at": ts,
                "adapter": f"{platform}",
                "error": f"service error: {safe_msg}",
                "verified": None,
                "verified_at": None,
                "verification_error": None,
            })
            fail_count += 1
            max_failure_code = max(max_failure_code, 4)
            publish_logger.error(
                f"publish failed: {exc}",
                extra={"id": row.get("id"), "platform": platform},
            )
            continue
        except Exception as exc:
            safe_msg = _safe_for_log(str(exc))
            outputs.append({
                "id": row.get("id", ""),
                "platform": platform,
                "status": "failed",
                "title": row.get("title", ""),
                "draft_url": "",
                "published_url": "",
                "created_at": ts,
                "adapter": f"{platform}",
                "error": f"unexpected error: {safe_msg}",
                "verified": None,
                "verified_at": None,
                "verification_error": None,
            })
            fail_count += 1
            max_failure_code = max(max_failure_code, 4)
            publish_logger.error(
                f"publish failed: {exc}",
                extra={"id": row.get("id"), "platform": platform},
            )
            continue

        output_dict = result.to_publish_output(row, ts)

        # Real-publish verification (R1, R2, R4). The verifier wraps its own
        # internal exceptions as verifier_internal_error: outcomes — it can
        # never abort the batch.
        if result.status == "published":
            service = _get_blogger_service() if result.adapter == "blogger-api" else None
            outcome = verify_published(row, result, service=service)
            output_dict["verified"] = outcome.verified
            output_dict["verified_at"] = outcome.verified_at
            output_dict["verification_error"] = outcome.verification_error

            # R16 per-row stderr line. Every string passing through here
            # has been sanitised by either the verifier or _safe_for_log
            # to strip CR/LF and cap length.
            verified_label = (
                "true" if outcome.verified is True
                else "false" if outcome.verified is False
                else "null"
            )
            safe_url = _safe_for_log(result.published_url or "<no-url>")
            safe_reason = _safe_for_log(outcome.verification_error or "-")
            print(
                f"verified={verified_label} {safe_url} [{safe_reason}]",
                file=sys.stderr,
            )

            err_str = outcome.verification_error or ""
            if outcome.verified is True:
                verified_true_count += 1
            elif outcome.verified is False:
                verified_false_count += 1
            elif err_str.startswith(_ERR_INTERNAL_PREFIX):
                verifier_internal_error_count += 1
            else:
                # Every published row lands in exactly one bucket — drop
                # the guarded `elif err_str:` so a future verifier change
                # that emits (None, None, None) for an unrecognised skip
                # path still surfaces in the summary.
                verified_null_count += 1

        outputs.append(output_dict)
        if result.error:
            fail_count += 1
            max_failure_code = max(max_failure_code, 4)
        else:
            success_count += 1
            if result.adapter in _MEDIUM_ADAPTERS:
                last_medium_success_idx = row_idx
            publish_logger.info(
                f"published: id={row.get('id', '')} status={result.status}",
                extra={"id": row.get("id"), "status": result.status},
            )

    # Successful rows (including those with verified=false) go to stdout —
    # preserves the audit trail. Adapter-failed rows go to stderr as a
    # text-block + feed into max_failure_code.
    successful = [r for r in outputs if r.get("error") is None]
    failed = [r for r in outputs if r.get("error") is not None]

    if successful:
        write_jsonl(successful)

    for f in failed:
        safe_err = _safe_for_log(f.get("error") or "")
        print(f"publish failed: {safe_err}", file=sys.stderr)

    # R17 run-end summary. V1 simplified shape (lag_ratio + Medium-tagging
    # dropped per scope-guardian trim). Internal-error count surfaces only
    # when non-zero so a clean run shows three counters. Suppressed on
    # empty batches — emitting "0/0/0" before the no-payloads exit-5
    # message is just noise.
    if outputs:
        summary = (
            f"verification: {verified_true_count} verified, "
            f"{verified_false_count} unverified (verified=false), "
            f"{verified_null_count} null (verified=null)"
        )
        if verifier_internal_error_count > 0:
            summary += f" ({verifier_internal_error_count} internal-error)"
        print(summary, file=sys.stderr)

    publish_logger.info(
        f"publish complete: {success_count} succeeded, {fail_count} failed",
        extra={"success": success_count, "failed": fail_count},
    )

    # Verifier-internal-error rows count as verified=false for exit-code
    # purposes (saturation guard — prevents a verifier regression from
    # silently muting the defence). Their JSONL field stays verified=null.
    verified_false_for_exit = verified_false_count + verifier_internal_error_count

    # R15 — max() rule. DependencyError (3) and ExternalServiceError (4)
    # both feed max_failure_code; verification failure adds 4. The empty-
    # batch exit-5 path is preserved.
    final_code = max(max_failure_code, 4 if verified_false_for_exit > 0 else 0)

    if final_code == 0 and not args.dry_run and not successful:
        emit_error("no payloads were published", exit_code=5)

    if final_code != 0:
        raise SystemExit(final_code)
