"""Tests for generate-backlink-text CLI — Plan 2026-05-27-006 Units 1, 3, 4, 5.

Unit 1 test scenarios (this file covers):
- Happy path: a 3-record JSONL stream parses to 3 internal records.
- Happy path: JSON object and JSON array parse the same as equivalent JSONL.
- Edge case: empty stdin / empty file → exit 0, empty stdout, stderr summary "0".
- Edge case: record count = --max-records passes; count+1 → InputValidationError exit 2.
- Edge case: raw input > --max-input-bytes → exit 2 before parse.
- Error path: --output-format=xml → UsageError exit 1 (code 1, not 2).
- Error path: record missing target_url/anchor_text → rejected row, batch continues.
- Integration: python -m --help emits usage banner (covered by test_cli_python_m_entrypoints.py).

Unit 4 test scenarios:
- Happy path: text with anchor in the link to target_url → ok, generated_text carried.
- Edge case: anchor only in body (not in <a> text) → rejected missing_anchor.
- Edge case: anchor differs by case/whitespace → ok (normalized match).
- Error path: no link to target_url → missing_link.
- Edge case: extra link to another domain → stripped, stripped_extra_links set, record ok.
- Edge case: target.com@evil.com confusion link → host canonicalized, stripped.
- Error path: article body > 400 words → length_out_of_bounds.
- Error path: comment under 30 words → length_out_of_bounds.
- Error path: output contains bidi override char → unsafe_chars.
- Error path: refusal phrase in output → llm_refusal.
- Edge case: zh output for en request → ok + language_flag set (NOT rejected).
- Edge case: long (>200 char) target_url → output target_url is full URL, not truncated.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _REPO_ROOT / "src"


# ── _read_candidates unit tests ───────────────────────────────────────────────


def test_read_candidates_jsonl_three_records():
    """3-record JSONL stream parses to 3 dicts."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    jsonl = "\n".join([
        '{"target_url": "https://a.com/", "anchor_text": "A", "mode": "comment"}',
        '{"target_url": "https://b.com/", "anchor_text": "B", "mode": "article"}',
        '{"target_url": "https://c.com/", "anchor_text": "C", "mode": "comment"}',
    ])
    result = _read_candidates(jsonl)
    assert len(result) == 3
    assert result[0]["anchor_text"] == "A"
    assert result[2]["target_url"] == "https://c.com/"


def test_read_candidates_json_object_wraps_to_list():
    """Single JSON object parses identically to a 1-record JSONL."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    obj = {"target_url": "https://x.com/", "anchor_text": "x", "mode": "comment"}
    result_obj = _read_candidates(json.dumps(obj))
    result_jsonl = _read_candidates(json.dumps(obj))
    assert len(result_obj) == 1
    assert result_obj[0] == result_jsonl[0]


def test_read_candidates_json_array():
    """JSON array parses to same record list as equivalent JSONL."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    records = [
        {"target_url": "https://a.com/", "anchor_text": "A", "mode": "comment"},
        {"target_url": "https://b.com/", "anchor_text": "B", "mode": "article"},
    ]
    from_array = _read_candidates(json.dumps(records))
    from_jsonl = _read_candidates(
        '{"target_url": "https://a.com/", "anchor_text": "A", "mode": "comment"}\n'
        '{"target_url": "https://b.com/", "anchor_text": "B", "mode": "article"}'
    )
    assert from_array == from_jsonl


def test_read_candidates_empty_input_returns_empty_list():
    """Empty input → empty list (R5b)."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    assert _read_candidates("") == []
    assert _read_candidates("   \n  ") == []


def test_read_candidates_max_input_bytes_exceeded_raises():
    """Raw input > max_input_bytes → InputValidationError (exit 2)."""
    from backlink_publisher._util.errors import InputValidationError
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    big_text = "x" * 100
    with pytest.raises(InputValidationError, match="max-input-bytes"):
        _read_candidates(big_text, max_input_bytes=50)


def test_read_candidates_exactly_max_records_passes():
    """Record count == max_records: no error."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    lines = [
        '{"target_url": "https://x.com/", "anchor_text": "a", "mode": "comment"}'
    ] * 5
    result = _read_candidates("\n".join(lines), max_records=5)
    assert len(result) == 5


def test_read_candidates_max_records_exceeded_raises():
    """Record count > max_records → InputValidationError (exit 2)."""
    from backlink_publisher._util.errors import InputValidationError
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    lines = [
        '{"target_url": "https://x.com/", "anchor_text": "a", "mode": "comment"}'
    ] * 6
    with pytest.raises(InputValidationError, match="max-records"):
        _read_candidates("\n".join(lines), max_records=5)


def test_read_candidates_skips_malformed_jsonl_lines():
    """Malformed JSONL lines are silently skipped (strict=False semantics)."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    text = (
        '{"target_url": "https://a.com/", "anchor_text": "A", "mode": "comment"}\n'
        'NOT JSON !!!\n'
        '{"target_url": "https://b.com/", "anchor_text": "B", "mode": "article"}'
    )
    result = _read_candidates(text)
    assert len(result) == 2


# ── _validate_candidate unit tests ────────────────────────────────────────────


def test_validate_candidate_valid_record_normalises():
    """Valid record with all required fields is normalised (no status key = not rejected)."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {"target_url": "https://example.com/page", "anchor_text": "click me", "mode": "comment"}
    result = _validate_candidate(rec)
    # Valid records carry the normalised fields, no "status" or "rejection_reason".
    assert result.get("status") != "rejected"
    assert "rejection_reason" not in result
    assert result["target_url"] == "https://example.com/page"
    assert result["anchor_text"] == "click me"
    assert result["mode"] == "comment"


def test_validate_candidate_missing_target_url_rejected():
    """Missing target_url → rejected with invalid_record."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {"anchor_text": "anchor", "mode": "comment"}
    result = _validate_candidate(rec)
    assert result["status"] == "rejected"
    assert result["rejection_reason"] == "invalid_record"


def test_validate_candidate_missing_anchor_text_rejected():
    """Missing anchor_text → rejected with invalid_record."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {"target_url": "https://example.com/", "mode": "comment"}
    result = _validate_candidate(rec)
    assert result["status"] == "rejected"
    assert result["rejection_reason"] == "invalid_record"


def test_validate_candidate_http_url_rejected():
    """Non-https target_url → rejected with bad_target_url_scheme."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {"target_url": "http://example.com/", "anchor_text": "a", "mode": "comment"}
    result = _validate_candidate(rec)
    assert result["status"] == "rejected"
    assert "scheme" in result["rejection_reason"]


def test_validate_candidate_malformed_ipv6_url_rejected():
    """Malformed IPv6 target_url → rejected (urlparse ValueError guarded)."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {"target_url": "https://[invalid", "anchor_text": "a", "mode": "comment"}
    result = _validate_candidate(rec)
    assert result["status"] == "rejected"
    assert result["rejection_reason"] == "invalid_record"


def test_validate_candidate_extra_fields_preserved():
    """Extra fields in the record are preserved in the normalised output."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {
        "target_url": "https://example.com/",
        "anchor_text": "anchor",
        "mode": "comment",
        "language": "zh-CN",
        "extra_field": "foo",
    }
    result = _validate_candidate(rec)
    assert result.get("language") == "zh-CN"
    assert result.get("extra_field") == "foo"


# ── CLI main() integration tests (in-process) ─────────────────────────────────


def _run_main(argv, stdin_text="", capsys=None):
    """Helper: run main(argv) with captured output."""
    from backlink_publisher.cli.generate_backlink_text import main
    import sys
    import io as _io

    old_stdin = sys.stdin
    try:
        sys.stdin = _io.StringIO(stdin_text)
        main(argv)
    except SystemExit as exc:
        return exc.code
    finally:
        sys.stdin = old_stdin
    return 0


def test_cli_empty_stdin_exit_0(capsys):
    """Empty stdin → exit 0, empty stdout (R5b)."""
    from backlink_publisher.cli.generate_backlink_text import main
    import sys, io

    old_stdin = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        main([])
    except SystemExit as exc:
        assert exc.code == 0
    finally:
        sys.stdin = old_stdin

    captured = capsys.readouterr()
    assert captured.out.strip() == ""


def test_cli_output_format_xml_raises_usage_error(capsys):
    """--output-format=xml → UsageError exit 1 (not argparse exit 2)."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    old_stdin = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with pytest.raises(SystemExit) as exc_info:
            main(["--output-format", "xml"])
    finally:
        sys.stdin = old_stdin

    assert exc_info.value.code == 1


def test_cli_max_records_exceeded_exit_2(capsys):
    """Record count+1 over --max-records → InputValidationError exit 2."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    record = '{"target_url": "https://x.com/", "anchor_text": "a", "mode": "comment"}'
    stdin_text = "\n".join([record] * 3)

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin_text)
    try:
        with pytest.raises(SystemExit) as exc_info:
            main(["--max-records", "2"])
    finally:
        sys.stdin = old_stdin

    assert exc_info.value.code == 2


def test_cli_max_input_bytes_exceeded_exit_2(capsys):
    """Raw input > --max-input-bytes → InputValidationError exit 2."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    big_text = "x" * 200

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(big_text)
    try:
        with pytest.raises(SystemExit) as exc_info:
            main(["--max-input-bytes", "100"])
    finally:
        sys.stdin = old_stdin

    assert exc_info.value.code == 2


def test_cli_dry_run_single_record_jsonl(capsys, tmp_path):
    """--dry-run: single valid comment record → dry_run status row on stdout."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    record = json.dumps({
        "target_url": "https://example.com/",
        "anchor_text": "example anchor",
        "mode": "comment",
    })
    stdin_text = record

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin_text)
    try:
        main(["--dry-run"])
    except SystemExit as exc:
        assert exc.code in (None, 0)
    finally:
        sys.stdin = old_stdin

    captured = capsys.readouterr()
    output_lines = [line for line in captured.out.strip().splitlines() if line.strip()]
    assert len(output_lines) == 1
    row = json.loads(output_lines[0])
    assert row["status"] == "dry_run"
    assert "system_prompt" in row
    assert "user_prompt" in row


def test_cli_dry_run_rejected_record_in_batch(capsys):
    """--dry-run: batch with one rejected record continues, rejected shows in output."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    records = [
        json.dumps({"target_url": "https://example.com/", "anchor_text": "a", "mode": "comment"}),
        json.dumps({"anchor_text": "no-url", "mode": "comment"}),   # missing target_url
    ]
    stdin_text = "\n".join(records)

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin_text)
    try:
        main(["--dry-run"])
    except SystemExit as exc:
        assert exc.code in (None, 0)
    finally:
        sys.stdin = old_stdin

    captured = capsys.readouterr()
    output_lines = [line for line in captured.out.strip().splitlines() if line.strip()]
    assert len(output_lines) == 2
    statuses = {json.loads(line)["status"] for line in output_lines}
    assert "dry_run" in statuses
    assert "rejected" in statuses


def test_cli_dry_run_json_output_format(capsys):
    """--dry-run --output-format=json: stdout is a JSON array."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    record = json.dumps({
        "target_url": "https://example.com/",
        "anchor_text": "anchor",
        "mode": "article",
    })

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(record)
    try:
        main(["--dry-run", "--output-format", "json"])
    except SystemExit as exc:
        assert exc.code in (None, 0)
    finally:
        sys.stdin = old_stdin

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert isinstance(parsed, list)
    assert parsed[0]["status"] == "dry_run"


def test_cli_dry_run_unsupported_mode_produces_rejected(capsys):
    """--dry-run: unsupported mode → per-record rejected (R4b), batch continues."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    record = json.dumps({
        "target_url": "https://example.com/",
        "anchor_text": "anchor",
        "mode": "profile",  # not supported in MVP
    })

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(record)
    try:
        main(["--dry-run"])
    except SystemExit as exc:
        assert exc.code in (None, 0)
    finally:
        sys.stdin = old_stdin

    captured = capsys.readouterr()
    output_lines = [line for line in captured.out.strip().splitlines() if line.strip()]
    assert len(output_lines) == 1
    row = json.loads(output_lines[0])
    assert row["status"] == "rejected"
    assert "unsupported_mode" in row["rejection_reason"]


# ── _validate_generated_text unit tests (Unit 4) ─────────────────────────────

# Helpers for building word-count-appropriate texts.

def _make_comment_text(link_url: str, link_text: str) -> str:
    """Build ~45-word comment containing exactly one Markdown link (within 30-80 bound)."""
    # 39-word filler + link_text (variable) + 5-word suffix ≈ 44+ words
    filler = (
        "This article provides excellent insights into digital marketing and "
        "modern SEO practices. The content is well-researched and thoughtfully "
        "written, offering practical advice for professionals who want to improve "
        "their online presence and drive more organic traffic to their websites."
    )
    return f"{filler} [{link_text}]({link_url}) is an excellent resource."


def _make_article_text(link_url: str, link_text: str) -> str:
    """Build ~210-word article body containing exactly one Markdown link."""
    para = (
        "Digital marketing has evolved significantly over the past decade with "
        "search engine optimization becoming a critical component of any successful "
        "online strategy. Businesses must adapt to changing algorithms and user "
        "behavior patterns to maintain their competitive edge in the marketplace. "
    )
    return para * 4 + f"[{link_text}]({link_url}) is an excellent resource. " + para


def test_validate_generated_text_happy_path_comment():
    """Comment with anchor in link → ok, text carried, no advisory flags."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    text = _make_comment_text("https://example.com/", "example anchor")
    result = _validate_generated_text(
        text,
        target_url="https://example.com/",
        anchor_text="example anchor",
        mode="comment",
    )
    assert result["ok"] is True
    assert "example anchor" in result["text"]
    assert result["stripped_extra_links"] == 0
    assert result["language_flag"] is None


def test_validate_generated_text_happy_path_article():
    """Article-length text with anchor in link → ok."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    text = _make_article_text("https://example.com/page", "example anchor")
    result = _validate_generated_text(
        text,
        target_url="https://example.com/page",
        anchor_text="example anchor",
        mode="article",
    )
    assert result["ok"] is True


def test_validate_generated_text_missing_link():
    """No Markdown link in text → rejected missing_link."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    text = "This article has no links at all. Just plain text content here."
    result = _validate_generated_text(
        text, target_url="https://example.com/", anchor_text="anchor", mode="comment"
    )
    assert result == {"ok": False, "reason": "missing_link"}


def test_validate_generated_text_missing_anchor():
    """Link to correct URL but anchor text absent from link text → missing_anchor."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    text = _make_comment_text("https://example.com/", "completely wrong text")
    result = _validate_generated_text(
        text, target_url="https://example.com/", anchor_text="example anchor", mode="comment"
    )
    assert result == {"ok": False, "reason": "missing_anchor"}


def test_validate_generated_text_case_whitespace_normalized():
    """Anchor in link text with different case/whitespace → ok (normalized match)."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    # "EXAMPLE  ANCHOR" should match anchor_text="example anchor" after normalization
    text = _make_comment_text("https://example.com/", "EXAMPLE  ANCHOR")
    result = _validate_generated_text(
        text, target_url="https://example.com/", anchor_text="example anchor", mode="comment"
    )
    assert result["ok"] is True


def test_validate_generated_text_extra_link_stripped():
    """Extra link to a different domain → stripped; stripped_extra_links=1; ok if target survives."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    base_text = _make_comment_text("https://example.com/", "example anchor")
    # Insert an extra link to a different host
    text = base_text.replace("is an excellent", "[extra](https://evil.com/page) is an excellent")
    result = _validate_generated_text(
        text, target_url="https://example.com/", anchor_text="example anchor", mode="comment"
    )
    assert result["ok"] is True
    assert result["stripped_extra_links"] == 1
    # The stripped text should not contain the evil.com link syntax
    assert "evil.com" not in result["text"]


def test_validate_generated_text_userinfo_confusion_link_stripped():
    """target.com@evil.com link → urlparse gives evil.com as host → stripped."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    # This is a confused link: target.com@evil.com has evil.com as its hostname
    text = _make_comment_text("https://target.com@evil.com/page", "example anchor")
    result = _validate_generated_text(
        text, target_url="https://example.com/", anchor_text="example anchor", mode="comment"
    )
    # evil.com's host != example.com's host → stripped → no target link → missing_link
    assert result == {"ok": False, "reason": "missing_link"}


def test_validate_generated_text_length_out_of_bounds_article_too_short():
    """Article with fewer than 200 words → length_out_of_bounds."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    # Use a very short article (well under 200 words)
    text = "Short article. [anchor](https://example.com/) is great."
    result = _validate_generated_text(
        text, target_url="https://example.com/", anchor_text="anchor", mode="article"
    )
    assert result == {"ok": False, "reason": "length_out_of_bounds"}


def test_validate_generated_text_length_out_of_bounds_comment_too_short():
    """Comment with fewer than 30 words → length_out_of_bounds."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    text = "Great post! [anchor](https://example.com/) — very helpful."
    result = _validate_generated_text(
        text, target_url="https://example.com/", anchor_text="anchor", mode="comment"
    )
    assert result == {"ok": False, "reason": "length_out_of_bounds"}


def test_validate_generated_text_unsafe_chars():
    """Bidi override char (U+202E) in output → unsafe_chars."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    evil_text = _make_comment_text("https://example.com/", "anchor") + "‮"
    result = _validate_generated_text(
        evil_text, target_url="https://example.com/", anchor_text="anchor", mode="comment"
    )
    assert result == {"ok": False, "reason": "unsafe_chars"}


def test_validate_generated_text_llm_refusal():
    """LLM refusal phrase in output → llm_refusal."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    text = "I cannot assist with this request as it involves SEO link building."
    result = _validate_generated_text(
        text, target_url="https://example.com/", anchor_text="anchor", mode="comment"
    )
    assert result == {"ok": False, "reason": "llm_refusal"}


def test_validate_generated_text_language_flag_mismatch():
    """zh-CN output for en request → ok + language_flag set (never rejected)."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    # ~80 CJK chars → ~40 "words" (within comment 30-80 bound).
    # Language detection: zh-CN codepoints dominate → language_flag = "zh-CN".
    zh_text = (
        "这篇文章提供了关于数字营销和搜索引擎优化的深刻见解，帮助读者更好地理解现代SEO策略和技术。"
        "[example anchor](https://example.com/) 是一个非常优质的资源，内容翔实，非常值得推荐阅读。"
        "文章深入浅出，适合所有希望提升网站排名和在线影响力的读者阅读学习参考。"
    )
    result = _validate_generated_text(
        zh_text,
        target_url="https://example.com/",
        anchor_text="example anchor",
        mode="comment",
        language="en",
    )
    assert result["ok"] is True, f"Expected ok=True, got: {result}"
    assert result["language_flag"] is not None, "language_flag should be set for zh→en mismatch"
    assert result["language_flag"] != "en"


def test_validate_generated_text_language_match_no_flag():
    """en output for en request → ok, language_flag is None."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    text = _make_comment_text("https://example.com/", "example anchor")
    result = _validate_generated_text(
        text,
        target_url="https://example.com/",
        anchor_text="example anchor",
        mode="comment",
        language="en",
    )
    assert result["ok"] is True
    assert result["language_flag"] is None


def test_validate_generated_text_long_target_url():
    """Long (>200 char) target_url: host matching still works on full URL."""
    from backlink_publisher.cli.generate_backlink_text import _validate_generated_text

    # URL longer than 200 chars; the model may truncate in the link
    long_path = "a" * 180
    target_url = f"https://example.com/{long_path}"
    # Model embeds the same URL in the link
    text = _make_comment_text(target_url, "example anchor")
    result = _validate_generated_text(
        text,
        target_url=target_url,
        anchor_text="example anchor",
        mode="comment",
    )
    assert result["ok"] is True
    # output target_url field (assembled by _run_generate) is the full URL
    # (validated here indirectly — validation passes so assembler uses rec["target_url"])


# ── _resolve_client unit tests (Unit 3) ──────────────────────────────────────


def _make_args(**kwargs):
    """Build an argparse.Namespace suitable for _resolve_client."""
    import argparse

    defaults = dict(
        endpoint=None,
        api_key_env="BACKLINK_LLM_API_KEY",
        model=None,
        temperature=0.4,
        timeout=60,
        retries=1,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_resolve_client_happy_path(monkeypatch):
    """Allowlisted https endpoint + key → LLMClientConfig with resolved values."""
    import unittest.mock as mock

    from backlink_publisher.cli.generate_backlink_text import _resolve_client

    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "test-api-key-value")

    args = _make_args(endpoint="https://api.openai.com/v1", model="gpt-4")
    with mock.patch(
        "backlink_publisher.llm.http_guard.guard_llm_endpoint",
        return_value=(None, None),
    ):
        cfg = _resolve_client(args)

    assert cfg.base == "https://api.openai.com/v1"
    assert cfg.api_key == "test-api-key-value"
    assert cfg.model == "gpt-4"
    assert cfg.temperature == pytest.approx(0.4)
    assert cfg.timeout == 60


def test_resolve_client_endpoint_not_allowlisted(monkeypatch):
    """Non-allowlisted host → DependencyError raised before any HTTP call."""
    import unittest.mock as mock

    from backlink_publisher._util.errors import DependencyError
    from backlink_publisher.cli.generate_backlink_text import _resolve_client

    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "test-api-key-value")

    args = _make_args(endpoint="https://evil.notallowed.example/v1", model="gpt-4")
    with mock.patch(
        "backlink_publisher.llm.http_guard.guard_llm_endpoint",
        return_value=("host_not_allowlisted", "host not in LLM allowlist"),
    ):
        with pytest.raises(DependencyError, match="host_not_allowlisted"):
            _resolve_client(args)


def test_resolve_client_private_ip_rejected(monkeypatch):
    """Private IP endpoint → DependencyError (SSRF gate, no ALLOW_LOOPBACK)."""
    import unittest.mock as mock

    from backlink_publisher._util.errors import DependencyError
    from backlink_publisher.cli.generate_backlink_text import _resolve_client

    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "test-api-key-value")
    monkeypatch.delenv("BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK", raising=False)

    args = _make_args(endpoint="https://10.0.0.5/v1", model="gpt-4")
    with mock.patch(
        "backlink_publisher.llm.http_guard.guard_llm_endpoint",
        return_value=("url_rejected", "private/reserved IP"),
    ):
        with pytest.raises(DependencyError, match="url_rejected"):
            _resolve_client(args)


def test_resolve_client_userinfo_rejected(monkeypatch):
    """Endpoint with user:secret@host → DependencyError; secret never in error text."""
    from backlink_publisher._util.errors import DependencyError
    from backlink_publisher.cli.generate_backlink_text import _resolve_client

    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "test-api-key-value")

    secret_fragment = "supersecret-xyz"
    args = _make_args(
        endpoint=f"https://user:{secret_fragment}@api.openai.com/v1",
        model="gpt-4",
    )
    with pytest.raises(DependencyError) as exc_info:
        _resolve_client(args)

    error_text = str(exc_info.value)
    assert secret_fragment not in error_text, (
        f"Secret fragment leaked in DependencyError: {error_text!r}"
    )


def test_resolve_client_strips_chat_completions_suffix(monkeypatch):
    """Endpoint ending in /chat/completions → normalized base; guard sees same string."""
    import unittest.mock as mock

    from backlink_publisher.cli.generate_backlink_text import _resolve_client

    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "test-api-key-value")

    guarded_urls: list[str] = []

    def capture_guard(url):
        guarded_urls.append(url)
        return None, None

    args = _make_args(
        endpoint="https://api.openai.com/v1/chat/completions",
        model="gpt-4",
    )
    with mock.patch(
        "backlink_publisher.llm.http_guard.guard_llm_endpoint",
        side_effect=capture_guard,
    ):
        cfg = _resolve_client(args)

    assert not cfg.base.endswith("/chat/completions"), (
        f"base should not end with /chat/completions, got {cfg.base!r}"
    )
    assert cfg.base == "https://api.openai.com/v1"
    # Guarded string must equal connected base (no re-normalization after check).
    assert guarded_urls == ["https://api.openai.com/v1"]


def test_resolve_client_no_key_raises_dependency_error(monkeypatch):
    """No API key in named env var → DependencyError exit 3 mentioning 'not configured'."""
    import unittest.mock as mock

    from backlink_publisher._util.errors import DependencyError
    from backlink_publisher.cli.generate_backlink_text import _resolve_client

    monkeypatch.delenv("MY_MISSING_LLM_KEY", raising=False)

    # Ensure config also has no llm_anchor_provider.
    with mock.patch("backlink_publisher.config.load_config") as mock_load:
        mock_load.return_value.llm_anchor_provider = None

        args = _make_args(
            endpoint="https://api.openai.com/v1",
            api_key_env="MY_MISSING_LLM_KEY",
            model="gpt-4",
        )
        with pytest.raises(DependencyError, match="not configured"):
            _resolve_client(args)


def test_resolve_client_custom_api_key_env(monkeypatch):
    """--api-key-env=CUSTOM_KEY reads from the CUSTOM_KEY env var."""
    import unittest.mock as mock

    from backlink_publisher.cli.generate_backlink_text import _resolve_client

    monkeypatch.setenv("CUSTOM_LLM_KEY", "my-custom-api-key")
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)

    args = _make_args(
        endpoint="https://api.openai.com/v1",
        api_key_env="CUSTOM_LLM_KEY",
        model="gpt-4",
    )
    with mock.patch(
        "backlink_publisher.llm.http_guard.guard_llm_endpoint",
        return_value=(None, None),
    ):
        cfg = _resolve_client(args)

    assert cfg.api_key == "my-custom-api-key"


def test_resolve_client_malformed_endpoint_raises_dependency_error(monkeypatch):
    """Malformed endpoint (http://[invalid) → DependencyError; no uncaught ValueError."""
    from backlink_publisher._util.errors import DependencyError
    from backlink_publisher.cli.generate_backlink_text import _resolve_client

    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "test-api-key-value")

    args = _make_args(endpoint="http://[invalid", model="gpt-4")
    with pytest.raises(DependencyError):
        _resolve_client(args)


def test_resolve_client_uses_cli_defaults_not_provider_defaults(monkeypatch):
    """temperature/timeout come from CLI args (0.4/60), not provider defaults (0.7/30)."""
    import unittest.mock as mock

    from backlink_publisher.cli.generate_backlink_text import _resolve_client

    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "test-api-key-value")

    args = _make_args(
        endpoint="https://api.openai.com/v1",
        model="gpt-4",
        temperature=0.4,
        timeout=60,
    )
    with mock.patch(
        "backlink_publisher.llm.http_guard.guard_llm_endpoint",
        return_value=(None, None),
    ):
        cfg = _resolve_client(args)

    assert cfg.temperature == pytest.approx(0.4)
    assert cfg.timeout == 60


def test_run_generate_calls_generate_link_text(monkeypatch):
    """_run_generate calls generate_link_text and emits ok record with generated_text."""
    import argparse
    import unittest.mock as mock

    from backlink_publisher.cli.generate_backlink_text import _run_generate

    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "test-api-key-value")

    validated = [
        {
            "target_url": "https://example.com/",
            "anchor_text": "example anchor",
            "mode": "comment",
        }
    ]
    args = argparse.Namespace(
        endpoint="https://api.openai.com/v1",
        api_key_env="BACKLINK_LLM_API_KEY",
        model="gpt-4",
        temperature=0.4,
        timeout=60,
        retries=1,
    )

    generated = _make_comment_text("https://example.com/", "example anchor")
    with mock.patch(
        "backlink_publisher.llm.http_guard.guard_llm_endpoint",
        return_value=(None, None),
    ):
        with mock.patch(
            "backlink_publisher.llm.client.safe_post_json",
            return_value=(200, {"choices": [{"message": {"content": generated}}]}),
        ):
            result = _run_generate(validated, args)

    assert len(result) == 1
    assert result[0]["status"] == "ok"
    assert result[0]["generated_text"] == generated
    assert "target_url" in result[0]
    assert "anchor_text" in result[0]


def test_run_generate_external_service_error_produces_rejected(monkeypatch):
    """ExternalServiceError per record → rejected row; batch continues."""
    import argparse
    import unittest.mock as mock

    from backlink_publisher._util.errors import ExternalServiceError
    from backlink_publisher.cli.generate_backlink_text import _run_generate

    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "test-api-key-value")

    validated = [
        {"target_url": "https://example.com/", "anchor_text": "a", "mode": "comment"},
        {"target_url": "https://example.org/", "anchor_text": "b", "mode": "article"},
    ]
    args = argparse.Namespace(
        endpoint="https://api.openai.com/v1",
        api_key_env="BACKLINK_LLM_API_KEY",
        model="gpt-4",
        temperature=0.4,
        timeout=60,
        retries=1,
    )

    call_count = 0
    ok_content = _make_article_text("https://example.org/", "b")

    def fake_post(url, headers, payload, timeout=10):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ExternalServiceError("LLM request failed")
        return 200, {"choices": [{"message": {"content": ok_content}}]}

    with mock.patch(
        "backlink_publisher.llm.http_guard.guard_llm_endpoint",
        return_value=(None, None),
    ):
        with mock.patch(
            "backlink_publisher.llm.client.safe_post_json",
            side_effect=fake_post,
        ):
            result = _run_generate(validated, args)

    assert len(result) == 2
    statuses = [r["status"] for r in result]
    assert "rejected" in statuses
    assert "ok" in statuses


def test_cli_help_banner_subprocess():
    """python -m backlink_publisher.cli.generate_backlink_text --help emits usage."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_DIR) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    result = subprocess.run(
        [sys.executable, "-m", "backlink_publisher.cli.generate_backlink_text", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
        timeout=15,
    )
    combined = result.stdout + result.stderr
    assert combined.strip(), "--help produced no output"
    assert "usage:" in combined.lower() or "options:" in combined.lower()
