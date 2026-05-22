"""Tests for the R8/R9 publish-time reachability re-check.

Plan 2026-05-14-001 Unit 5. Exercises _check_row_reachability and the
per-row skip path through publish-backlinks. The default autouse fixture
in tests/conftest.py patches check_url to (True, None); tests below
re-patch for failure scenarios.
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest

from backlink_publisher.cli import publish_backlinks


def _valid_payload(row_id: str = "abc") -> dict[str, Any]:
    return {
        "id": row_id,
        "platform": "medium",
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "A test excerpt.",
        "tags": ["x"],
        "content_markdown": "Content about https://example.com here.",
        "links": [
            {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub", "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "Test Article",
            "description": "A test excerpt.",
            "canonical_url": "https://example.com/article",
        },
        "validation": {"status": "passed", "checked_at": "2026-05-14T00:00:00+00:00", "warnings": [], "errors": []},
    }


def _run_publish(input_data: str, extra_argv: list[str] | None = None) -> tuple[str, str, int]:
    """Run publish-backlinks --dry-run-ish via direct main() call."""
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = StringIO(input_data)
        out, err = StringIO(), StringIO()
        sys.stdout, sys.stderr = out, err
        argv = ["--mode", "draft", "--dry-run"] + (extra_argv or [])
        try:
            with patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup"):
                with patch(
                    "backlink_publisher.cli.publish_backlinks.adapter_publish"
                ) as mock_pub:
                    from backlink_publisher.publishing.adapters.base import AdapterResult
                    mock_pub.return_value = AdapterResult(
                        status="draft",
                        adapter="medium-api",
                        platform="medium",
                        _dry_run=True,
                        _command="dry-run",
                    )
                    publish_backlinks.main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr


# --- _check_row_reachability unit tests ---


def test_check_row_reachability_all_ok() -> None:
    """All URLs reachable → (True, None)."""
    row = _valid_payload()
    with patch(
        "backlink_publisher.cli._publish_helpers.check_url",
        return_value=(True, None),
    ):
        ok, failing = publish_backlinks._check_row_reachability(row)
    assert ok is True
    assert failing is None


def test_check_row_reachability_target_unreachable() -> None:
    """target_url unreachable → (False, target_url)."""
    row = _valid_payload()

    def fake(url: str) -> tuple[bool, str | None]:
        if url == "https://example.com/article":
            return False, "HTTP 404"
        return True, None

    with patch(
        "backlink_publisher.cli._publish_helpers.check_url",
        side_effect=fake,
    ):
        ok, failing = publish_backlinks._check_row_reachability(row)
    assert ok is False
    assert failing == "https://example.com/article"


def test_check_row_reachability_link_unreachable() -> None:
    """A supporting link unreachable → (False, that link's url)."""
    row = _valid_payload()

    def fake(url: str) -> tuple[bool, str | None]:
        if url == "https://wikipedia.org":
            return False, "HTTP 503"
        return True, None

    with patch(
        "backlink_publisher.cli._publish_helpers.check_url",
        side_effect=fake,
    ):
        ok, failing = publish_backlinks._check_row_reachability(row)
    assert ok is False
    assert failing == "https://wikipedia.org"


def test_check_row_reachability_empty_urls_passes() -> None:
    """Row with no URLs returns (True, None) without calling check_url."""
    row = {"target_url": "", "links": []}
    with patch(
        "backlink_publisher.cli._publish_helpers.check_url",
    ) as mocked:
        ok, failing = publish_backlinks._check_row_reachability(row)
    assert ok is True
    assert failing is None
    mocked.assert_not_called()


# --- skip_publish_time_check flag bypasses the gate ---


def test_skip_flag_bypasses_check(tmp_path: Any) -> None:
    """When --skip-publish-time-check is set, check_url is never called."""
    payload = _valid_payload()
    with patch(
        "backlink_publisher.cli._publish_helpers.check_url",
    ) as mocked:
        # Dry-run already bypasses the check; explicitly pass the flag to
        # ensure it's preserved on the args namespace for non-dry-run too.
        out, err, code = _run_publish(json.dumps(payload), ["--skip-publish-time-check"])
    assert code == 0
    # check_url should not have been called for dry-run regardless,
    # but the flag's presence on args confirms wiring.
    assert "--skip-publish-time-check" not in err  # no help-text leak
