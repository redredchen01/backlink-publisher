"""Output-payload validation — extracted from schema.py for monolith SLOC budget.

Each ``_check_output_*`` helper validates one independent aspect of a planned
output row and returns its own list of error messages. ``validate_output_payload``
concatenates them in declaration order — the append ordering is a characterized
contract (see ``tests/test_schema_output_payload_characterization.py``). Splitting
the blocks keeps each rule independently testable and drops the radon cyclomatic
complexity of the aggregate well below the C threshold.
"""

from __future__ import annotations

import re
from typing import Any

from .schema import (
    LINK_KINDS,
    MAX_CONTENT_HTML_BYTES,
    OUTPUT_ONE_OF_GROUPS,
    OUTPUT_OPTIONAL_FIELDS,
    OUTPUT_REQUIRED_FIELDS,
    _check_main_domain_presence,
    _is_field_present,
    reject_unsupported_platform,
)


def _check_output_required_fields(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field, ftype in OUTPUT_REQUIRED_FIELDS.items():
        if field not in row:
            errors.append(f"missing required output field '{field}'")
        elif not isinstance(row[field], ftype):
            errors.append(f"field '{field}' must be {ftype.__name__}, got {type(row[field]).__name__}")
    return errors


def _check_output_optional_field_types(row: dict[str, Any]) -> list[str]:
    # Validate optional output fields' types when present (e.g., content_html
    # peer of content_markdown). Plan 2026-05-18-006 Unit 1.
    errors: list[str] = []
    for field, ftype in OUTPUT_OPTIONAL_FIELDS.items():
        if field in row and not isinstance(row[field], ftype):
            errors.append(f"field '{field}' must be {ftype.__name__}, got {type(row[field]).__name__}")
    return errors


def _check_output_one_of_groups(row: dict[str, Any]) -> list[str]:
    # At-least-one cross-field predicate per OUTPUT_ONE_OF_GROUPS. Uses
    # _is_field_present (treats whitespace-only as absent — symmetric with
    # validate-time dispatch in Unit 6).
    errors: list[str] = []
    for group in OUTPUT_ONE_OF_GROUPS:
        if not any(_is_field_present(row.get(field)) for field in group):
            errors.append(
                f"at least one of {list(group)} must be present and non-empty"
            )
    return errors


def _check_content_html_size(row: dict[str, Any]) -> list[str]:
    # content_html size cap — defends downstream regex + html.parser from
    # regex-bomb / memory-pressure attacks. Plan Threat Model DoS row.
    if "content_html" in row and isinstance(row["content_html"], str):
        size = len(row["content_html"].encode("utf-8"))
        if size > MAX_CONTENT_HTML_BYTES:
            return [
                f"content_html size {size} bytes exceeds {MAX_CONTENT_HTML_BYTES} byte cap"
            ]
    return []


def _check_links_structure(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "links" in row and isinstance(row["links"], list):
        for i, link in enumerate(row["links"]):
            if not isinstance(link, dict):
                errors.append(f"links[{i}] must be a dict")
            else:
                for req in ("url", "anchor", "kind", "required"):
                    if req not in link:
                        errors.append(f"links[{i}]: missing field '{req}'")
                if "url" in link and not re.match(r"^https?://", link["url"]):
                    errors.append(f"links[{i}]: invalid URL format: {link['url']}")
                if "kind" in link and link["kind"] not in LINK_KINDS:
                    errors.append(f"links[{i}]: invalid kind '{link['kind']}'")
    return errors


def _check_seo_structure(row: dict[str, Any]) -> list[str]:
    # Validate SEO structure
    #
    # ``seo`` is an OUTPUT_REQUIRED_FIELDS member; when present it must carry
    # ``title`` / ``description`` / ``canonical_url`` as strings. ``canonical_url``
    # additionally goes through a URL-format validator (Plan 2026-05-21-003
    # Unit 1): this is the SOLE defense layer for forwarder adapters that
    # inject the value into HTML / YAML / GraphQL contexts without their own
    # escaping (per ``tests/test_adapter_blogger_api_xss_contract.py``).
    #
    # The regex rejects control chars, whitespace, quotes, angle-brackets,
    # and backticks — the union of HTML attribute / HTML element / YAML newline
    # / GraphQL string-escape / template-literal injection vectors. It accepts
    # both ``http://`` and ``https://`` but no other schemes (no ``javascript:``,
    # ``data:``, ``file:``, ``vbscript:``).
    #
    # Empty string is intentionally accepted (Mixed canonical strategy: rows
    # opt into syndication mode by populating canonical_url, or stay in
    # pure-backlink mode by leaving it empty; adapters short-circuit ``""``
    # via ``... or None`` at read time).
    errors: list[str] = []
    if "seo" in row and isinstance(row["seo"], dict):
        for req in ("title", "description", "canonical_url"):
            if req not in row["seo"]:
                errors.append(f"seo: missing field '{req}'")
            elif not isinstance(row["seo"][req], str):
                errors.append(f"seo.{req} must be a string")

        canonical = row["seo"].get("canonical_url")
        if isinstance(canonical, str) and canonical != "":
            if not re.match(r"^https?://[^\s\"'<>`\x00-\x1f\x7f]+$", canonical, re.IGNORECASE):
                errors.append(
                    f"seo.canonical_url is not a valid http(s) URL "
                    f"(must match ^https?:// and contain no whitespace, "
                    f"quotes, angle brackets, backticks, or control chars): "
                    f"{canonical!r}"
                )
    return errors


def _check_link_count(row: dict[str, Any]) -> list[str]:
    # Validate link count (6-8 for backlink articles)
    link_count = len(row.get("links", []))
    if link_count < 6 or link_count > 8:
        return [f"link count {link_count} is not between 6 and 8"]
    return []


def _check_nonempty_text_fields(row: dict[str, Any]) -> list[str]:
    # title / excerpt / slug must not be whitespace-only when present as strings.
    errors: list[str] = []
    for field in ("title", "excerpt", "slug"):
        if field in row and isinstance(row[field], str) and not row[field].strip():
            errors.append(f"{field} must not be empty")
    return errors


def validate_output_payload(row: dict[str, Any]) -> list[str]:
    """Validate a planned output payload. Returns list of error messages.

    Concatenates the per-block ``_check_output_*`` helpers in a fixed order;
    the resulting error ordering is a characterized contract.
    """
    errors: list[str] = []
    errors.extend(_check_output_required_fields(row))
    errors.extend(_check_output_optional_field_types(row))
    errors.extend(_check_output_one_of_groups(row))
    errors.extend(_check_content_html_size(row))
    errors.extend(_check_links_structure(row))
    errors.extend(_check_seo_structure(row))
    errors.extend(_check_link_count(row))
    errors.extend(_check_nonempty_text_fields(row))

    # Validate main_domain appears in content (markdown substring; HTML host-parse
    # lives in cli.validate_backlinks per Unit 6).
    main_domain_error = _check_main_domain_presence(row)
    if main_domain_error is not None:
        errors.append(main_domain_error)

    return errors


def validate_publish_payload(row: dict[str, Any]) -> list[str]:
    """Validate a payload ready for publishing. Returns list of error messages."""
    errors = validate_output_payload(row)

    # Additional publish-specific checks
    if "platform" in row:
        msg = reject_unsupported_platform(row["platform"])
        if msg is not None:
            errors.append(msg)

    return errors
