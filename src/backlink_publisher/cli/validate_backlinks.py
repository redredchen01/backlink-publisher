"""Validate planned backlink payloads with structured logging."""

from __future__ import annotations

import sys
from typing import Any

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before validation
from .. import config_echo
from .._util import errors
from backlink_publisher._util.errors import InputValidationError
from backlink_publisher.config import Config, load_config
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher.linkcheck.http import check_urls_strict
from backlink_publisher.publishing.content_negotiation import route_tier_for
from backlink_publisher._util.logger import validate_logger
from ..schema import _is_field_present, reject_unsupported_platform, validate_output_payload

# Re-export symbols from extracted sub-module so any external callers (tests,
# downstream scripts) can still import them from validate_backlinks directly.
from ._validate_payload import (  # noqa: F401
    _HrefCollector,
    _extract_hrefs_from_html,
    _check_main_domain_in_html,
    _resolve_branded_pool,
    _nfc_normalize_in_place,
    _detect_row_body_language,
    _enhance_payload,
)


def _row_field_text(row: dict[str, Any], field: str) -> str:
    """Read a row field as a string, treating non-strings as empty."""
    value = row.get(field, "")
    return value if isinstance(value, str) else ""


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="validate-backlinks",
        description="Validate planned backlink payloads.",
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Input JSONL file (default: stdin)",
    )
    parser.add_argument(
        "--no-validate-url-check",
        action="store_true",
        default=False,
        dest="no_validate_url_check",
        help="Skip URL reachability checks at validate-time",
    )
    parser.add_argument(
        "--no-check-urls",
        action="store_true",
        default=False,
        dest="no_validate_url_check_legacy",
        help=(
            "DEPRECATED alias for --no-validate-url-check. "
            "Will be removed in a future version."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="WARN",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="Log verbosity (default: WARN)",
    )
    args = parser.parse_args(argv)

    from backlink_publisher._util.logger import set_log_level
    set_log_level(args.log_level)

    validate_logger.info("validate-backlinks started")

    # R10: --no-check-urls remains as a deprecated alias for back-compat.
    # Either flag set => URL checks disabled.
    if args.no_validate_url_check_legacy and not args.no_validate_url_check:
        validate_logger.warn(
            "--no-check-urls is deprecated; use --no-validate-url-check. "
            "Will be removed in a future version."
        )
    check_urls = not (args.no_validate_url_check or args.no_validate_url_check_legacy)

    # R4 branded-pool fallback source. Failure here is non-fatal — payload-first
    # snapshot from plan-backlinks is the primary source; missing config just
    # disables the live fallback.
    config: Config | None = None
    try:
        config = load_config()
    except InputValidationError:
        raise  # cells.py fail-loud contract: unknown channel / overlap must surface
    except Exception as exc:  # noqa: BLE001 — other config-load failures are tolerated
        validate_logger.warn(
            f"config load failed ({exc}); branded_pool fallback disabled, "
            "relying on payload-emitted snapshots only"
        )

    # Config Echo Chamber (Round-3 #7): emit a 4-line banner so operators
    # see which config was actually resolved + env overrides + SHA.
    if config is not None:
        config_echo.emit_banner(config, "validate-backlinks")

    try:
        rows = list(read_jsonl(args.input))
    except SystemExit as exc:
        raise SystemExit(exc.code)

    validate_logger.info(f"validating {len(rows)} payloads")

    if check_urls:
        all_urls = set()
        for row in rows:
            all_urls.add(row.get("target_url", ""))
            all_urls.add(row.get("main_domain", ""))
            for link in row.get("links", []):
                all_urls.add(link.get("url", ""))
            # Plan 2026-05-18-006 Unit 6 + pass-2 security P1: also include
            # <a href> URLs from content_html in the reachability scan.
            # Closes the symmetric-coverage gap between content_markdown
            # (URLs found inline) and content_html sources, so a HTML row
            # can't ship dead/malicious-redirect links that a markdown row
            # would have caught.
            html = row.get("content_html")
            if isinstance(html, str) and html.strip():
                for href in _extract_hrefs_from_html(html):
                    href = href.strip()
                    # Only http(s) URLs are reachable; other schemes (data:,
                    # javascript:, etc.) are rejected by R3 elsewhere.
                    if href.startswith(("http://", "https://")):
                        all_urls.add(href)
        all_urls.discard("")

        if all_urls:
            try:
                check_urls_strict(list(all_urls))
            except errors.ExternalServiceError as exc:
                validate_logger.error(f"URL check failed: {exc}")
                raise SystemExit(4) from None

    outputs: list[dict[str, Any]] = []
    all_errors: list[str] = []
    # Silent-Drop Tripwire — partition drops by gate so the reconciliation
    # line tells the operator exactly where each row vanished.
    platform_drops: list[int] = []
    validation_drops: list[int] = []

    for idx, row in enumerate(rows, start=1):
        # Check for unsupported platforms (post-R9d: helper covers any
        # unregistered platform, not just linkedin)
        platform = row.get("platform", "")
        platform_msg = reject_unsupported_platform(platform)
        if platform_msg is not None:
            all_errors.append(f"row {idx}: {platform_msg}")
            platform_drops.append(idx)
            continue

        # Plan 2026-05-18-006 Unit 6 R10: tier (b)/(c) content_html-only
        # gate. Runs as the next check after the platform-enum guard. A
        # content_html-only row destined for a platform whose route is not
        # tier (a) is rejected here — closes the silent-empty-publish risk
        # where the adapter would receive an empty content_markdown.
        if (
            _is_field_present(row.get("content_html"))
            and not _is_field_present(row.get("content_markdown"))
            and route_tier_for(platform) != "a"
        ):
            all_errors.append(
                f"row {idx}: platform '{platform}' does not yet accept "
                f"content_html (only markdown). Provide content_markdown or "
                f"wait for adapter retrofit."
            )
            platform_drops.append(idx)
            continue

        errs = validate_output_payload(row)
        if errs:
            all_errors.extend(f"row {idx}: {e}" for e in errs)
            validation_drops.append(idx)
            continue
        enhanced = _enhance_payload(row, config)
        if enhanced["validation"]["status"] == "failed":
            # R2/R5 row-level abort: don't forward to stdout; surface errors to stderr.
            for err in enhanced["validation"]["errors"]:
                all_errors.append(f"row {idx}: {err}")
            continue
        outputs.append(enhanced)

    # R2/R5: per-row skip semantic — passing rows STILL stream to stdout
    # so downstream consumers see partial success; exit code reflects overall
    # success only when zero rows failed. Schema/platform-level failures
    # (which already populated all_errors before _enhance_payload) follow
    # the same per-row pattern under the new contract.
    failed_count = len(rows) - len(outputs)
    write_jsonl(outputs)

    # Emit Silent-Drop Tripwire reconciliation BEFORE the exit guard so failed
    # runs still surface a delta summary.
    validate_logger.recon(
        "validate_reconciliation",
        input_rows=len(rows),
        output_rows=len(outputs),
        delta=len(rows) - len(outputs),
        dropped={
            "platform": len(platform_drops),
            "validation": len(validation_drops),
        },
        dropped_row_indices={
            "platform": platform_drops,
            "validation": validation_drops,
        },
    )

    if all_errors:
        for err in all_errors:
            print(f"validation error: {err}", file=sys.stderr)
        validate_logger.error(
            f"validation failed: {len(all_errors)} errors "
            f"({len(outputs)} passed, {failed_count} failed)"
        )
        raise SystemExit(2)

    validate_logger.info(
        f"validated {len(outputs)} payloads "
        f"({len(outputs)} passed, {failed_count} failed)"
    )


if __name__ == "__main__":
    main()
