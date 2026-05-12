"""Tests for AdapterResult base type."""

import json

from backlink_publisher.adapters.base import AdapterResult


def test_adapter_result_defaults():
    r = AdapterResult(status="drafted", adapter="blogger-api", platform="blogger")
    assert r.draft_url == ""
    assert r.published_url == ""
    assert r.error is None
    assert r._dry_run is False
    assert r._provider_meta == {}


def test_adapter_result_failed_allows_empty_urls():
    r = AdapterResult(
        status="failed",
        adapter="medium-api",
        platform="medium",
        draft_url="",
        published_url="",
        error="something went wrong",
    )
    assert r.error == "something went wrong"
    assert r.draft_url == ""


def test_provider_meta_is_per_instance():
    """Mutating one instance's _provider_meta must not leak to another."""
    a = AdapterResult(status="published", adapter="blogger-api", platform="blogger")
    b = AdapterResult(status="published", adapter="blogger-api", platform="blogger")
    a._provider_meta["blog_id"] = "1"
    assert b._provider_meta == {}


def test_to_publish_output_shape():
    r = AdapterResult(
        status="drafted",
        adapter="blogger-api",
        platform="blogger",
        draft_url="https://blog.example.com/p/123",
    )
    row = {"id": "abc123", "title": "My Post"}
    out = r.to_publish_output(row, "2026-05-11T00:00:00+00:00")
    assert out["id"] == "abc123"
    assert out["title"] == "My Post"
    assert out["status"] == "drafted"
    assert out["draft_url"] == "https://blog.example.com/p/123"
    assert out["adapter"] == "blogger-api"
    assert out["error"] is None


def test_to_publish_output_includes_verification_field_defaults():
    """All three additive verification fields default to null for any status."""
    r = AdapterResult(status="published", adapter="medium-api", platform="medium",
                      published_url="https://medium.com/@u/slug-abc")
    out = r.to_publish_output({"id": "x", "title": "t"}, "2026-05-12T00:00:00+00:00")
    assert out["verified"] is None
    assert out["verified_at"] is None
    assert out["verification_error"] is None


def test_to_publish_output_drafted_status_keeps_verification_null():
    """Drafted rows skip verification (R3); defaults remain null."""
    r = AdapterResult(status="drafted", adapter="blogger-api", platform="blogger",
                      draft_url="https://blog.example.com/p/123")
    out = r.to_publish_output({"id": "x", "title": "t"}, "2026-05-12T00:00:00+00:00")
    assert out["verified"] is None
    assert out["verification_error"] is None


def test_to_publish_output_is_json_serialisable():
    """The new fields must serialise to JSON (None -> null)."""
    r = AdapterResult(status="published", adapter="blogger-api", platform="blogger",
                      published_url="https://blog.example.com/2026/05/x.html")
    out = r.to_publish_output({"id": "i", "title": "t"}, "2026-05-12T00:00:00+00:00")
    encoded = json.loads(json.dumps(out))
    assert encoded["verified"] is None
    assert encoded["verified_at"] is None
    assert encoded["verification_error"] is None


def test_provider_meta_not_in_jsonl_output():
    """`_provider_meta` is internal-only and must not leak into the public JSONL shape."""
    r = AdapterResult(status="published", adapter="blogger-api", platform="blogger")
    r._provider_meta["blog_id"] = "12345"
    r._provider_meta["post_id"] = "67890"
    out = r.to_publish_output({"id": "i", "title": "t"}, "2026-05-12T00:00:00+00:00")
    assert "_provider_meta" not in out
    assert "blog_id" not in out
    assert "post_id" not in out


def test_adapter_status_enum_unchanged():
    """R13: status enum must remain drafted/published/failed; no published_unverified."""
    for status in ("drafted", "published", "failed"):
        r = AdapterResult(status=status, adapter="blogger-api", platform="blogger")
        assert r.status == status
