#!/usr/bin/env python3
"""LLM rejection-rate spike — OPTIONAL pre-flight for hybrid mode.

When you need this
------------------
The default 51acgs.com configuration ships with typed anchor pools sized
for **LLM-free runtime** — 126 pre-written candidates across 20 cells, the
heavy-use home/branded cell padded to 15 entries to survive the 20-entry
text-dedup window. A 500-article simulation produces zero LLM fallback
calls. If your config matches that shape, you do not need this script.

You only need this script if you intentionally:
- thin a cell below the safe threshold to make room for LLM creativity, or
- experiment with LLM-augmented anchor generation for a new site whose
  pool isn't fully written yet.

In those scenarios, the LLM is genuinely on the hot path and you need to
know its rejection rate before promoting the config — especially for
adult-ACG content where mainstream providers (OpenAI, Anthropic, Google)
often refuse generation requests. A high rejection rate would push the
scheduler into a permanent degrade-to-branded state, defeating the
distribution targets the scheduler exists to enforce.

What it does
------------
1. Loads ``config.toml`` (env-var ``BACKLINK_LLM_API_KEY`` still wins over
   the toml value if both are set).
2. Picks 20 realistic spike requests covering all five url_categories and
   all four anchor types — the same shapes the resolver will issue in
   production.
3. Calls ``OpenAICompatibleProvider.generate_candidates`` for each request
   and counts an attempt as REJECTED when ANY of these holds:
     - the provider raised (network, parse, 4xx auth/refusal, etc.)
     - it returned zero candidates
     - none of the returned candidates passed ``_passes_filters``
4. Prints a per-request table and an overall rejection-rate summary.
5. Exits 0 when rejection rate < 20%, else 1 — wire it into CI or a manual
   pre-flight check.

What it does
------------
1. Loads ``config.toml`` (env-var ``BACKLINK_LLM_API_KEY`` still wins over
   the toml value if both are set).
2. Picks 20 realistic spike requests covering all five url_categories and
   all four anchor types — the same shapes the resolver will issue in
   production.
3. Calls ``OpenAICompatibleProvider.generate_candidates`` for each request
   and counts an attempt as REJECTED when ANY of these holds:
     - the provider raised (network, parse, 4xx auth/refusal, etc.)
     - it returned zero candidates
     - none of the returned candidates passed ``_passes_filters``
4. Prints a per-request table and an overall rejection-rate summary.
5. Exits 0 when rejection rate < 20%, else 1 — wire it into CI or a manual
   pre-flight check.

Usage
-----
    # one-off run with your normal config
    python scripts/llm_rejection_spike.py

    # provide the key via env var instead of config.toml
    BACKLINK_LLM_API_KEY=sk-... python scripts/llm_rejection_spike.py

    # override the main_domain (defaults to 51acgs.com — the brainstorm site)
    python scripts/llm_rejection_spike.py --main-domain https://other.example

    # tighter or looser gate
    python scripts/llm_rejection_spike.py --threshold 0.15
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make the package importable when running the script directly from the repo.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from backlink_publisher.adapters.llm_anchor_provider import (  # noqa: E402
    LLMAnchorRequest,
    OpenAICompatibleProvider,
)
from backlink_publisher.anchor_resolver import _passes_filters  # noqa: E402
from backlink_publisher.config import load_config  # noqa: E402

# Default rejection threshold from the plan's risk acknowledgments — anything
# above this means typed pools must be filled before the scheduler can engage.
_DEFAULT_THRESHOLD = 0.20

# 20 spike requests covering all 5 url_categories × 4 anchor_types.
# Keywords + subjects intentionally match the 51acgs.com brainstorm shape so
# the rejection rate observed here matches what production would see.
_SPIKE_KEYWORDS = [
    "成人漫画",
    "热门漫画",
    "本周漫画",
    "成人动漫",
    "漫画分类",
    "漫画专题",
    "同人作品",
    "ACG资源",
]

_SPIKE_REQUESTS: list[dict[str, Any]] = [
    # 5 home requests across types
    {"url_category": "home", "anchor_type": "branded", "kw": "成人漫画", "subject": "首页"},
    {"url_category": "home", "anchor_type": "partial", "kw": "成人漫画", "subject": "首页"},
    {"url_category": "home", "anchor_type": "exact", "kw": "成人漫画", "subject": "首页"},
    {"url_category": "home", "anchor_type": "lsi", "kw": "ACG资源", "subject": "首页"},
    # 4 hot requests
    {"url_category": "hot", "anchor_type": "branded", "kw": "热门漫画", "subject": "热门榜"},
    {"url_category": "hot", "anchor_type": "partial", "kw": "热门漫画", "subject": "热门榜"},
    {"url_category": "hot", "anchor_type": "exact", "kw": "本周漫画", "subject": "本周热门"},
    {"url_category": "hot", "anchor_type": "lsi", "kw": "热门作品", "subject": "热门榜"},
    # 4 animate requests
    {"url_category": "animate", "anchor_type": "branded", "kw": "成人动漫", "subject": "动漫"},
    {"url_category": "animate", "anchor_type": "partial", "kw": "成人动漫", "subject": "动漫"},
    {"url_category": "animate", "anchor_type": "exact", "kw": "成人动漫", "subject": "动漫"},
    {"url_category": "animate", "anchor_type": "lsi", "kw": "ACG资源", "subject": "动漫"},
    # 4 category requests
    {"url_category": "category", "anchor_type": "branded", "kw": "漫画分类", "subject": "分类总览"},
    {"url_category": "category", "anchor_type": "partial", "kw": "漫画分类", "subject": "分类总览"},
    {"url_category": "category", "anchor_type": "exact", "kw": "漫画分类", "subject": "分类总览"},
    {"url_category": "category", "anchor_type": "lsi", "kw": "同人作品", "subject": "分类总览"},
    # 3 topic requests
    {"url_category": "topic", "anchor_type": "branded", "kw": "漫画专题", "subject": "专题文章"},
    {"url_category": "topic", "anchor_type": "partial", "kw": "漫画专题", "subject": "专题文章"},
    {"url_category": "topic", "anchor_type": "exact", "kw": "漫画专题", "subject": "专题文章"},
    {"url_category": "topic", "anchor_type": "lsi", "kw": "同人作品", "subject": "专题文章"},
]


@dataclass
class SpikeOutcome:
    request: dict[str, Any]
    candidates: list[str]
    rejection_reason: str | None  # None = accepted, else short tag
    passing: list[str]  # candidates that survived _passes_filters

    @property
    def rejected(self) -> bool:
        return self.rejection_reason is not None


def _run_one(provider: OpenAICompatibleProvider, request: dict[str, Any], target_url: str) -> SpikeOutcome:
    """Issue one LLM call and classify the outcome."""
    req = LLMAnchorRequest(
        url_category=request["url_category"],
        anchor_type=request["anchor_type"],
        keyword=request["kw"],
        target_url=target_url,
        url_subject=request.get("subject"),
        n=5,
    )
    try:
        candidates = provider.generate_candidates(req)
    except Exception as exc:
        return SpikeOutcome(
            request=request,
            candidates=[],
            rejection_reason=f"exception:{type(exc).__name__}",
            passing=[],
        )

    if not candidates:
        return SpikeOutcome(
            request=request,
            candidates=[],
            rejection_reason="empty_candidates",
            passing=[],
        )

    passing = [c for c in candidates if _passes_filters(c)]
    if not passing:
        return SpikeOutcome(
            request=request,
            candidates=candidates,
            rejection_reason="all_filtered",
            passing=[],
        )

    return SpikeOutcome(
        request=request,
        candidates=candidates,
        rejection_reason=None,
        passing=passing,
    )


def _print_results(outcomes: list[SpikeOutcome], threshold: float) -> int:
    """Format the spike results as a Markdown table; return process exit code."""
    rejected = [o for o in outcomes if o.rejected]
    rate = len(rejected) / len(outcomes)

    print("# LLM rejection-rate spike")
    print()
    print(f"- **Total requests**: {len(outcomes)}")
    print(f"- **Rejected**: {len(rejected)}")
    print(f"- **Rejection rate**: {rate * 100:.1f}%")
    print(f"- **Threshold**: {threshold * 100:.0f}%")
    if rate > threshold:
        print(f"- **Verdict**: ❌ FAIL — rate exceeds threshold")
    else:
        print(f"- **Verdict**: ✅ PASS — rate within threshold")
    print()
    print("| # | url_category | anchor_type | keyword | result | sample candidate |")
    print("|---|---|---|---|---|---|")
    for i, o in enumerate(outcomes, start=1):
        req = o.request
        if o.rejected:
            sample = ", ".join(o.candidates[:2]) if o.candidates else "(none)"
            status = f"❌ {o.rejection_reason}"
        else:
            sample = o.passing[0]
            status = "✅ accepted"
        # Truncate long candidate strings for table sanity
        if len(sample) > 40:
            sample = sample[:37] + "…"
        print(
            f"| {i} | {req['url_category']} | {req['anchor_type']} | "
            f"{req['kw']} | {status} | {sample} |"
        )
    return 1 if rate > threshold else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="llm_rejection_spike",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--main-domain",
        default="https://51acgs.com",
        help="Site whose url_categories are referenced as target URLs (default: 51acgs.com)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=_DEFAULT_THRESHOLD,
        metavar="FLOAT",
        help=f"Max acceptable rejection rate, 0-1 (default: {_DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.toml (default: ~/.config/backlink-publisher/config.toml)",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if config.llm_anchor_provider is None:
        print(
            "ERROR: no LLM provider configured. Set [llm.anchor_provider] in "
            "config.toml or supply the BACKLINK_LLM_API_KEY env var alongside "
            "a base_url and model.",
            file=sys.stderr,
        )
        return 2

    provider = OpenAICompatibleProvider(
        base_url=config.llm_anchor_provider.base_url,
        api_key=config.llm_anchor_provider.api_key,
        model=config.llm_anchor_provider.model,
        timeout_s=config.llm_anchor_provider.timeout_s,
    )

    # Map url_category → a real URL on the chosen main_domain so the
    # provider's prompt sees a plausible target_url. If site_url_categories
    # is configured, prefer that; otherwise synthesize a path.
    main_domain_key = args.main_domain.rstrip("/")
    site_cats = config.site_url_categories.get(main_domain_key, {})

    def _target_url_for(category: str) -> str:
        if category in site_cats:
            return site_cats[category]
        # Fallback synthetic paths so the script still works when the user
        # hasn't filled in site_url_categories yet.
        return f"{main_domain_key}/{category}"

    outcomes: list[SpikeOutcome] = []
    for req in _SPIKE_REQUESTS:
        target = _target_url_for(req["url_category"])
        outcomes.append(_run_one(provider, req, target))

    return _print_results(outcomes, args.threshold)


if __name__ == "__main__":
    sys.exit(main())
