"""Pure validate-backlinks engine — no process-global side effects.

Thin-WebUI Phase 2 Unit 6 (plan ``2026-05-27-004``). Extracted from
``cli/validate_backlinks.py`` so the CLI shell and the in-process ``PipelineAPI``
bridge share one validation kernel and produce identical data + typed errors.
Follows the ``ledger.aggregate.build_ledger`` engine/shell/in-process precedent.

This module is PURE compute. It MUST NOT:
- touch ``sys.stdout`` / ``sys.stderr`` (H3 — caller owns I/O);
- call ``set_log_level`` (H1 — flips verbosity for the shared scheduler thread);
- raise ``SystemExit`` / call ``emit_envelope_and_exit`` (caller maps exit codes);
- read stdin / write stdout / emit the config_echo banner / do recon logging.

It returns a :class:`ValidateOutcome`. The two failure modes map cleanly:
- URL reachability failure → **raise** :class:`errors.ExternalServiceError`
  (caller maps to exit-4);
- per-row validation failures → collected into ``outcome.errors`` (caller maps
  to exit-2), so passing rows still stream while the run is flagged failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Importing the adapters package populates the registry via its ``register()``
# side effects (reject_unsupported_platform / route_tier_for read it). The
# engine triggers registration itself rather than rely on the caller — mirrors
# ledger.aggregate's self-population so the engine is correct in-process even if
# no shell imported adapters first.
import backlink_publisher.publishing.adapters  # noqa: F401,E402
from backlink_publisher._util import errors
from backlink_publisher._util.errors import InputValidationError
from backlink_publisher._util.logger import validate_logger
from backlink_publisher.config import Config, load_config
from backlink_publisher.linkcheck.http import check_urls_strict
from backlink_publisher.publishing.content_negotiation import route_tier_for
from backlink_publisher.schema import (
    _is_field_present,
    reject_unsupported_platform,
    validate_output_payload,
)

from backlink_publisher.cli._validate_payload import (
    _enhance_payload,
    _extract_hrefs_from_html,
)


@dataclass
class ValidateOutcome:
    """Result of validating a batch of planned-backlink rows.

    - ``outputs``: enhanced rows that passed every gate (stream to stdout).
    - ``errors``: human-readable per-row validation errors (caller → exit-2).
    - ``platform_drops`` / ``validation_drops``: 1-based row indices that
      vanished at the platform-enum/tier gate vs. the schema/payload gate —
      feeds the Silent-Drop Tripwire reconciliation line.
    - ``input_count``: number of rows received (for the recon delta + epilogue).
    """

    outputs: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    platform_drops: list[int] = field(default_factory=list)
    validation_drops: list[int] = field(default_factory=list)
    input_count: int = 0

    @property
    def failed_count(self) -> int:
        """Rows that did not make it to ``outputs`` (input minus passing)."""
        return self.input_count - len(self.outputs)


def load_config_tolerant() -> Config | None:
    """Load config with the validate-time fail-soft tolerance (audit surface 7).

    Shared by the CLI shell and the in-process ``PipelineAPI`` bridge so both
    degrade identically. ``InputValidationError`` re-raises (cells.py fail-loud:
    unknown channel / overlap must surface); any other load failure WARNs and
    returns ``None`` so the branded_pool live-fallback is simply disabled and the
    run proceeds on payload-emitted snapshots only.

    This helper is NOT pure (it WARN-logs + reads disk), so it lives outside
    :func:`validate_rows`; config is passed INTO the engine.
    """
    try:
        return load_config()
    except InputValidationError:
        raise  # cells.py fail-loud contract: unknown channel / overlap must surface
    except Exception as exc:  # noqa: BLE001 — other config-load failures are tolerated
        validate_logger.warn(
            f"config load failed ({exc}); branded_pool fallback disabled, "
            "relying on payload-emitted snapshots only"
        )
        return None


def validate_rows(
    rows: list[dict[str, Any]],
    config: Config | None,
    *,
    check_urls: bool,
) -> ValidateOutcome:
    """Validate planned-backlink ``rows`` against ``config``. Pure compute.

    When ``check_urls`` is True, collect every reachable URL (target_url,
    main_domain, each link url, and http(s) ``<a href>`` from content_html) and
    run :func:`check_urls_strict`. A reachability failure **raises**
    :class:`errors.ExternalServiceError` — the caller maps it to exit-4.

    Then run the per-row loop (platform-enum guard → content_html-only tier gate
    → schema validation → ``_enhance_payload``). Rows that pass land in
    ``outcome.outputs``; failures are collected into ``outcome.errors`` (the
    caller maps a non-empty list to exit-2) so passing rows still stream and the
    operator sees partial success.
    """
    outcome = ValidateOutcome(input_count=len(rows))

    if check_urls:
        all_urls: set[str] = set()
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
            # Pure: propagate the typed exception; the caller (shell or
            # PipelineAPI) owns the exit-4 envelope / PipeResult mapping.
            check_urls_strict(list(all_urls))

    for idx, row in enumerate(rows, start=1):
        # Check for unsupported platforms (post-R9d: helper covers any
        # unregistered platform, not just linkedin)
        platform = row.get("platform", "")
        platform_msg = reject_unsupported_platform(platform)
        if platform_msg is not None:
            outcome.errors.append(f"row {idx}: {platform_msg}")
            outcome.platform_drops.append(idx)
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
            outcome.errors.append(
                f"row {idx}: platform '{platform}' does not yet accept "
                f"content_html (only markdown). Provide content_markdown or "
                f"wait for adapter retrofit."
            )
            outcome.platform_drops.append(idx)
            continue

        errs = validate_output_payload(row)
        if errs:
            outcome.errors.extend(f"row {idx}: {e}" for e in errs)
            outcome.validation_drops.append(idx)
            continue
        enhanced = _enhance_payload(row, config)
        if enhanced["validation"]["status"] == "failed":
            # R2/R5 row-level abort: don't forward to outputs; surface errors.
            for err in enhanced["validation"]["errors"]:
                outcome.errors.append(f"row {idx}: {err}")
            continue
        outcome.outputs.append(enhanced)

    return outcome
