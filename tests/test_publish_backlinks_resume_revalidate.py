"""Tests for R13: --resume hard re-validates checkpoint items.

Plan 2026-05-14-001 Unit 7. Verifies that on --resume, pending/failed
items whose payload was created under the buggy language_matches get
reclassified with the retro_* error_class and skipped from the resume
batch (their slot moves to checkpoint status='failed' with error_class
naming the retro reason).
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from backlink_publisher import checkpoint
from backlink_publisher.cli import publish_backlinks


def _good_payload(row_id: str, language: str = "en") -> dict[str, Any]:
    """A payload that passes the new R2/R5 gate."""
    body = {
        "en": "This is an English article about https://example.com and some content here.",
        "zh-CN": "这是一篇关于人工智能的文章，我们在这里讨论一些技术细节，详见https://example.com。",
    }[language]
    anchor_main = {"en": "Example", "zh-CN": "苹果官网"}[language]
    anchor_target = {"en": "Article", "zh-CN": "文章详情"}[language]
    return {
        "id": row_id,
        "platform": "medium",
        "language": language,
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": "Test",
        "slug": "test",
        "excerpt": "x",
        "tags": ["t"],
        "content_markdown": body,
        "links": [
            {"url": "https://example.com", "anchor": anchor_main, "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": anchor_target, "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://so.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub", "kind": "supporting", "required": False},
        ],
        "seo": {"title": "Test", "description": "x", "canonical_url": "https://example.com/article"},
    }


def _bad_lang_payload(row_id: str) -> dict[str, Any]:
    """zh-CN row whose body is in English — fails R2 under the new gate."""
    p = _good_payload(row_id, language="zh-CN")
    p["content_markdown"] = "This is an English article about example.com content."
    return p


def _bad_anchor_payload(row_id: str) -> dict[str, Any]:
    """zh-CN row whose main_domain anchor is Latin (not in branded_pool) — fails R5."""
    p = _good_payload(row_id, language="zh-CN")
    p["links"][0]["anchor"] = "example.com"  # Latin, no CJK
    return p


def _run_resume(run_id: str) -> tuple[str, str, int]:
    """Invoke publish-backlinks --resume <run_id> via main()."""
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = StringIO("")
        out, err = StringIO(), StringIO()
        sys.stdout, sys.stderr = out, err
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
                        _dry_run=False,
                        _command="resume",
                        published_url="https://medium.com/p/xyz",
                    )
                    publish_backlinks.main(["--resume", run_id, "--mode", "draft"])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr


def test_resume_reclassifies_retro_language_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending row whose body language is wrong → retro_language_failed."""
    monkeypatch.setattr(
        "backlink_publisher.checkpoint._checkpoint_dir",
        lambda: tmp_path,
    )
    run_id, _ = checkpoint.create_checkpoint(
        [_bad_lang_payload("row1")],
        platform="medium",
        mode="draft",
    )

    _, stderr, code = _run_resume(run_id)

    reloaded = checkpoint.load_checkpoint(run_id)
    items = reloaded["items"]
    assert len(items) == 1
    assert items[0]["status"] == "failed"
    assert items[0]["error_class"] == checkpoint.RETRO_LANGUAGE_FAILED
    assert "body language" in items[0]["error"]


def test_resume_reclassifies_retro_anchor_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending row with Latin anchor in zh-CN article → retro_anchor_failed."""
    monkeypatch.setattr(
        "backlink_publisher.checkpoint._checkpoint_dir",
        lambda: tmp_path,
    )
    run_id, _ = checkpoint.create_checkpoint(
        [_bad_anchor_payload("row1")],
        platform="medium",
        mode="draft",
    )

    _run_resume(run_id)

    reloaded = checkpoint.load_checkpoint(run_id)
    items = reloaded["items"]
    assert items[0]["status"] == "failed"
    assert items[0]["error_class"] == checkpoint.RETRO_ANCHOR_FAILED


def test_resume_skips_already_retro_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Idempotency: re-resuming a run that already has retro items skips them."""
    monkeypatch.setattr(
        "backlink_publisher.checkpoint._checkpoint_dir",
        lambda: tmp_path,
    )
    run_id, _ = checkpoint.create_checkpoint(
        [_bad_anchor_payload("row1")],
        platform="medium",
        mode="draft",
    )
    _run_resume(run_id)
    reloaded = checkpoint.load_checkpoint(run_id)
    assert reloaded["items"][0]["error_class"] == checkpoint.RETRO_ANCHOR_FAILED

    # Re-resume — should not change the classification.
    _run_resume(run_id)
    reloaded2 = checkpoint.load_checkpoint(run_id)
    assert reloaded2["items"][0]["error_class"] == checkpoint.RETRO_ANCHOR_FAILED


def test_resume_does_not_reclassify_done_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Already-done items are out of scope for R13 — even if their payload
    would fail under the new gate. Verifies the 'pending/failed only' guard."""
    monkeypatch.setattr(
        "backlink_publisher.checkpoint._checkpoint_dir",
        lambda: tmp_path,
    )
    run_id, _ = checkpoint.create_checkpoint(
        [_bad_lang_payload("done1")],
        platform="medium",
        mode="draft",
    )
    # Manually flip the item to 'done' as if it was already published
    # under the buggy gate. The harm is already done; we should NOT
    # reclassify it on subsequent --resume.
    ckpt = checkpoint.load_checkpoint(run_id)
    ckpt["items"][0]["status"] = "done"
    ckpt["items"][0]["published_url"] = "https://medium.com/p/already-shipped"
    ckpt["status"] = "complete"  # so the no-pending fast path triggers
    checkpoint._atomic_write(
        checkpoint._checkpoint_path(run_id), ckpt
    )

    _run_resume(run_id)

    reloaded = checkpoint.load_checkpoint(run_id)
    item = reloaded["items"][0]
    assert item["status"] == "done"
    assert item.get("error_class") not in (
        checkpoint.RETRO_LANGUAGE_FAILED,
        checkpoint.RETRO_ANCHOR_FAILED,
    )
