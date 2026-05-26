"""Divergence detection for the dual-state auditor (R1 + R3).

Compares the two read-only views from ``readers`` and emits one
``DivergenceRecord`` per finding. v1 classes:

- ``null_url_orphan`` (R1) — an ``articles`` row with no ``live_url``.
- ``history_orphan`` (R3) — a *published* history record none of whose published
  URLs appears in ``articles`` (the publish is entirely unrepresented).
- ``article_orphan`` (R3) — an ``articles`` row whose ``live_url`` matches no
  published URL in history.

The R3 join is on **canonical URL** (``canonicalize_url`` on both sides), never
on ``host`` (``articles.host`` is a bare netloc that ``is_same_host`` rejects,
and mixes publish-vs-target host across row types). The history-orphan rule is
**record-level** (a record is an orphan only if *none* of its URLs match), which
makes it fan-out-safe, ignores a record's draft URL when its published URL
matched, and naturally avoids re-detecting the deferred-R2 duplicate-URL case
(two records sharing one article both "match" it, so neither is an orphan).
Plan 2026-05-26-001 Unit 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .readers import ArticleRow, StoreSnapshot, _canon

#: R8 static source→tier map. The draft-queue ("drafts") source lands with R4
#: as "informational"; v1 sources are all high-signal.
_SOURCE_TIER: dict[str, str] = {"history": "high-signal", "articles": "high-signal"}

_PUBLISHED_STATUS = "published"


@dataclass
class DivergenceRecord:
    """One divergence finding. ``authority`` is ``indeterminate`` because the
    only ground truth for "did this link publish?" is the live web, which is
    out of scope — or ``possibly-transient`` when read during a concurrent
    write (R10). The ``class`` enum expands when R2/R4 land.
    """

    divergence_class: str  # null_url_orphan | history_orphan | article_orphan
    source: str  # history | articles
    authority: str = "indeterminate"
    canonical_url: str | None = None
    article_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def source_tier(self) -> str:
        return _SOURCE_TIER.get(self.source, "informational")

    def to_jsonl_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "class": self.divergence_class,
            "source": self.source,
            "source_tier": self.source_tier,
            "authority": self.authority,
        }
        if self.canonical_url is not None:
            out["canonical_url"] = self.canonical_url
        if self.article_id is not None:
            out["article_id"] = self.article_id
        if self.details:
            out["details"] = self.details
        return out


def _published_url_set(record: dict[str, Any]) -> set[str]:
    """Canonical published URLs declared by one history record. Tolerates a
    malformed (non-list) ``article_urls`` rather than crashing."""
    urls = record.get("article_urls")
    if not isinstance(urls, list):
        return set()
    return {_canon(u) for u in urls if isinstance(u, str) and u}


def find_divergences(snapshot: StoreSnapshot) -> list[DivergenceRecord]:
    """Detect R1 + R3 divergences. Records are stamped ``possibly-transient``
    when the snapshot was read during a concurrent write (R10).
    """
    authority = "possibly-transient" if snapshot.transient else "indeterminate"
    records: list[DivergenceRecord] = []

    # R1: articles with no live_url.
    for art in snapshot.articles:
        if not art.live_url:
            records.append(
                DivergenceRecord(
                    divergence_class="null_url_orphan",
                    source="articles",
                    authority=authority,
                    article_id=art.article_id,
                    details={"reason": "live_url IS NULL"},
                )
            )

    # Canonical published-URL universe on the articles side. ``article_by_url``
    # maps each canonical URL to ALL article_ids that canonicalize to it —
    # articles.live_url is UNIQUE on the *raw* URL, so two rows can collide on
    # the canonical form; reporting only one would silently drop the others.
    article_urls: set[str] = set()
    article_by_url: dict[str, list[int]] = {}
    for art in snapshot.articles:
        if art.live_url:
            key = _canon(art.live_url)
            article_urls.add(key)
            article_by_url.setdefault(key, []).append(art.article_id)

    # Cache each published record's canonical URL set once (used twice below).
    published: list[tuple[dict[str, Any], set[str]]] = []
    history_urls: set[str] = set()
    for rec in snapshot.history:
        if rec.get("status") != _PUBLISHED_STATUS:
            continue
        rec_urls = _published_url_set(rec)
        if not rec_urls:
            continue
        published.append((rec, rec_urls))
        history_urls |= rec_urls

    # R3a: published history records entirely absent from articles.
    for rec, rec_urls in published:
        if rec_urls.isdisjoint(article_urls):
            records.append(
                DivergenceRecord(
                    divergence_class="history_orphan",
                    source="history",
                    authority=authority,
                    canonical_url=sorted(rec_urls)[0],
                    details={
                        "history_id": rec.get("id"),
                        "target_url": rec.get("target_url"),
                        "urls": sorted(rec_urls),
                    },
                )
            )

    # R3b: article rows whose live_url matches no published history URL. Emit
    # one record per colliding article_id so none is dropped.
    for url in sorted(article_urls - history_urls):
        for article_id in article_by_url.get(url, [None]):
            records.append(
                DivergenceRecord(
                    divergence_class="article_orphan",
                    source="articles",
                    authority=authority,
                    canonical_url=url,
                    article_id=article_id,
                )
            )

    return records
