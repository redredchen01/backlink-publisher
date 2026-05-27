"""Schema definitions and validation for backlink pipeline payloads."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .linkcheck.language import SUPPORTED_LANGUAGES

INPUT_SCHEMA_FIELDS = {
    "target_url": str,
    "main_domain": str,
    "language": str,
    "platform": str,
    "url_mode": str,
    "publish_mode": str,
}

INPUT_OPTIONAL_FIELDS = {
    "topic": str,
    "seed_keywords": list,
    "extra_urls": list,
    "custom_title": str,
    "custom_tags": str,
    "target_language": str,
}

#: Re-export from :mod:`backlink_publisher.linkcheck.language` for back-compat —
#: the canonical source is :data:`linkcheck.language.SUPPORTED_LANGUAGES`. Plan
#: 2026-05-18-006 Unit 1 de-duplicated the previous parallel ``set`` literal.
#: (Post-Plan-2026-05-18-001 Unit 6 packaging refactor: language_check.py
#: moved to linkcheck/language.py; legacy import path still works via the
#: MetaPathFinder shim in :mod:`backlink_publisher.__init__`.)
__all__ = ["SUPPORTED_LANGUAGES", "supported_platforms", "reject_unsupported_platform"]


def supported_platforms() -> frozenset[str]:
    """Return the set of platform names with at least one registered adapter.

    Delegates to :func:`backlink_publisher.publishing.registry.registered_platforms`
    so the schema-layer enum stays in lockstep with the dispatch registry
    (plan 2026-05-18-009 R9e). The lazy import inside the function forces the
    adapter side-effect registration via ``backlink_publisher.publishing.adapters``
    so callers do not need to remember to import it first.
    """
    from .publishing import adapters  # noqa: F401  populate registry
    from .publishing.registry import registered_platforms

    return frozenset(registered_platforms())


def reject_unsupported_platform(platform: str) -> str | None:
    """Return a user-facing rejection message if ``platform`` lacks an adapter.

    Plan 2026-05-18-009 R9d — folds the three coordinated LinkedIn-specific
    rejection sites (``schema.py``, ``publish_backlinks.py``,
    ``validate_backlinks.py``) into a single registry-driven helper. Coverage
    now extends beyond linkedin to any unregistered platform (e.g. tiktok,
    threads). Returns ``None`` when the platform is registered.
    """
    if platform in supported_platforms():
        return None
    supported = ", ".join(sorted(supported_platforms()))
    return f"platform '{platform}' is not supported. Supported: {supported}"


URL_MODES = {"A", "B", "C"}
PUBLISH_MODES = {"draft", "publish"}

# Output payload fields
OUTPUT_REQUIRED_FIELDS = {
    "id": str,
    "platform": str,
    "language": str,
    "publish_mode": str,
    "target_url": str,
    "main_domain": str,
    "url_mode": str,
    "title": str,
    "slug": str,
    "excerpt": str,
    "tags": list,
    "links": list,
    "seo": dict,
}

#: Output fields that are individually optional but appear in groups where at
#: least one must be present. Plan 2026-05-18-006 R2 / Unit 1 — ``content_html``
#: was added as a peer to ``content_markdown``; rows must carry at least one.
#: Future Telegraph node format can extend the existing group rather than
#: requiring a new top-level structure (extensibility per arch-strategist).
OUTPUT_ONE_OF_GROUPS: tuple[tuple[str, ...], ...] = (
    ("content_markdown", "content_html"),
)

#: Optional output fields with type expectations. Validated only when present.
OUTPUT_OPTIONAL_FIELDS = {
    "content_markdown": str,
    "content_html": str,
    "main_domain_normalized": str,
}

LINK_KINDS = {"main_domain", "target", "supporting", "extra", "category", "detail"}

MAX_PAYLOAD_SIZE_BYTES = 256 * 1024  # 256 KB

#: Cap on ``content_html`` byte length. Defends the script/style strip regex
#: in :mod:`language_check` and stdlib ``html.parser`` (Unit 6) from
#: regex-bomb / memory-pressure inputs. ``content_markdown`` left uncapped
#: in v1 (existing baseline; no regression). Plan 2026-05-18-006 Unit 1 +
#: Threat Model DoS row.
MAX_CONTENT_HTML_BYTES = 1_048_576  # 1 MiB


def _is_field_present(value: Any) -> bool:
    """Return True iff ``value`` is a non-empty, non-whitespace string.

    Field-presence predicate shared between schema-time validation (this module)
    and validate-time dispatch (:mod:`backlink_publisher.cli.validate_backlinks`).
    A ``None`` value or whitespace-only string is treated as absent.

    Plan 2026-05-18-006 Unit 1 + Unit 6 (consistent semantics across phases).
    """
    return isinstance(value, str) and bool(value.strip())


def _normalize_main_domain(url: str) -> str:
    """Return ``url`` with hostname IDN-encoded to ASCII punycode + lowercased.

    Operator-supplied ``main_domain`` is a full URL with scheme. Splits the
    URL, extracts the hostname, IDN-encodes (handling Unicode hostnames like
    ``löve.de`` → ``xn--lve-1la.de``), lowercases, strips trailing dot, and
    reconstructs.

    Raises :class:`ValueError` if the URL has no hostname or the IDN-encode
    fails (e.g. label longer than 63 octets, fully empty hostname after split).
    Callers should handle the exception as a per-row validation error rather
    than aborting the batch (plan 2026-05-18-006 security P2).
    """
    parts = urlsplit(url.strip())
    if not parts.hostname:
        raise ValueError("main_domain has no parseable hostname")
    try:
        hostname_ascii = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise ValueError(f"main_domain IDN-encode failed: {exc}") from exc
    if not hostname_ascii:
        raise ValueError("main_domain IDN-encode produced empty hostname")
    # Reconstruct with the normalized hostname. Preserve scheme, port, path,
    # query, fragment as-is — only the host component is normalized.
    netloc = hostname_ascii
    if parts.port is not None:
        netloc = f"{hostname_ascii}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _check_main_domain_presence(row: dict[str, Any]) -> str | None:
    """Verify ``main_domain`` appears in the row's content.

    Returns an error message if the invariant is violated, ``None`` otherwise.
    Routing:

    - ``content_markdown`` present → existing substring check, unchanged
      behavior.
    - ``content_html`` present, ``content_markdown`` absent → defers to the
      host-aware check at validate-time
      (:mod:`backlink_publisher.cli.validate_backlinks`); this schema-level
      helper returns ``None`` so the row isn't rejected at schema time
      (the actual HTML check requires :mod:`html.parser` which lives outside
      schema).
    - Both present → substring check still runs on the markdown side; the
      HTML side is also checked downstream.
    - Neither present → handled by ``OUTPUT_ONE_OF_GROUPS``, not here.

    Plan 2026-05-18-006 Unit 1 (refactor of inline check) + Unit 6 (HTML
    host-parse).
    """
    if "main_domain" not in row:
        return None
    md_domain = row["main_domain"]
    if not isinstance(md_domain, str):
        return None
    if _is_field_present(row.get("content_markdown")):
        md = row["content_markdown"]
        md_domain_norm = md_domain.rstrip("/")
        if md_domain_norm not in md and md_domain not in md:
            return f"main_domain '{md_domain}' does not appear in content_markdown"
    # HTML-only path is validated in cli.validate_backlinks (Unit 6) which
    # has access to html.parser.
    return None


# Re-export from extracted sub-modules. All existing callers import from
# ``backlink_publisher.schema`` — the re-exports keep those import paths
# working without changes.
from ._schema_input import (  # noqa: F401, E402
    _check_input_enumerated_values,
    _check_input_optional_field_types,
    _check_input_required_fields,
    _check_input_seed_keywords,
    _check_input_urls_and_normalize,
    validate_input_payload,
    validate_input_payload_strict,
)
from ._schema_output import (  # noqa: F401, E402
    _check_content_html_size,
    _check_link_count,
    _check_links_structure,
    _check_nonempty_text_fields,
    _check_output_one_of_groups,
    _check_output_optional_field_types,
    _check_output_required_fields,
    _check_seo_structure,
    validate_output_payload,
    validate_publish_payload,
)
