"""Tests for plan-backlinks."""

from __future__ import annotations

import json
import re
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.cli.plan_backlinks import main
from backlink_publisher.errors import InputValidationError


def _stderr_without_warnings(stderr: str) -> str:
    """Strip benign WARN + RECON log lines so tests can assert on real errors only.

    RECON is the always-on Silent-Drop Tripwire reconciliation event emitted
    at end-of-run regardless of --log-level. WARN lines are anchor-keyword
    fallback notices and similar advisory signals."""
    lines = [
        line for line in stderr.splitlines()
        if line
        and '"level": "WARN"' not in line
        and '"level": "RECON"' not in line
    ]
    return "\n".join(lines)


def _run_plan(input_data: str, argv: list[str] | None = None) -> tuple[str, str, int]:
    """Run plan-backlinks with given stdin data. Returns (stdout, stderr, exit_code)."""
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        sys.stdin = StringIO(input_data)
        out = StringIO()
        err = StringIO()
        sys.stdout = out
        sys.stderr = err
        try:
            main(argv or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def test_plan_three_rows():
    """plan-backlinks can read 3 JSONL rows and output 3 planned payload rows."""
    seeds = [
        {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
            "topic": "Test Topic",
        },
        {
            "target_url": "https://blog.example.org/post",
            "main_domain": "https://blog.example.org",
            "language": "zh-CN",
            "platform": "blogger",
            "url_mode": "C",
            "publish_mode": "publish",
        },
        {
            "target_url": "https://tech.ru/overview",
            "main_domain": "https://tech.ru",
            "language": "ru",
            "platform": "medium",
            "url_mode": "B",
            "publish_mode": "draft",
        },
    ]
    input_data = "\n".join(json.dumps(s) for s in seeds)
    stdout, stderr, code = _run_plan(input_data)

    assert code == 0, f"Expected exit 0, got {code}. stderr: {stderr}"
    # Anchor-keyword fallback emits a WARN per article when the target site has
    # no anchor_keywords configured — that is the documented signal, not noise.
    assert _stderr_without_warnings(stderr) == "", (
        f"Expected only WARN lines on stderr, got: {stderr}"
    )

    lines = stdout.strip().split("\n")
    assert len(lines) == 3, f"Expected 3 output rows, got {len(lines)}"

    for line in lines:
        payload = json.loads(line)
        assert "id" in payload
        assert "title" in payload
        assert "content_markdown" in payload
        assert "links" in payload
        assert 5 <= len(payload["links"]) <= 8
        assert payload["main_domain"] in payload["content_markdown"]


def test_plan_empty_input():
    """Empty input must produce an error on stderr and non-zero exit."""
    stdout, stderr, code = _run_plan("")
    assert code == 2
    assert "empty input" in stderr.lower()
    assert stdout == ""


def test_plan_malformed_json():
    """Malformed JSON in input must produce error."""
    stdout, stderr, code = _run_plan("{broken\n")
    assert code == 2
    assert "malformed" in stderr.lower()
    assert stdout == ""


def test_plan_unsupported_platform():
    """platform=linkedin must be rejected with exit code 2."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "linkedin",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    stdout, stderr, code = _run_plan(json.dumps(seed))
    assert code == 2
    assert "linkedin" in stderr.lower()
    assert stdout == ""


def test_plan_missing_required_field():
    """Missing required field must produce error."""
    seed = {
        "target_url": "https://example.com/article",
        # missing main_domain
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    stdout, stderr, code = _run_plan(json.dumps(seed))
    assert code == 2
    assert "main_domain" in stderr.lower()
    assert stdout == ""


def test_plan_invalid_url_mode():
    """Invalid url_mode must produce error."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "Z",
        "publish_mode": "draft",
    }
    stdout, stderr, code = _run_plan(json.dumps(seed))
    assert code == 2
    assert "url_mode" in stderr.lower()
    assert stdout == ""


def test_plan_all_url_modes():
    """All URL modes (A, B, C) must produce valid output."""
    for mode in ("A", "B", "C"):
        seed = {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": mode,
            "publish_mode": "draft",
        }
        stdout, stderr, code = _run_plan(json.dumps(seed))
        assert code == 0, f"Mode {mode} failed: {stderr}"
        payload = json.loads(stdout.strip())
        assert payload["url_mode"] == mode
        assert 5 <= len(payload["links"]) <= 8
        assert payload["main_domain"] in payload["content_markdown"]


def test_plan_all_languages():
    """All supported languages must produce valid output."""
    for lang in ("en", "zh-CN", "ru"):
        seed = {
            "target_url": f"https://{lang}.example.com/article",
            "main_domain": f"https://{lang}.example.com",
            "language": lang,
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        stdout, stderr, code = _run_plan(json.dumps(seed))
        assert code == 0, f"Language {lang} failed: {stderr}"
        payload = json.loads(stdout.strip())
        assert payload["language"] == lang
        assert len(payload["title"]) > 0
        assert len(payload["content_markdown"]) > 20


def test_plan_stable_deterministic_id():
    """Same seed input must always produce the same id."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    _, _, code1 = _run_plan(json.dumps(seed))
    stdout1, _, _ = _run_plan(json.dumps(seed))
    stdout2, _, _ = _run_plan(json.dumps(seed))
    assert stdout1 == stdout2


def test_plan_main_domain_natural_placement():
    """main_domain must appear naturally in content, not at very start or end."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    stdout, stderr, code = _run_plan(json.dumps(seed))
    assert code == 0
    payload = json.loads(stdout.strip())
    content = payload["content_markdown"]
    domain = "https://example.com"
    assert domain in content
    # Not at the very start (after leading markdown)
    stripped = content.lstrip("# ")
    assert not stripped.startswith(domain), "main_domain should not be at the very start"
    # Not at the very end
    assert not content.rstrip().endswith(domain), "main_domain should not be at the very end"


@pytest.mark.parametrize("language,url_mode", [
    ("en", "A"), ("en", "B"), ("en", "C"),
    ("zh-CN", "A"), ("zh-CN", "B"), ("zh-CN", "C"),
    ("ru", "A"), ("ru", "B"), ("ru", "C"),
])
def test_all_main_domain_occurrences_are_hyperlinked(language, url_mode):
    """Every main_domain URL in content_markdown must be wrapped as [anchor](url), not bare text."""
    import re
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": language,
        "platform": "blogger",
        "url_mode": url_mode,
        "publish_mode": "draft",
    }
    stdout, _, code = _run_plan(json.dumps(seed))
    assert code == 0
    payload = json.loads(stdout.strip())
    content = payload["content_markdown"]

    # No bare URL — every main_domain must be preceded by ]( (inside a Markdown link)
    bare = re.findall(r'(?<!\]\()https://example\.com[/]?(?!\))', content)
    assert not bare, (
        f"[{language}/{url_mode}] Found {len(bare)} bare URL(s) not wrapped as hyperlinks: {bare}\n"
        f"Content:\n{content[:400]}"
    )

    # At least 2 proper markdown links to main_domain in article body
    links = re.findall(r'\[[^\]]+\]\(https://example\.com[^)]*\)', content)
    assert len(links) >= 2, (
        f"[{language}/{url_mode}] Expected ≥2 markdown links, found {len(links)}: {links}"
    )


def test_plan_no_stderr_on_success():
    """On success, stderr must contain no errors (WARN lines are allowed)."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    _, stderr, code = _run_plan(json.dumps(seed))
    assert code == 0
    assert _stderr_without_warnings(stderr) == "", (
        f"Expected only WARN lines on stderr, got: {stderr!r}"
    )


@pytest.mark.parametrize("language,url_mode,same_url", [
    ("en",    "A", True),
    ("en",    "A", False),
    ("zh-CN", "A", True),
    ("zh-CN", "A", False),
    ("ru",    "A", True),
    ("ru",    "A", False),
    ("zh-CN", "B", False),
    ("zh-CN", "C", False),
])
def test_target_site_link_density(language, url_mode, same_url):
    """Every article must contain ≥ 6 hyperlinks pointing to the target site (A+B+C ≥ 6)."""
    main_domain = "https://example.com"
    target_url = main_domain if same_url else "https://example.com/article"
    seed = {
        "target_url": target_url,
        "main_domain": main_domain,
        "language": language,
        "platform": "blogger",
        "url_mode": url_mode,
        "publish_mode": "draft",
    }
    stdout, _, code = _run_plan(json.dumps(seed))
    assert code == 0
    content = json.loads(stdout.strip())["content_markdown"]

    links = re.findall(r'\[[^\]]+\]\(https://example\.com[^)]*\)', content)
    assert len(links) >= 6, (
        f"[{language}/{url_mode}/same={same_url}] Expected ≥6 target-site links, "
        f"found {len(links)}: {links}"
    )


def test_plan_output_fields():
    """Output must contain all required fields."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
        "topic": "Test",
    }
    stdout, stderr, code = _run_plan(json.dumps(seed))
    assert code == 0
    payload = json.loads(stdout.strip())
    required = ["id", "platform", "language", "publish_mode", "target_url",
                "main_domain", "url_mode", "title", "slug", "excerpt", "tags",
                "content_markdown", "links", "seo"]
    for field in required:
        assert field in payload, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# SEO anchor_keywords integration (R2/R3/R4)
# ---------------------------------------------------------------------------


from backlink_publisher.cli.plan_backlinks import _generate_payload  # noqa: E402
from backlink_publisher.config import Config  # noqa: E402


def _make_config(main_domain: str, keywords: list[str]) -> Config:
    return Config(target_anchor_keywords={main_domain.rstrip("/"): keywords})


def _seed(url_mode: str = "A", language: str = "en") -> dict:
    return {
        "target_url": "https://target.example.com/post",
        "main_domain": "https://target.example.com",
        "language": language,
        "platform": "medium",
        "url_mode": url_mode,
        "publish_mode": "draft",
        "topic": "Test",
    }


def test_anchor_keywords_used_in_links_main_domain_and_target_kinds():
    cfg = _make_config(
        "https://target.example.com",
        ["BrandWord", "HeadTerm", "LongTailPhrase"],
    )
    payload = _generate_payload(_seed(url_mode="A"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    target_link = next(l for l in payload["links"] if l["kind"] == "target")
    # url_mode A → offset 0 → anchors[0]=BrandWord, anchors[1]=HeadTerm
    assert main_link["anchor"] == "BrandWord"
    assert target_link["anchor"] == "HeadTerm"


def test_anchor_keywords_url_mode_b_offsets():
    cfg = _make_config(
        "https://target.example.com",
        ["BrandWord", "HeadTerm", "LongTailPhrase"],
    )
    payload = _generate_payload(_seed(url_mode="B"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    target_link = next(l for l in payload["links"] if l["kind"] == "target")
    # offset 1 → anchors[0]=HeadTerm, anchors[1]=LongTailPhrase
    assert main_link["anchor"] == "HeadTerm"
    assert target_link["anchor"] == "LongTailPhrase"


def test_anchor_keywords_url_mode_c_wraps_around():
    cfg = _make_config(
        "https://target.example.com",
        ["BrandWord", "HeadTerm", "LongTailPhrase"],
    )
    payload = _generate_payload(_seed(url_mode="C"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    target_link = next(l for l in payload["links"] if l["kind"] == "target")
    # offset 2 → anchors[0]=LongTailPhrase, anchors[1]=BrandWord (wraps)
    assert main_link["anchor"] == "LongTailPhrase"
    assert target_link["anchor"] == "BrandWord"


def test_anchor_keywords_appear_in_body_markdown():
    cfg = _make_config(
        "https://target.example.com",
        ["UniqueAnchorAlpha", "UniqueAnchorBeta"],
    )
    payload = _generate_payload(_seed(url_mode="A", language="en"), config=cfg)
    md = payload["content_markdown"]
    assert "[UniqueAnchorAlpha](https://target.example.com)" in md
    assert "[UniqueAnchorBeta](https://target.example.com)" in md


def test_anchor_keywords_appear_in_excerpt():
    cfg = _make_config(
        "https://target.example.com",
        ["ExcerptKeyword", "AnotherKw"],
    )
    payload = _generate_payload(_seed(url_mode="A"), config=cfg)
    # Excerpt uses anchors[0] in its single anchored slot
    assert "[ExcerptKeyword](https://target.example.com)" in payload["excerpt"]


def test_anchor_keywords_fallback_when_no_pool(caplog):
    cfg = Config()  # no target_anchor_keywords entry
    with caplog.at_level("WARNING"):
        payload = _generate_payload(_seed(url_mode="A"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    # Falls back to bare-domain label
    assert main_link["anchor"] == "target.example.com"
    # No keyword leakage; the markdown still references the domain
    assert "target.example.com" in payload["content_markdown"]


def test_anchor_keywords_fallback_when_pool_empty():
    cfg = _make_config("https://target.example.com", [])  # explicit empty
    payload = _generate_payload(_seed(url_mode="A"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    assert main_link["anchor"] == "target.example.com"


def test_anchor_keywords_single_keyword_repeats():
    cfg = _make_config("https://target.example.com", ["OnlyOne"])
    payload = _generate_payload(_seed(url_mode="A"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    target_link = next(l for l in payload["links"] if l["kind"] == "target")
    # With a 1-element pool both slots get the same keyword (deterministic, OK)
    assert main_link["anchor"] == "OnlyOne"
    assert target_link["anchor"] == "OnlyOne"


@pytest.mark.parametrize("language,url_mode", [
    (lang, mode) for lang in ("en", "zh-CN", "ru") for mode in ("A", "B", "C")
])
def test_anchor_keywords_all_languages_and_modes(language, url_mode):
    """Body templates for every language+mode combination must wire keywords through."""
    cfg = _make_config(
        "https://target.example.com",
        ["KeywordA", "KeywordB", "KeywordC"],
    )
    payload = _generate_payload(
        _seed(url_mode=url_mode, language=language), config=cfg,
    )
    md = payload["content_markdown"]
    # Both anchor positions must appear inside [<keyword>](main_domain) constructs.
    # The exact two depend on offset, but at least one of the configured
    # keywords must appear in an anchored position.
    assert any(
        f"[{kw}](https://target.example.com)" in md
        for kw in ("KeywordA", "KeywordB", "KeywordC")
    ), f"no SEO anchor keyword present in {language}/{url_mode}: {md[:200]}"
    # Bare-domain anchor must NOT appear inside link brackets pointing to main_domain
    assert "[target.example.com](https://target.example.com)" not in md


def test_anchor_keyword_distribution_across_url_modes():
    """A target site rendered across A+B+C produces ≥3 distinct anchors (R3 spec)."""
    cfg = _make_config(
        "https://target.example.com",
        ["Brand", "Head", "LongTail"],
    )
    distinct_anchors = set()
    for mode in ("A", "B", "C"):
        payload = _generate_payload(_seed(url_mode=mode), config=cfg)
        for link in payload["links"]:
            if link["kind"] in ("main_domain", "target"):
                distinct_anchors.add(link["anchor"])
    assert len(distinct_anchors) >= 3, (
        f"expected ≥3 distinct anchors across A/B/C, got {distinct_anchors}"
    )


def test_anchor_keywords_no_config_uses_fallback_silently_for_payload():
    """Calling _generate_payload with config=None should still succeed (fallback)."""
    payload = _generate_payload(_seed(url_mode="A"), config=None)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    assert main_link["anchor"] == "target.example.com"


# ── --from-csv integration ─────────────────────────────────────────────────────

def test_from_csv_generates_payloads(tmp_path):
    """--from-csv reads URLs from a file and generates payloads."""
    csv_file = tmp_path / "urls.csv"
    csv_file.write_text(
        "https://example.com/page1\nhttps://example.com/page2\n",
        encoding="utf-8",
    )
    stdout, stderr, code = _run_plan("", argv=[f"--from-csv={csv_file}"])
    assert code == 0, f"Expected 0, got {code}. stderr: {stderr}"
    lines = [l for l in stdout.strip().split("\n") if l]
    assert len(lines) == 2
    p0 = json.loads(lines[0])
    assert p0["target_url"].rstrip("/") == "https://example.com/page1"
    assert p0["main_domain"].rstrip("/") == "https://example.com"
    assert p0["platform"] == "blogger"
    assert p0["language"] == "zh-CN"


def test_from_csv_custom_defaults(tmp_path):
    """--from-csv respects --default-platform and --default-language."""
    csv_file = tmp_path / "urls.csv"
    csv_file.write_text("https://medium.com/p/abc\n", encoding="utf-8")
    stdout, stderr, code = _run_plan(
        "",
        argv=[
            f"--from-csv={csv_file}",
            "--default-platform=medium",
            "--default-language=en",
            "--default-url-mode=B",
            "--default-publish-mode=publish",
        ],
    )
    assert code == 0
    p = json.loads(stdout.strip())
    assert p["platform"] == "medium"
    assert p["language"] == "en"
    assert p["url_mode"] == "B"
    assert p["publish_mode"] == "publish"


def test_from_csv_empty_file_exits_2(tmp_path):
    """--from-csv with empty file → exit 2."""
    csv_file = tmp_path / "empty.csv"
    csv_file.write_text("", encoding="utf-8")
    stdout, stderr, code = _run_plan("", argv=[f"--from-csv={csv_file}"])
    assert code == 2


def test_from_csv_mutual_exclusion_with_input():
    """--from-csv combined with --input → exit 2."""
    import io
    stdout, stderr, code = _run_plan(
        '{"target_url": "https://a.com"}',
        argv=["--from-csv=somefile.csv", "--input=/dev/stdin"],
    )
    # Should fail before even trying to open the file
    assert code in (2, 1)


def test_from_csv_and_from_sitemap_mutually_exclusive(tmp_path):
    """--from-csv and --from-sitemap together → exit 2."""
    csv_file = tmp_path / "urls.csv"
    csv_file.write_text("https://a.com\n", encoding="utf-8")
    stdout, stderr, code = _run_plan(
        "",
        argv=[f"--from-csv={csv_file}", "--from-sitemap=https://example.com/sitemap.xml"],
    )
    assert code == 2


def test_from_sitemap_generates_payloads():
    """--from-sitemap fetches sitemap and generates payloads for each URL."""
    sitemap_xml = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://site.com/page1</loc></url>
  <url><loc>https://site.com/page2</loc></url>
</urlset>"""
    from unittest.mock import patch, MagicMock
    mock_resp = MagicMock()
    mock_resp.content = sitemap_xml
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        stdout, stderr, code = _run_plan(
            "", argv=["--from-sitemap=https://site.com/sitemap.xml"]
        )

    assert code == 0
    lines = [l for l in stdout.strip().split("\n") if l]
    assert len(lines) == 2
    urls = {json.loads(l)["target_url"].rstrip("/") for l in lines}
    assert "https://site.com/page1" in urls
    assert "https://site.com/page2" in urls


def test_from_sitemap_network_error_exits_2():
    """--from-sitemap with network error → exit 2."""
    from unittest.mock import patch
    with patch("requests.get", side_effect=ConnectionError("offline")):
        stdout, stderr, code = _run_plan(
            "", argv=["--from-sitemap=https://example.com/sitemap.xml"]
        )
    assert code == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])