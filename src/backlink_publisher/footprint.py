"""Self-fingerprint auditor for backlink HTML output.

Penguin / SpamBrain spam detection clusters destination links across many
domains pointing at the same money URL. The cluster key is not the anchor
text or the article body — those vary. The cluster key is the **byte-level
HTML scaffolding** that every article emits identically: attribute ordering
inside ``<a>`` tags, the exact ``rel`` string, whitespace before ``<a>``,
the comma-vs-em-dash separator between links.

``work_themed_generator``'s self-comment (on the work-themed branch) already
acknowledges fingerprint awareness — but only for link *ordering* across
the 6 permutations. The surrounding scaffolding (attribute order, rel
content, separator style) is identical across all permutations and across
all rows, which is precisely the cluster key.

This module is the offline auditor: it parses a stream of rendered HTML
payloads (the JSONL output of ``plan-backlinks``) and reports the
byte-level patterns that appear in 100% of outputs — those are the
project's self-fingerprint signature.

Pure functions, no I/O, no LLM. The CLI wrapper is in
``cli/footprint.py``; this module is the engine.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Hashable, TypeVar

# --- Gate contract constants ----------------------------------------------
# Read by the regression gate (tests/test_footprint_regression.py) at import
# time. Bump SCHEMA_VERSION whenever the engine's emitted shape changes in a
# way that invalidates committed baselines; the gate then raises
# FootprintGateSchemaMismatch instructing the operator to regenerate.
SCHEMA_VERSION: int = 1

# Default drift / alarm thresholds (percent). Plan Unit 4 may tune per
# renderer path via THRESHOLD_OVERRIDES once natural variance is measured.
DEFAULT_THRESHOLD_DRIFT_PP: float = 5.0
DEFAULT_THRESHOLD_ALARM_PCT: float = 95.0

# Per-(corpus, dimension) threshold overrides, populated by Unit 4 when a
# renderer path has natural variance that exceeds the default drift cap.
# Key: (corpus_name, dimension_name); value: (drift_pp, alarm_pct).
THRESHOLD_OVERRIDES: dict[tuple[str, str], tuple[float, float]] = {}

# Matches <a ...>...</a>. Captures the attributes blob and the anchor text.
_A_TAG_RE = re.compile(r"<a(\s[^>]*)>([^<]*)</a>", re.IGNORECASE | re.DOTALL)

# Inside the attributes blob, capture name="value" pairs in order of appearance.
_ATTR_RE = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')


_K = TypeVar("_K", bound=Hashable)


def _top_by_count_then_lex(counter: Counter[_K], n: int) -> list[tuple[_K, int]]:
    """Deterministic alternative to ``Counter.most_common(n)``.

    Sorts by count descending, then by key ascending (Python's native
    ordering — works for ``str`` keys and ``tuple[str, ...]`` keys alike).
    Tied counts always resolve to the lex-smallest key, independent of
    insertion order and ``PYTHONHASHSEED``. Empty counter returns ``[]``.
    """
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


@dataclass(frozen=True)
class LinkSignature:
    """The byte-level fingerprint of one ``<a>`` tag.

    Capturing attribute order + rel content + the immediately-preceding
    character lets the auditor surface patterns that span domains.
    """

    attr_name_order: tuple[str, ...]  # e.g. ("href", "target", "rel")
    rel_value: str  # exact rel string, e.g. "noopener noreferrer"
    target_value: str  # exact target string, e.g. "_blank"
    preceding_char: str  # one character before "<a", or "" if at body start


def extract_link_signatures(html: str) -> list[LinkSignature]:
    """Walk every ``<a>`` tag in ``html`` and return its signature.

    Returns an empty list when no links present. Order of returned
    signatures matches order of appearance in ``html``.
    """
    out: list[LinkSignature] = []
    for match in _A_TAG_RE.finditer(html):
        attrs_blob = match.group(1)
        attr_pairs = _ATTR_RE.findall(attrs_blob)
        if not attr_pairs:
            continue
        attr_names = tuple(name.lower() for name, _ in attr_pairs)
        attr_dict = {name.lower(): value for name, value in attr_pairs}
        # Preceding char (for separator pattern detection)
        start = match.start()
        preceding = html[start - 1] if start > 0 else ""
        out.append(
            LinkSignature(
                attr_name_order=attr_names,
                rel_value=attr_dict.get("rel", ""),
                target_value=attr_dict.get("target", ""),
                preceding_char=preceding,
            )
        )
    return out


@dataclass
class FootprintReport:
    """Aggregate fingerprint summary across a corpus of HTML payloads.

    Each field tracks one byte-level dimension a Penguin / SpamBrain
    clustering pass would key on. ``concentration_pct`` >= 95% on any
    dimension indicates the operator's articles are byte-uniform enough
    that destination links pointing at the same money URL will cluster
    on that dimension.
    """

    total_links: int
    total_payloads: int
    payloads_without_links: int = 0
    attr_order_counts: Counter[tuple[str, ...]] = field(default_factory=Counter)
    rel_value_counts: Counter[str] = field(default_factory=Counter)
    target_value_counts: Counter[str] = field(default_factory=Counter)
    preceding_char_counts: Counter[str] = field(default_factory=Counter)

    def top_attr_order(self, n: int = 3) -> list[tuple[tuple[str, ...], int]]:
        return _top_by_count_then_lex(self.attr_order_counts, n)

    def top_rel_values(self, n: int = 5) -> list[tuple[str, int]]:
        return _top_by_count_then_lex(self.rel_value_counts, n)

    def concentration_pct(self, dimension: str) -> float:
        """Return the share of links whose value matches the most common one
        on the given dimension. 100% = pure self-fingerprint (worst case).
        """
        counter = getattr(self, f"{dimension}_counts", None)
        if not isinstance(counter, Counter) or self.total_links == 0:
            return 0.0
        top = _top_by_count_then_lex(counter, 1)
        if not top:
            return 0.0
        _, top_count = top[0]
        return 100.0 * top_count / self.total_links


def analyze_corpus(html_payloads: list[str]) -> FootprintReport:
    """Aggregate link signatures across many rendered HTML payloads.

    ``html_payloads`` is typically a list of ``content_markdown`` (or
    rendered HTML) strings — one per article. Empty payloads, payloads
    without ``<a>`` tags, and unparseable strings are tracked but never
    raise.
    """
    report = FootprintReport(total_links=0, total_payloads=len(html_payloads))
    for html in html_payloads:
        sigs = extract_link_signatures(html or "")
        if not sigs:
            report.payloads_without_links += 1
            continue
        for sig in sigs:
            report.total_links += 1
            report.attr_order_counts[sig.attr_name_order] += 1
            report.rel_value_counts[sig.rel_value] += 1
            report.target_value_counts[sig.target_value] += 1
            report.preceding_char_counts[sig.preceding_char] += 1
    return report


def format_report_markdown(report: FootprintReport, *, alarm_pct: float = 95.0) -> str:
    """Render a human-readable summary, flagging dimensions where ≥ alarm_pct
    of all links share the same value (cluster-key risk).
    """
    if report.total_links == 0:
        return (
            "# Footprint Audit\n\n"
            f"No links found in {report.total_payloads} payload(s). "
            "Nothing to fingerprint."
        )

    lines = [
        "# Footprint Audit",
        "",
        f"Analyzed **{report.total_links} links** across "
        f"**{report.total_payloads} payload(s)** "
        f"({report.payloads_without_links} without links).",
        "",
        "## Cluster-Key Risk (≥ "
        f"{alarm_pct:.0f}% concentration on one value = high fingerprint risk)",
        "",
        "| Dimension | Top value | Concentration | Verdict |",
        "|---|---|---|---|",
    ]

    for dim_label, dim_key, top_extractor in [
        (
            "Attribute order in <a>",
            "attr_order",
            lambda: " → ".join(report.top_attr_order(1)[0][0]) if report.attr_order_counts else "—",
        ),
        (
            "rel value",
            "rel_value",
            lambda: repr(report.top_rel_values(1)[0][0]) if report.rel_value_counts else "—",
        ),
        (
            "target value",
            "target_value",
            lambda: repr(_top_by_count_then_lex(report.target_value_counts, 1)[0][0])
            if report.target_value_counts
            else "—",
        ),
        (
            "Preceding char",
            "preceding_char",
            lambda: repr(_top_by_count_then_lex(report.preceding_char_counts, 1)[0][0])
            if report.preceding_char_counts
            else "—",
        ),
    ]:
        pct = report.concentration_pct(dim_key)
        verdict = "⚠️ CLUSTER KEY" if pct >= alarm_pct else "OK"
        lines.append(
            f"| {dim_label} | {top_extractor()} | {pct:.1f}% | {verdict} |"
        )

    lines.append("")
    lines.append("## Top attribute orderings")
    lines.append("")
    for order, count in report.top_attr_order(3):
        lines.append(f"- `{' → '.join(order)}` ({count} links)")

    lines.append("")
    lines.append("## Top rel values")
    lines.append("")
    for value, count in report.top_rel_values(5):
        lines.append(f"- `{value!r}` ({count} links)")

    lines.append("")
    lines.append(
        "**How to act on a CLUSTER KEY warning**: the dimension is "
        "byte-uniform across every link your articles emit. A Penguin / "
        "SpamBrain clustering pass keys on this exact pattern to group "
        "destination domains pointing at the same money URL. Mitigation: "
        "randomize the ordering across articles, vary the rel value "
        "(e.g. omit `noreferrer` on some links), or break the preceding-"
        "character regularity. The fix is in the renderer, not in this "
        "report — `bp footprint` only detects."
    )
    return "\n".join(lines)
