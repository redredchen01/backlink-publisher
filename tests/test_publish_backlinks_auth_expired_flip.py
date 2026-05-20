"""publish-backlinks AuthExpiredError flip — Plan 2026-05-19-001 Unit 6.

Cross-layer assertion: a mocked adapter raises ``AuthExpiredError``;
the publish loop must:
  - call ``mark_expired(channel)`` on the channel_status_store
  - write a checkpoint row with ``error_class="auth_expired"``
  - exit with code 3
  - preserve ``bound_at`` on the existing record (Unit 1 invariant)
  - NOT flip unrelated channels (isolation)
"""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher._util.errors import AuthExpiredError
from backlink_publisher.linkcheck.verify import VerificationResult


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path):
    """Pin _config_dir + _cache_dir under tmp_path so channel-status.json
    and checkpoints write to ephemeral test storage."""
    fake_config_dir = tmp_path / "config"
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    with patch(
        "backlink_publisher.config._config_dir", return_value=fake_config_dir,
    ), patch(
        "backlink_publisher.checkpoint._cache_dir",
        return_value=tmp_path / "cache",
    ):
        # The channel_status_store singleton resolves its path at import time,
        # so we also rebind that path here.
        from webui_store.channel_status import channel_status_store as _store
        _store.path = fake_config_dir / "channel-status.json"
        yield fake_config_dir


@pytest.fixture(autouse=True)
def _mock_verify_pass(mocker):
    mocker.patch(
        "backlink_publisher.cli.publish_backlinks.verify_published",
        return_value=VerificationResult(ok=True, reason=""),
    )


def _run_publish(input_data: str, argv: list[str] | None = None) -> tuple[str, str, int]:
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = StringIO(input_data)
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        try:
            main(argv or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return sys.stdout.getvalue(), sys.stderr.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr


def _make_valid_payload(platform: str = "medium", row_id: str = "auth-flip-1") -> dict:
    return {
        "id": row_id,
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "An excerpt.",
        "tags": ["tag1"],
        "content_markdown": "Content about https://example.com page.",
        "links": [
            {"url": "https://example.com", "anchor": "Example",
             "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article",
             "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki",
             "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN",
             "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO",
             "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub",
             "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "Test Article | SEO",
            "description": "SEO description",
            "canonical_url": "https://example.com/article",
        },
    }


class TestAuthExpiredFlipMainPath:
    @patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
    @patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
    def test_medium_auth_expired_flips_channel_and_exits_3(
        self, mock_pub, mock_verify
    ):
        from webui_store.channel_status import get_status, mark_bound

        # Seed an existing bound record so we can verify bound_at preservation.
        from pathlib import Path
        fake_path = Path(get_status.__globals__["_config_dir"]()) / "medium-storage-state.json"
        fake_path.parent.mkdir(parents=True, exist_ok=True)
        fake_path.write_text("{}")
        mark_bound("medium", fake_path)
        original_bound_at = get_status("medium")["bound_at"]
        assert original_bound_at  # sanity

        mock_pub.side_effect = AuthExpiredError(
            channel="medium", reason="Medium /me HTTP 401"
        )

        payload = _make_valid_payload(platform="medium")
        stdout, stderr, code = _run_publish(
            json.dumps(payload), ["--platform", "medium", "--mode", "draft"]
        )

        assert code == 3, f"expected exit 3 (DependencyError family), got {code}; stderr={stderr!r}"
        assert "credentials expired" in stderr

        # Channel flipped to expired, bound_at preserved
        status = get_status("medium")
        assert status["status"] == "expired"
        assert status["bound_at"] == original_bound_at, (
            "Unit 1 invariant: mark_expired must preserve bound_at"
        )

    @patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
    @patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
    def test_blogger_auth_expired_does_not_flip_medium(
        self, mock_pub, mock_verify
    ):
        """Isolation: a Blogger AuthExpiredError must not flip Medium's status."""
        from webui_store.channel_status import get_status, mark_bound
        from pathlib import Path

        fake_dir = Path(get_status.__globals__["_config_dir"]())
        fake_dir.mkdir(parents=True, exist_ok=True)
        (fake_dir / "medium-storage-state.json").write_text("{}")
        mark_bound("medium", fake_dir / "medium-storage-state.json")

        mock_pub.side_effect = AuthExpiredError(
            channel="blogger", reason="Blogger HTTP 401"
        )

        payload = _make_valid_payload(platform="blogger")
        stdout, stderr, code = _run_publish(
            json.dumps(payload), ["--platform", "blogger", "--mode", "draft"]
        )

        assert code == 3
        # Medium untouched
        assert get_status("medium")["status"] == "bound"
        # Blogger flipped
        assert get_status("blogger")["status"] == "expired"

    @patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
    @patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
    def test_checkpoint_row_marked_auth_expired(
        self, mock_pub, mock_verify, tmp_path
    ):
        """The failed row in the checkpoint has error_class='auth_expired'."""
        mock_pub.side_effect = AuthExpiredError(
            channel="medium", reason="Medium /posts HTTP 401"
        )
        payload = _make_valid_payload(platform="medium", row_id="row-x")
        stdout, stderr, code = _run_publish(
            json.dumps(payload), ["--platform", "medium", "--mode", "draft"]
        )
        assert code == 3
        # Find the checkpoint file
        ckpt_dir = tmp_path / "cache" / "checkpoints"
        files = list(ckpt_dir.glob("*.json"))
        assert files, "checkpoint was not written"
        data = json.loads(files[0].read_text())
        # Find row-x
        row = next((it for it in data["items"] if it["id"] == "row-x"), None)
        assert row is not None
        assert row["status"] == "failed"
        assert row.get("error_class") == "auth_expired"

    @patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
    @patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
    def test_unknown_channel_in_auth_error_is_rejected_at_construction(
        self, mock_pub, mock_verify
    ):
        """Defense-in-depth: AuthExpiredError ctor itself validates channel
        against CHANNELS, so a buggy adapter that tries channel='../evil'
        can't even construct the exception."""
        from backlink_publisher._util.errors import UsageError
        with pytest.raises(UsageError):
            AuthExpiredError(channel="../evil")


class TestAuthExpiredIsCaughtAsDependencyError:
    """Compatibility: callers that still ``except DependencyError`` (e.g.,
    in-tree code that hasn't migrated yet) keep catching AuthExpiredError."""

    def test_isinstance_dependency_error(self):
        from backlink_publisher._util.errors import DependencyError
        exc = AuthExpiredError(channel="medium", reason="test")
        assert isinstance(exc, DependencyError)
        assert exc.exit_code == 3
