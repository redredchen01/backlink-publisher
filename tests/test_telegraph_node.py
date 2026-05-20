"""Tests for ``adapters.telegraph_node.markdown_to_telegraph_nodes``.

Test scenarios trace Unit 3 of:
    docs/plans/2026-05-15-004-feat-telegraph-adapter-plan.md
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backlink_publisher.publishing.adapters.telegraph_node import (
    _is_safe_href,
    markdown_to_telegraph_nodes,
)


# ─── Happy paths ────────────────────────────────────────────────────────────


def test_basic_anchor_inside_paragraph_keeps_href_and_strips_target_rel():
    md = "Visit [our site](https://example.com/x) now."
    nodes, stats = markdown_to_telegraph_nodes(md)

    assert nodes == [
        {
            "tag": "p",
            "children": [
                "Visit ",
                {
                    "tag": "a",
                    "attrs": {"href": "https://example.com/x"},
                    "children": ["our site"],
                },
                " now.",
            ],
        }
    ]
    assert stats["anchors"] == 1
    assert stats["downgrades"] == 0
    assert "a" not in stats["downgrades_by_tag"]
    assert stats["utf8_bytes"] > 0


def test_anchor_target_blank_rel_noopener_are_stripped_from_output():
    # markdown_utils.render_to_html adds target=_blank rel=noopener to every <a>;
    # Telegraph attrs schema only accepts {href, src}, so target/rel must be
    # silently dropped at the converter layer.
    md = "[click here](https://example.com/landing)"
    nodes, _stats = markdown_to_telegraph_nodes(md)

    paragraph = nodes[0]
    anchor = paragraph["children"][0]  # type: ignore[index]
    assert anchor["tag"] == "a"
    assert set(anchor["attrs"].keys()) == {"href"}
    assert anchor["attrs"]["href"] == "https://example.com/landing"


def test_mixed_inline_emphasis_and_lists_round_trip():
    md = (
        "## not_h3 should be unwrapped\n\n"
        "### Section title\n\n"
        "Para with **bold** and *em* and a [link](https://example.com).\n\n"
        "- item one\n"
        "- item two with **bold**\n"
    )
    nodes, stats = markdown_to_telegraph_nodes(md)

    # h3 is in the whitelist; h2 (## not_h3) should be unwrapped → its
    # inner text appears at top level.
    tags = [_top_tag(n) for n in nodes]
    assert "h3" in tags
    assert "ul" in tags
    assert stats["anchors"] == 1
    assert stats["downgrades_by_tag"].get("h2", 0) == 1


# ─── Unwrap-and-recurse: <a> survives inside non-whitelist containers ───────


def test_link_inside_table_survives_via_unwrap():
    md = "| header |\n| --- |\n| cell with [link](https://example.com/inside) |"
    nodes, stats = markdown_to_telegraph_nodes(md)

    # The <a> must still be present somewhere in the tree.
    flat = json.dumps(nodes, ensure_ascii=False)
    assert '"href": "https://example.com/inside"' in flat
    assert stats["anchors"] == 1
    # Table scaffolding (table / thead / tbody / tr / th / td) should each
    # be counted as a downgrade-by-tag at least once, and <a> itself must
    # NOT appear in the downgrade map.
    by_tag = stats["downgrades_by_tag"]
    assert "table" in by_tag
    assert "a" not in by_tag


def test_link_inside_blockquote_survives_via_unwrap():
    md = "> quoted with [link](https://example.com/q) inside"
    nodes, stats = markdown_to_telegraph_nodes(md)

    flat = json.dumps(nodes, ensure_ascii=False)
    assert '"href": "https://example.com/q"' in flat
    assert stats["anchors"] == 1
    assert stats["downgrades_by_tag"].get("blockquote", 0) >= 1


def test_h1_h2_h4_h5_h6_all_unwrap_but_h3_is_kept():
    md = "\n\n".join(
        [
            "# h1 title",
            "## h2 title",
            "### h3 title",
            "#### h4 title",
            "##### h5 title",
            "###### h6 title",
        ]
    )
    _nodes, stats = markdown_to_telegraph_nodes(md)

    by_tag = stats["downgrades_by_tag"]
    for unwrapped in ("h1", "h2", "h4", "h5", "h6"):
        assert by_tag.get(unwrapped, 0) == 1, (
            f"{unwrapped} should be unwrapped (counted once): "
            f"{by_tag.get(unwrapped, 0)}"
        )
    assert "h3" not in by_tag  # h3 is whitelisted — kept, not downgraded


def test_link_inside_em_keeps_both_em_and_anchor():
    md = "*[link text](https://example.com/em)*"
    nodes, _stats = markdown_to_telegraph_nodes(md)

    paragraph = nodes[0]
    em_node = paragraph["children"][0]  # type: ignore[index]
    assert em_node["tag"] == "em"
    inner = em_node["children"][0]
    assert inner["tag"] == "a"
    assert inner["attrs"]["href"] == "https://example.com/em"


def test_strikethrough_is_unwrapped_but_inner_text_and_links_survive():
    md = "~~deleted [link](https://example.com/s) text~~"
    nodes, stats = markdown_to_telegraph_nodes(md)

    flat = json.dumps(nodes, ensure_ascii=False)
    assert '"href": "https://example.com/s"' in flat
    # <s> (strikethrough tag from markdown-it GFM) should be in downgrades
    assert stats["downgrades_by_tag"].get("s", 0) >= 1


def test_hr_unwraps_silently_with_no_children():
    md = "before\n\n---\n\nafter"
    _nodes, stats = markdown_to_telegraph_nodes(md)
    assert stats["downgrades_by_tag"].get("hr", 0) == 1


def test_img_unwraps_to_no_children_when_alt_is_empty():
    md = "![](https://example.com/pic.png)"
    _nodes, stats = markdown_to_telegraph_nodes(md)
    assert stats["downgrades_by_tag"].get("img", 0) >= 1


# ─── <a> collapse-to-text: bad href ─────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_href",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(2)",
        "data:text/html,<script>x</script>",
        "vbscript:msgbox",
        "file:///etc/passwd",
        "",  # empty
        "/relative/path",  # no scheme
        "//example.com/protocol-relative",  # no scheme
    ],
)
def test_unsafe_or_missing_href_collapses_anchor_to_text(bad_href: str):
    # Build raw HTML directly — markdown can't easily emit some of these.
    # We call render_to_html ourselves to confirm behaviour, but the
    # collapse path is verified by feeding the HTML through the builder.
    from backlink_publisher.publishing.adapters.telegraph_node import _TelegraphNodeBuilder

    builder = _TelegraphNodeBuilder()
    builder.feed(f'<p>before <a href="{bad_href}">click</a> after</p>')
    builder.close()

    flat = json.dumps(builder.nodes, ensure_ascii=False)
    assert "click" in flat  # text preserved
    assert '"tag": "a"' not in flat  # no anchor in output
    assert builder.anchors == 0
    assert builder.downgrades_by_tag.get("a", 0) == 1


def test_anchor_with_safe_https_is_kept():
    assert _is_safe_href("https://example.com")
    assert _is_safe_href("http://example.com")
    assert _is_safe_href("mailto:foo@example.com")
    assert _is_safe_href("tel:+1234567890")


def test_anchor_with_unsafe_scheme_is_rejected_by_helper():
    assert not _is_safe_href("")
    assert not _is_safe_href("/relative")
    assert not _is_safe_href("javascript:void(0)")
    assert not _is_safe_href("data:text/plain,hello")


# ─── Stats and edge cases ───────────────────────────────────────────────────


def test_empty_markdown_returns_empty_nodes_and_zero_stats():
    nodes, stats = markdown_to_telegraph_nodes("")
    assert nodes == []
    assert stats == {
        "downgrades": 0,
        "anchors": 0,
        "utf8_bytes": 0,
        "downgrades_by_tag": {},
    }


def test_utf8_bytes_reflects_cjk_expansion():
    # 50 Chinese characters wrapped in a paragraph. Each char is 3 UTF-8
    # bytes, plus the JSON wrapper. We assert a lower bound rather than
    # exact size so the test does not lock in JSON whitespace details.
    cjk = "短链工具背书测试" * 10  # 80 chars, all 3-byte UTF-8
    nodes, stats = markdown_to_telegraph_nodes(cjk)
    assert nodes  # produced a paragraph
    assert stats["utf8_bytes"] >= 80 * 3
    # ensure_ascii=False is on: byte count is not inflated by \uXXXX escapes
    serialized = json.dumps(nodes, ensure_ascii=False).encode("utf-8")
    assert stats["utf8_bytes"] == len(serialized)


def test_br_via_handle_starttag_emits_void_node():
    # <br> in HTML5 (no slash) arrives via handle_starttag. The walker
    # must treat it as void — no children key, no dangling frame.
    from backlink_publisher.publishing.adapters.telegraph_node import _TelegraphNodeBuilder

    builder = _TelegraphNodeBuilder()
    builder.feed("<p>a<br>b</p>")
    builder.close()
    paragraph = builder.nodes[0]
    assert paragraph["children"][1] == {"tag": "br"}  # type: ignore[index]


def test_br_via_handle_startendtag_emits_void_node():
    from backlink_publisher.publishing.adapters.telegraph_node import _TelegraphNodeBuilder

    builder = _TelegraphNodeBuilder()
    builder.feed("<p>a<br/>b</p>")
    builder.close()
    paragraph = builder.nodes[0]
    assert paragraph["children"][1] == {"tag": "br"}  # type: ignore[index]


def test_malformed_html_does_not_raise():
    # Unclosed tags, mismatched closes — html.parser is permissive in
    # Python 3.5+ and never raises. Verify the walker survives.
    from backlink_publisher.publishing.adapters.telegraph_node import _TelegraphNodeBuilder

    builder = _TelegraphNodeBuilder()
    builder.feed("<p>open <b>bold </p>")  # </b> missing
    builder.close()
    # Just assert no exception and we got some output.
    assert isinstance(builder.nodes, list)


def test_deeply_nested_pathological_input_does_not_explode():
    # Build a markdown blockquote nest 60 deep. The depth cap kicks in
    # well below that and prevents O(depth²) walker behaviour.
    md = "".join(">" * 60) + " end"
    nodes, stats = markdown_to_telegraph_nodes(md)
    # Walker did not raise, returned a (possibly heavily downgraded) tree.
    assert isinstance(nodes, list)
    assert stats["downgrades"] >= 1


# ─── Golden fixture round-trip ──────────────────────────────────────────────


_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "telegraph_node"


@pytest.mark.parametrize(
    "fixture_name",
    sorted(p.stem for p in _FIXTURES_DIR.glob("*.md")) if _FIXTURES_DIR.exists() else [],
)
def test_golden_fixtures_round_trip(fixture_name: str):
    """Each .md / .nodes.json pair in fixtures/telegraph_node/ must agree.

    The .nodes.json file holds the expected ``nodes`` list (not the full
    ``(nodes, stats)`` tuple). Regenerate by hand when the converter
    behaviour intentionally changes.
    """
    md_path = _FIXTURES_DIR / f"{fixture_name}.md"
    expected_path = _FIXTURES_DIR / f"{fixture_name}.nodes.json"

    md = md_path.read_text(encoding="utf-8")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    nodes, _stats = markdown_to_telegraph_nodes(md)
    assert nodes == expected, (
        f"Fixture '{fixture_name}' diverged. Got:\n"
        f"{json.dumps(nodes, ensure_ascii=False, indent=2)}"
    )


# ─── helpers ─────────────────────────────────────────────────────────────────


def _top_tag(node) -> str:
    if isinstance(node, dict):
        return node.get("tag", "")
    return ""
