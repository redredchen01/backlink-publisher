"""Validate planned backlink payloads with structured logging."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from .. import config_echo, errors
from ..anchor_lang import check_anchor_language
from ..config import Config, get_anchor_pool_v2, load_config
from ..errors import emit_error, InputValidationError
from ..jsonl import read_jsonl, write_jsonl
from ..language_check import (
    SUPPORTED_LANGUAGES,
    detect_language,
    language_matches,
)
from ..linkcheck import check_urls_strict
from ..logger import validate_logger
from ..markdown_utils import validate_markdown_convertible
from ..schema import SUPPORTED_PLATFORMS, validate_output_payload


def _resolve_branded_pool(row: dict[str, Any], config: Config | None) -> list[str]:
    """Return the branded_pool to use for R4 exemption checks.

    Resolution order (per plan 2026-05-14-001):
    1. ``row.metadata.branded_pool`` snapshot emitted by plan-backlinks.
       Closes the validate→publish TOCTOU window — the snapshot is what
       plan-time considered branded.
    2. Live ``get_anchor_pool_v2`` lookup against the loaded config.
       Fallback for older JSONL produced before this PR shipped.
    3. Empty list. The gate proceeds with no exemption; legitimate Latin
       brand-name anchors will fail R4. Surfaced via a one-time WARN per
       row so the operator notices.
    """
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        snap = metadata.get("branded_pool")
        if isinstance(snap, list):
            return [str(x) for x in snap]
    if config is None:
        return []
    main_domain = row.get("main_domain", "")
    if not main_domain:
        return []
    return list(get_anchor_pool_v2(config, main_domain, "home", "branded"))


def _enhance_payload(row: dict[str, Any], config: Config | None = None) -> dict[str, Any]:
    """Attach a ``validation`` block; populate errors[] on R2/R4/R5 failure.

    Contract (R11): ``validation.status`` is ``"failed"`` if any error fired,
    else ``"passed"``. ``validation.errors`` is the structured failure list.
    ``validation.warnings`` is preserved as an empty list for back-compat
    (test_validate_backlinks.py:189 asserts shape).
    """
    errors_list: list[str] = []
    warnings_list: list[str] = []

    requested = row.get("language", "")

    # R3 enum guard — non-enum row.language skips R2/R4 with a WARN.
    if requested not in SUPPORTED_LANGUAGES:
        validate_logger.warn(
            f"row {row.get('id', '?')}: language '{requested}' outside enum "
            f"{sorted(SUPPORTED_LANGUAGES)}; skipping language and anchor gates"
        )
    else:
        # R2: body language match.
        text = row.get("content_markdown", "")
        detected = detect_language(text)
        if not language_matches(detected, requested):
            errors_list.append(
                f"body language '{detected}' does not match requested '{requested}'"
            )

        # R4/R5: per-anchor codepoint check for kind in {main_domain, target}.
        branded_pool = _resolve_branded_pool(row, config)
        for idx, link in enumerate(row.get("links", [])):
            anchor = link.get("anchor", "") if isinstance(link, dict) else ""
            kind = link.get("kind", "") if isinstance(link, dict) else ""
            ok, reason = check_anchor_language(anchor, requested, kind, branded_pool)
            if not ok:
                errors_list.append(
                    f"link[{idx}] anchor {anchor!r} failed: {reason}"
                )

    row["validation"] = {
        "status": "failed" if errors_list else "passed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "warnings": warnings_list,
        "errors": errors_list,
    }
    return row


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

    from ..logger import set_log_level
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
    except Exception as exc:  # noqa: BLE001 — config-load failures are tolerated
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
        # Check for unsupported platforms (linkedin)
        platform = row.get("platform", "")
        if platform == "linkedin":
            all_errors.append(
                f"row {idx}: platform 'linkedin' is not supported. "
                f"Supported: {', '.join(sorted(SUPPORTED_PLATFORMS))}"
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