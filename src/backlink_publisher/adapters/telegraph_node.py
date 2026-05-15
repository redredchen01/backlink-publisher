"""Markdown → Telegraph Node tree converter (Unit 3 of Telegraph adapter plan).

Plan: docs/plans/2026-05-15-004-feat-telegraph-adapter-plan.md (Unit 3).

The Telegraph API accepts a Node tree where each node is either a plain
string (text content) or a dict shaped ``{"tag": str, "attrs": dict?,
"children": list?}``. The ``attrs`` map only carries ``href`` / ``src``;
no other attributes are honoured by the platform. See
https://telegra.ph/api .

This converter is intentionally narrow:

* Whitelist of structural tags Telegraph renders well as backlink-article
  content (links, paragraphs, h3 sub-headings, lists, inline emphasis,
  hard line breaks). Other tags are *unwrapped* — the tag itself is
  dropped but its children continue to be walked, so links nested inside
  unsupported containers (most importantly ``<table>``) still reach the
  output. The single exception is ``<a>``: when its href is missing or
  uses a scheme outside the safe-list, the whole anchor collapses to its
  inner text (defence-in-depth against ``javascript:`` / ``data:`` URIs
  even though Telegraph sanitises server-side).

* Pure function — no IO, no logging side effects. The caller decides what
  to do with the ``stats`` channel (Unit 4's adapter records it in
  ``_provider_meta`` and uses ``utf8_bytes`` for the pre-flight 60 KB
  budget check, well below Telegraph's 64 KB hard limit).
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from .. import markdown_utils

Node = dict[str, Any] | str

#: Tags Telegraph renders natively and that backlink-article markdown needs
#: to keep verbatim. Everything outside this set is unwrapped (children
#: preserved, tag itself dropped) so the link payload survives.
_ALLOWED_TAGS: frozenset[str] = frozenset(
    {"a", "p", "h3", "ul", "ol", "li", "b", "em", "strong", "br"}
)

#: URL schemes accepted on ``<a href=...>``. Anything else (``javascript:``,
#: ``data:``, ``vbscript:``, ``file:``, relative refs, …) collapses the
#: anchor to inner text.
_ALLOWED_HREF_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto", "tel"})

#: HTML5 void elements. Used so non-whitelisted tags like ``<hr>`` /
#: ``<img>`` do not leave a dangling "unwrap" frame on the stack waiting
#: for a close tag that will never arrive.
_HTML5_VOID: frozenset[str] = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
)

#: Hard cap on nesting depth. Realistic markdown emits depth 5–10; this
#: protects against pathological inputs that could turn the walker into
#: an O(depth²) stack churn. When exceeded, the overflow node is dropped
#: (counted as a downgrade) — the rest of the tree continues to convert.
_MAX_DEPTH: int = 30


def markdown_to_telegraph_nodes(
    md: str,
) -> tuple[list[Node], dict[str, Any]]:
    """Convert ``md`` into a Telegraph Node tree.

    Returns ``(nodes, stats)`` where ``stats`` is a dict shaped::

        {
            "downgrades": int,          # total tags dropped or collapsed
            "anchors": int,             # successful <a> survivors
            "utf8_bytes": int,          # JSON byte budget for 64KB pre-check
            "downgrades_by_tag": dict[str, int],
        }

    Empty input short-circuits to ``([], stats_zero)`` — the adapter
    treats this as ``status="failed"`` upstream without calling the
    Telegraph API.
    """
    stats: dict[str, Any] = {
        "downgrades": 0,
        "anchors": 0,
        "utf8_bytes": 0,
        "downgrades_by_tag": {},
    }
    if not md:
        return [], stats

    html = markdown_utils.render_to_html(md)
    if not html:
        return [], stats

    builder = _TelegraphNodeBuilder()
    builder.feed(html)
    builder.close()

    nodes = builder.nodes
    stats["downgrades"] = builder.downgrades
    stats["anchors"] = builder.anchors
    stats["downgrades_by_tag"] = dict(builder.downgrades_by_tag)
    if nodes:
        stats["utf8_bytes"] = len(
            json.dumps(nodes, ensure_ascii=False).encode("utf-8")
        )

    return nodes, stats


def _is_safe_href(href: str) -> bool:
    """Return True iff ``href`` uses an explicitly allow-listed URL scheme."""
    if not href:
        return False
    try:
        parsed = urlparse(href)
    except ValueError:
        return False
    scheme = parsed.scheme.lower()
    if not scheme:
        return False
    return scheme in _ALLOWED_HREF_SCHEMES


class _TelegraphNodeBuilder(HTMLParser):
    """Stream HTML → Telegraph Node tree via stack-based walker.

    Each frame on ``_stack`` is a dict:

    * ``tag``     — name of the open tag (for matching ``handle_endtag``)
    * ``kind``    — ``kept`` (this tag stays in output) /
                    ``unwrap`` (tag dropped, children flow into parent) /
                    ``collapse`` (children flow into parent as plain text;
                    used only for ``<a>`` with bad href)
    * ``children`` — the list new child nodes / strings get appended to.
                     For ``kept`` it points at the node's own children;
                     for ``unwrap`` / ``collapse`` it aliases the parent's
                     children, so unwrapped subtrees splice straight into
                     the enclosing context.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[Node] = []
        self.downgrades: int = 0
        self.anchors: int = 0
        self.downgrades_by_tag: dict[str, int] = {}
        self._stack: list[dict[str, Any]] = [
            {"tag": "_root", "kind": "root", "children": self.nodes}
        ]

    # ── stack helpers ────────────────────────────────────────────────

    def _depth(self) -> int:
        # Root frame doesn't count toward the depth cap.
        return len(self._stack) - 1

    def _current_children(self) -> list[Node]:
        return self._stack[-1]["children"]

    def _bump_downgrade(self, tag: str) -> None:
        self.downgrades += 1
        self.downgrades_by_tag[tag] = self.downgrades_by_tag.get(tag, 0) + 1

    # ── parser callbacks ─────────────────────────────────────────────

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        # html.parser emits handle_starttag for <br> / <img> in HTML5 mode
        # (no trailing slash). Treat them as void so we never leave a
        # frame waiting for a matching close.
        self._open_tag(tag, attrs, void=tag in _HTML5_VOID)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        # XHTML / XML self-closing form like <br/> or <img/>.
        self._open_tag(tag, attrs, void=True)

    def handle_endtag(self, tag: str) -> None:
        # Permissive close: search the stack for a frame matching this
        # tag, then drop that frame and everything above it. Mismatched
        # closes (e.g. </b> when only <em> is open) are silently ignored
        # — markdown_utils.render_to_html does not emit nested mismatches
        # in practice, but we don't want a malformed input to wedge the
        # walker.
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i]["tag"] == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if not data:
            return
        # markdown-it emits "\n" between block elements (e.g. "</p>\n<p>")
        # and between unwrapped table rows / cells. HTMLParser delivers
        # those as standalone whitespace-only data callbacks. They carry
        # no rendering value (Telegraph collapses whitespace anyway) but
        # would bloat the JSON byte budget and pollute every unwrap-mix
        # output. Inside a kept block, real inter-word whitespace arrives
        # as part of the surrounding text run (HTMLParser concatenates
        # contiguous text), so this filter never drops anything visible.
        if not data.strip():
            return
        self._current_children().append(data)

    # ── core open-tag dispatch ───────────────────────────────────────

    def _open_tag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        *,
        void: bool,
    ) -> None:
        if self._depth() >= _MAX_DEPTH:
            # Depth cap: drop this node entirely. Children that arrive
            # inside it will be appended to the parent (because we did
            # not push a frame), which is good enough for the pathological
            # inputs this cap exists to defend against.
            self._bump_downgrade(tag)
            return

        if tag in _ALLOWED_TAGS:
            if tag == "a":
                self._open_anchor(attrs, void=void)
                return

            node: dict[str, Any] = {"tag": tag, "children": []}
            self._current_children().append(node)

            if void:
                # Telegraph accepts ``{"tag": "br"}`` without a children
                # key; emitting an empty list is also legal but trimming
                # keeps the JSON byte budget tighter.
                del node["children"]
                return

            self._stack.append(
                {"tag": tag, "kind": "kept", "children": node["children"]}
            )
            return

        # Non-whitelisted tag: unwrap-and-recurse. The tag is dropped, but
        # any children continue to be walked and land in the parent's
        # children list. This is what keeps a backlink ``<a>`` alive when
        # markdown_utils emits ``<table><tr><td>…<a>…</a>…</td></tr></table>``.
        self._bump_downgrade(tag)
        if void:
            return
        self._stack.append(
            {
                "tag": tag,
                "kind": "unwrap",
                "children": self._current_children(),
            }
        )

    def _open_anchor(
        self,
        attrs: list[tuple[str, str | None]],
        *,
        void: bool,
    ) -> None:
        href = ""
        for k, v in attrs:
            if k == "href":
                href = (v or "").strip()
                break

        if not _is_safe_href(href):
            # Drop the <a> wrapper — fall back to its inner text.
            self._bump_downgrade("a")
            if void:
                return
            self._stack.append(
                {
                    "tag": "a",
                    "kind": "collapse",
                    "children": self._current_children(),
                }
            )
            return

        node: dict[str, Any] = {
            "tag": "a",
            "attrs": {"href": href},
            "children": [],
        }
        self._current_children().append(node)
        self.anchors += 1

        if void:
            del node["children"]
            return

        self._stack.append(
            {"tag": "a", "kind": "kept", "children": node["children"]}
        )
