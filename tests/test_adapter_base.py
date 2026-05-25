"""Tests for AdapterResult base type."""

from backlink_publisher.publishing.adapters.base import AdapterResult


def test_adapter_result_defaults():
    r = AdapterResult(status="drafted", adapter="blogger-api", platform="blogger")
    assert r.draft_url == ""
    assert r.published_url == ""
    assert r.error is None
    assert r._dry_run is False


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


def test_to_publish_output_emits_link_attr_verification_when_present():
    """Happy path: an attached link-attr verdict is surfaced in the output."""
    verdict = {
        "verification": "ok",
        "total_anchors": 12,
        "nofollow_anchors": 0,
        "nofollow_detected": False,
        "nofollow_reason": None,
    }
    r = AdapterResult(
        status="published",
        adapter="txtfyi-form",
        platform="txtfyi",
        published_url="https://txt.fyi/abc",
        _provider_meta={"link_attr_verification": verdict},
    )
    out = r.to_publish_output({"id": "x1"}, "2026-05-25T00:00:00+00:00")
    assert out["link_attr_verification"] == verdict


def test_to_publish_output_emits_skipped_verdict():
    """A 'skipped' verdict is still surfaced — the operator needs to see skips."""
    verdict = {"verification": "skipped", "reason": "non-200"}
    r = AdapterResult(
        status="published",
        adapter="livejournal-api",
        platform="livejournal",
        published_url="https://x.livejournal.com/1",
        _provider_meta={"link_attr_verification": verdict},
    )
    out = r.to_publish_output({"id": "x2"}, "2026-05-25T00:00:00+00:00")
    assert out["link_attr_verification"] == verdict


def test_to_publish_output_omits_key_when_provider_meta_none():
    """Draft mode / non-verifying adapters: output shape is unchanged."""
    r = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url="https://blog.example.com/p/1",
    )
    out = r.to_publish_output({"id": "x3"}, "2026-05-25T00:00:00+00:00")
    assert "link_attr_verification" not in out


def test_to_publish_output_omits_key_when_meta_lacks_verification():
    """_provider_meta present but without the verification key → key omitted."""
    r = AdapterResult(
        status="published", adapter="medium-api", platform="medium",
        published_url="https://medium.com/p/1",
        _provider_meta={"something_else": 1},
    )
    out = r.to_publish_output({"id": "x4"}, "2026-05-25T00:00:00+00:00")
    assert "link_attr_verification" not in out
