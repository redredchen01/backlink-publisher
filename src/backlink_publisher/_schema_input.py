"""Input-payload validation — extracted from schema.py for monolith SLOC budget.

Each ``_check_input_*`` helper validates one independent aspect and returns its own
error list, embedding the ``line {line_num}:`` prefix. ``validate_input_payload``
concatenates them in declaration order — the append ordering is a characterized
contract (see ``tests/test_schema_input_payload_characterization.py``).
"""

from __future__ import annotations

import re
from typing import Any

from .linkcheck.language import SUPPORTED_LANGUAGES
from .schema import (
    INPUT_SCHEMA_FIELDS,
    INPUT_OPTIONAL_FIELDS,
    PUBLISH_MODES,
    URL_MODES,
    _normalize_main_domain,
    supported_platforms,
)


def _check_input_required_fields(row: dict[str, Any], line_num: int) -> list[str]:
    errors: list[str] = []
    for field, ftype in INPUT_SCHEMA_FIELDS.items():
        if field not in row:
            errors.append(f"line {line_num}: missing required field '{field}'")
        elif not isinstance(row[field], ftype):
            errors.append(f"line {line_num}: field '{field}' must be {ftype.__name__}")
    return errors


def _check_input_optional_field_types(row: dict[str, Any], line_num: int) -> list[str]:
    # Check optional fields types
    errors: list[str] = []
    for field, ftype in INPUT_OPTIONAL_FIELDS.items():
        if field in row and not isinstance(row[field], ftype):
            errors.append(f"line {line_num}: field '{field}' must be {ftype.__name__}")
    return errors


def _check_input_enumerated_values(row: dict[str, Any], line_num: int) -> list[str]:
    # Validate enumerated values
    errors: list[str] = []
    if "language" in row and row["language"] not in SUPPORTED_LANGUAGES:
        errors.append(
            f"line {line_num}: unsupported language '{row['language']}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
        )

    if "platform" in row and row["platform"] not in supported_platforms():
        errors.append(
            f"line {line_num}: unsupported platform '{row['platform']}'. "
            f"Supported: {', '.join(sorted(supported_platforms()))}"
        )

    if "url_mode" in row and row["url_mode"] not in URL_MODES:
        errors.append(
            f"line {line_num}: invalid url_mode '{row['url_mode']}'. "
            f"Supported: {', '.join(sorted(URL_MODES))}"
        )

    if "publish_mode" in row and row["publish_mode"] not in PUBLISH_MODES:
        errors.append(
            f"line {line_num}: invalid publish_mode '{row['publish_mode']}'. "
            f"Supported: {', '.join(sorted(PUBLISH_MODES))}"
        )
    return errors


def _check_input_urls_and_normalize(row: dict[str, Any], line_num: int) -> list[str]:
    """Validate URL scheme prefixes and normalize ``main_domain``.

    **Side effect (load-bearing):** when ``main_domain`` is a valid URL, the
    normalized punycode form is stored as ``row["main_domain_normalized"]`` for
    downstream Unit-6 host-parse. ``target_url`` is not normalized — it flows to
    adapters which need the operator's exact URL. Normalization failures become
    per-row errors, not batch-aborting ``SystemExit`` (plan-review security P2).
    """
    errors: list[str] = []
    for url_field in ("target_url", "main_domain"):
        if url_field in row:
            url_val = str(row[url_field])
            if not re.match(r"^https?://", url_val):
                errors.append(f"line {line_num}: field '{url_field}' is not a valid URL: {url_val}")
                continue
            if url_field == "main_domain":
                try:
                    row["main_domain_normalized"] = _normalize_main_domain(url_val)
                except ValueError as exc:
                    errors.append(
                        f"line {line_num}: field 'main_domain' could not be normalized: {exc}"
                    )
    return errors


def _check_input_seed_keywords(row: dict[str, Any], line_num: int) -> list[str]:
    # Validate seed_keywords item types (list type already checked above)
    errors: list[str] = []
    if "seed_keywords" in row and isinstance(row["seed_keywords"], list):
        for kw in row["seed_keywords"]:
            if not isinstance(kw, str):
                errors.append(f"line {line_num}: 'seed_keywords' items must be strings")
    return errors


def validate_input_payload(row: dict[str, Any], line_num: int) -> list[str]:
    """Validate an input seed row. Returns list of error messages.

    Concatenates the per-block ``_check_input_*`` helpers in a fixed order; the
    resulting error ordering is a characterized contract.

    Side effect (plan 2026-05-18-006 Unit 1): ``_check_input_urls_and_normalize``
    stores ``row["main_domain_normalized"]`` when ``main_domain`` is a valid URL,
    preserving the original ``row["main_domain"]`` verbatim.
    """
    errors: list[str] = []
    errors.extend(_check_input_required_fields(row, line_num))
    errors.extend(_check_input_optional_field_types(row, line_num))
    errors.extend(_check_input_enumerated_values(row, line_num))
    errors.extend(_check_input_urls_and_normalize(row, line_num))
    errors.extend(_check_input_seed_keywords(row, line_num))
    return errors


def validate_input_payload_strict(row: dict[str, Any]) -> list[str]:
    """Validate an input seed row strictly with exit code 2 semantics."""
    errors = validate_input_payload(row, 0)
    return errors
