"""Schema-layer canonical_url contract tests.

Plan 2026-05-21-003 Unit 1. Locks the schema-layer URL validator that
protects all downstream adapters from injection-style ``canonical_url``
payloads. Adapters remain pure forwarders per
``tests/test_adapter_blogger_api_xss_contract.py`` — they do not
re-sanitize. The schema gate is the *single* defense layer, so it must
reject every shape of malicious value here.

Coverage:

- Structural contract (regression-lock): the ``seo`` block itself is a
  required output field per ``OUTPUT_REQUIRED_FIELDS``; when present it
  must carry the three inner fields ``title`` / ``description`` /
  ``canonical_url`` as strings.
- Mixed canonical strategy: ``canonical_url`` MAY be an empty string
  ``""`` — adapters treat empty as "not provided" via ``... or None``
  short-circuit (pure-backlink mode). Non-empty values must pass the
  URL-format validator added by this Unit.
- URL-format validator (NEW): must match ``^https?://`` (case-
  insensitive, no other schemes), and must not contain control
  characters, quotes, angle brackets, or whitespace — the union of all
  known HTML/YAML/GraphQL escape vectors the forwarder adapters cannot
  defend against on their own.
"""

from __future__ import annotations

from typing import Any

import pytest

from backlink_publisher.schema import validate_publish_payload


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _base_publish_payload(**overrides: Any) -> dict[str, Any]:
    """Return a publish-payload row that satisfies all OTHER schema rules,
    so the only failure surface is the ``seo`` block under test.

    Mirrors ``_valid_output_row`` in ``tests/test_schema_source_format.py``.
    ``seo`` is part of ``OUTPUT_REQUIRED_FIELDS`` — always include a valid
    block; overrides can replace it.
    """
    payload: dict[str, Any] = {
        "id": "row-001",
        "platform": "telegraph",
        "language": "en",
        "publish_mode": "publish",
        "title": "Sample post",
        "excerpt": "Sample excerpt for SEO purposes.",
        "target_url": "https://example.com/target",
        "main_domain": "https://example.com",
        "url_mode": "A",
        "slug": "sample-post",
        "tags": ["test"],
        "content_markdown": "Body text with https://example.com link.",
        "links": [
            {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub", "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "SEO title",
            "description": "SEO description.",
            "canonical_url": "https://example.com/post",
        },
    }
    payload.update(overrides)
    return payload


def _seo(**overrides: Any) -> dict[str, str]:
    seo = {
        "title": "SEO title",
        "description": "SEO description.",
        "canonical_url": "https://example.com/post",
    }
    seo.update(overrides)
    return seo


# --------------------------------------------------------------------------- #
# Structural contract (regression-lock existing semantics)                    #
# --------------------------------------------------------------------------- #


class TestSeoStructure:
    def test_seo_block_with_all_three_fields_is_valid(self):
        """Default `seo` block — title/description/canonical_url all set."""
        row = _base_publish_payload()
        errors = validate_publish_payload(row)
        assert errors == [], errors

    def test_seo_missing_canonical_url_rejected(self):
        bad_seo = _seo()
        del bad_seo["canonical_url"]
        row = _base_publish_payload(seo=bad_seo)
        errors = validate_publish_payload(row)
        assert any("canonical_url" in e and "missing" in e for e in errors), errors

    def test_seo_canonical_url_non_string_rejected(self):
        row = _base_publish_payload(seo=_seo(canonical_url=12345))  # type: ignore[arg-type]
        errors = validate_publish_payload(row)
        assert any("canonical_url" in e and "must be a string" in e for e in errors), errors

    def test_seo_canonical_url_empty_string_accepted(self):
        """Empty string passes schema (still a string). Adapters treat ``""``
        as "not provided" via ``payload.get("seo", {}).get("canonical_url") or None``
        — schema layer does NOT enforce non-empty (would break the Mixed
        canonical strategy: row chooses syndication vs pure-backlink per row
        by populating or emptying this field)."""
        row = _base_publish_payload(seo=_seo(canonical_url=""))
        errors = validate_publish_payload(row)
        assert errors == [], errors


# --------------------------------------------------------------------------- #
# URL format validator (NEW in Unit 1)                                        #
# --------------------------------------------------------------------------- #


class TestCanonicalUrlFormat:
    @pytest.mark.parametrize(
        "valid_url",
        [
            "https://example.com/post",
            "http://example.com/post",
            "https://example.com/post?q=1&r=2",
            "https://example.com/post#fragment",
            "https://sub.example.com:8443/path/to/post",
            "https://example.com/路径",  # IRI passthrough; tightening to ASCII-only is out of scope
        ],
    )
    def test_accepts_normal_https_urls(self, valid_url: str):
        row = _base_publish_payload(seo=_seo(canonical_url=valid_url))
        errors = validate_publish_payload(row)
        assert errors == [], f"valid URL rejected: {valid_url!r} → {errors}"

    @pytest.mark.parametrize(
        "injection_payload",
        [
            # 1. HTML body injection — script tag break-out (Blogger/Writeas)
            '"><script>alert(1)</script>',
            # 2. HTML attribute escape — break out of href="..."
            '" onerror="alert(1)',
            # 3. YAML front-matter newline injection (ghpages)
            "https://example.com/post\nmalicious_key: true",
            "https://example.com/post\r\nadmin: true",
            # 4. javascript: pseudo-protocol — not http/https
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            # 5. GraphQL string escape break (Hashnode)
            'https://x"}{"evil":"yes',
            # 6. Whitespace / control chars
            "https://example.com/ post",
            "https://example.com/\tpost",
            "https://example.com/\x00null",
            # 7. Angle bracket smuggling
            "https://example.com/<iframe>",
            # 8. Single-quote attribute break
            "https://example.com/'onload='alert(1)",
            # 9. Plain text — missing scheme
            "example.com/post",
            "//example.com/post",
            # 10. file:// / vbscript: / other unsafe schemes
            "file:///etc/passwd",
            "vbscript:msgbox(1)",
        ],
    )
    def test_rejects_injection_payloads(self, injection_payload: str):
        row = _base_publish_payload(seo=_seo(canonical_url=injection_payload))
        errors = validate_publish_payload(row)
        assert any(
            "canonical_url" in e
            and ("valid" in e.lower() or "format" in e.lower() or "must match" in e.lower())
            for e in errors
        ), (
            f"injection payload accepted: {injection_payload!r} → errors={errors}"
        )
