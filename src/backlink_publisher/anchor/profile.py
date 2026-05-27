"""Per-site anchor profile state — sliding window of recent link records.

The anchor profile scheduler (zh-CN short-form path) needs a persistent view
of what anchor types and texts a site has recently published, so it can steer
each new article toward the deficit type. That state lives here as one JSON
file per site under ``~/.cache/backlink-publisher/anchor-profile/``.

Sliding window: the most recent ``_MAX_ENTRIES`` link records are kept;
older entries are trimmed on every write. This is "recent" not "all-time" by
design — the scheduler should respond to current drift, not be dragged by
ancient history when proportions or pools change.

Concurrency: ``threading.Lock`` protects the read-modify-write cycle inside
one process. Cross-process safety is NOT provided (single-process operational
convention per plan scope). If multi-process becomes real, layer an
``fcntl.flock`` sidecar lockfile around the same primitives.

Failure posture: profile state is an *advisory* signal, not a system of
record. A corrupt JSON file or version drift returns an empty profile with a
warning rather than raising — the scheduler will treat the site as cold-start
and rebuild state from new writes. The alternative (raising) would block the
entire batch on a recoverable diagnostic-only file.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backlink_publisher.config import _cache_dir
from backlink_publisher._util.io import atomic_write_json
from backlink_publisher._util.logger import plan_logger

# Schema version — bump when ProfileEntry shape changes incompatibly.
_PROFILE_SCHEMA_VERSION = 1

# Per-target sliding window in articles. Each target_url retains up to this
# many recent articles' worth of entries — bounded by article-integrity, so the
# kept set is the union of "most recent N articles touching target X" across
# all known targets. At solo-operator scale (1-3 money URLs per domain) this
# yields ~100-300 articles per profile = a 90d window with margin.
_MAX_ARTICLES_PER_TARGET = 100

# Legacy alias retained for backwards-compat with any external imports; not
# used internally after the article-integrity trim landed.
_MAX_ENTRIES = _MAX_ARTICLES_PER_TARGET

# Default size of the anchor-text dedup window passed to ``recent_texts``.
_DEFAULT_TEXT_WINDOW = 20

# Filename sanitization: keep alnum + dot/underscore/hyphen; replace the rest.
# This makes filesystem-safe names from URLs like ``https://example.com/path``.
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

# Lock map keyed by sanitized filename — separate sites can write in parallel
# but two threads against the same site serialize through one lock.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(filename: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(filename)
        if lock is None:
            lock = threading.Lock()
            _locks[filename] = lock
        return lock


@dataclass
class ProfileEntry:
    """A single recorded link from a published article.

    One article produces 2-3 entries (1 main + 1-2 secondary). ``ts`` is the
    moment the article was successfully validated. ``degraded`` is True when
    the entry came from the validator-failure fallback path; the scheduler
    still counts these in its distribution math (they really happened) but
    Unit 9's report surfaces the degradation rate as a quality signal.

    ``target_url`` is the absolute destination URL the anchor pointed at. Added
    as a tolerant additive field (no schema-version bump) — pre-bump entries
    read back as ``""`` and are bucketed into a virtual "domain-rollup" group
    by report-anchors' distribution-visibility layer. Must always be populated
    on new writes via the entry constructors at the publish-time call sites.
    """

    ts: str
    link_role: str  # "main" | "secondary"
    url_category: str  # "home" | "hot" | "animate" | "category" | "topic" | ...
    anchor_type: str  # one of ANCHOR_TYPES
    anchor_text: str
    degraded: bool = False
    target_url: str = ""


@dataclass
class ProfileState:
    version: int = _PROFILE_SCHEMA_VERSION
    main_domain: str = ""
    entries: list[ProfileEntry] = field(default_factory=list)


# ── path helpers ────────────────────────────────────────────────────────────


def _sanitize_filename(main_domain: str) -> str:
    """Turn a main_domain URL into a filesystem-safe filename stem."""
    return _FILENAME_UNSAFE.sub("_", main_domain.rstrip("/"))


def _profile_dir() -> Path:
    return _cache_dir() / "anchor-profile"


def _profile_path(main_domain: str) -> Path:
    return _profile_dir() / f"{_sanitize_filename(main_domain)}.json"


# ── load / record ───────────────────────────────────────────────────────────


def load_profile(main_domain: str) -> ProfileState:
    """Read the on-disk profile for ``main_domain``.

    Returns an empty ``ProfileState`` (cold-start) when:
    - the file does not exist
    - the file is unreadable / malformed JSON
    - the schema version differs from ``_PROFILE_SCHEMA_VERSION``
    Each non-happy branch emits a structured warning so anomalies surface in
    logs without blocking the pipeline.
    """
    path = _profile_path(main_domain)
    if not path.exists():
        return ProfileState(main_domain=main_domain)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        plan_logger.warn(
            "anchor_profile_load_failed",
            main_domain=main_domain,
            path=str(path),
            reason=type(exc).__name__,
            detail=str(exc),
        )
        return ProfileState(main_domain=main_domain)

    version = raw.get("version")
    if version != _PROFILE_SCHEMA_VERSION:
        plan_logger.warn(
            "anchor_profile_version_mismatch",
            main_domain=main_domain,
            expected=_PROFILE_SCHEMA_VERSION,
            got=version,
        )
        return ProfileState(main_domain=main_domain)

    entries_raw = raw.get("entries", [])
    if not isinstance(entries_raw, list):
        plan_logger.warn(
            "anchor_profile_entries_malformed",
            main_domain=main_domain,
            type=type(entries_raw).__name__,
        )
        return ProfileState(main_domain=main_domain)

    entries: list[ProfileEntry] = []
    for item in entries_raw:
        if not isinstance(item, dict):
            continue
        try:
            entries.append(
                ProfileEntry(
                    ts=str(item["ts"]),
                    link_role=str(item["link_role"]),
                    url_category=str(item["url_category"]),
                    anchor_type=str(item["anchor_type"]),
                    anchor_text=str(item["anchor_text"]),
                    degraded=bool(item.get("degraded", False)),
                    target_url=str(item.get("target_url", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            # Skip individual malformed entries rather than tossing the whole file.
            continue

    return ProfileState(
        version=version,
        main_domain=str(raw.get("main_domain", main_domain)),
        entries=entries,
    )


def iter_profiles() -> "Iterator[ProfileState]":
    """Yield every on-disk anchor profile (one per site).

    There is no per-target index — profiles are keyed by the seed's
    ``main_domain``. Consumers that need an all-sites view (e.g. the
    Backlink Equity Ledger, which keys by per-entry ``target_url``) glob
    the profile directory and load each file through the robust
    :func:`load_profile` path. Missing directory ⇒ no profiles (cold start).
    Files whose JSON lacks a ``main_domain`` key are skipped.
    """
    directory = _profile_dir()
    if not directory.exists():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        main_domain = raw.get("main_domain")
        if not main_domain:
            continue
        yield load_profile(str(main_domain))


def record_article(main_domain: str, new_entries: list[ProfileEntry]) -> None:
    """Atomically append ``new_entries`` and trim to the sliding window.

    Read-modify-write is protected by a per-site lock so two threads recording
    against the same main_domain serialize. Failures to write (e.g. cache_dir
    unwritable) are logged and swallowed — profile state is advisory only and
    must not abort the publishing batch.
    """
    if not new_entries:
        return

    filename = _sanitize_filename(main_domain)
    lock = _lock_for(filename)
    with lock:
        try:
            _profile_dir().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            plan_logger.warn(
                "anchor_profile_dir_create_failed",
                main_domain=main_domain,
                reason=type(exc).__name__,
            )
            return

        existing = load_profile(main_domain)
        merged = existing.entries + list(new_entries)
        # Article-integrity-preserving per-target trim:
        # Group into articles via the same boundary rule the publish-path
        # scheduler uses (_group_into_articles), keep the most-recent
        # _MAX_ARTICLES_PER_TARGET articles per distinct target_url, take the
        # UNION of those selections. Articles are never split — a main with
        # its secondaries is always evicted together, protecting
        # recent_secondary_count_split's invariant.
        merged = _trim_by_target(merged, _MAX_ARTICLES_PER_TARGET)

        payload = {
            "version": _PROFILE_SCHEMA_VERSION,
            "main_domain": main_domain,
            "entries": [asdict(e) for e in merged],
        }
        try:
            atomic_write_json(_profile_path(main_domain), payload)
        except OSError as exc:
            plan_logger.warn(
                "anchor_profile_write_failed",
                main_domain=main_domain,
                reason=type(exc).__name__,
                detail=str(exc),
            )


def now_iso() -> str:
    """Helper to produce ``ts`` values in the canonical form used by ``ProfileEntry``."""
    return datetime.now(timezone.utc).isoformat()


def _trim_by_target(
    entries: list[ProfileEntry], max_articles_per_target: int
) -> list[ProfileEntry]:
    """Keep up to ``max_articles_per_target`` most-recent articles per target_url.

    The kept set is the UNION across all distinct ``target_url`` values found
    in the entries (including ``""`` for pre-bump entries, which all share one
    virtual target). Articles are atomic: a main with its secondaries is kept
    or evicted together, never split.

    Article boundaries follow the same rule consumed by anchor_scheduler:
    each ``link_role == "main"`` entry starts an article and subsequent
    entries belong to it until the next main. Secondaries before the first
    main are trimmed-article remnants and are dropped (matches the existing
    ``_group_into_articles`` contract).
    """
    if not entries:
        return entries

    articles = _group_into_articles(entries)
    if not articles:
        return []

    # For each distinct target_url, mark the indices of its most-recent
    # ``max_articles_per_target`` articles.
    keep_indices: set[int] = set()
    targets = {e.target_url for art in articles for e in art}
    for target in targets:
        seen = 0
        for i in range(len(articles) - 1, -1, -1):
            if any(e.target_url == target for e in articles[i]):
                keep_indices.add(i)
                seen += 1
                if seen >= max_articles_per_target:
                    break

    return [entry for i, art in enumerate(articles) if i in keep_indices for entry in art]


# Re-export from extracted sub-module. All existing callers import from
# ``backlink_publisher.anchor.profile`` — the re-exports keep those paths
# working without changes.
from ._profile_analysis import (  # noqa: F401, E402
    _group_into_articles,
    recent_degradation_rate,
    recent_secondary_count_split,
    recent_texts,
    recent_type_counts,
    recent_url_category_counts,
)
