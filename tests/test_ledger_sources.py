"""Unit 2 — three-store read + per-target join.

Seeds events.db (articles + events), injects a history list, and writes an
anchor profile into the sandboxed cache dir (autouse fixtures isolate both
config and cache dirs), then asserts the join collapses correctly.
"""

import json

import pytest

from backlink_publisher.anchor import profile as anchor_profile
from backlink_publisher.anchor.profile import ProfileEntry
from backlink_publisher.events import EventStore
from backlink_publisher.ledger.sources import build_target_buckets

T1 = "https://site.com/a"
T2 = "https://site.com/b"
T3 = "https://site.com/c"


def _add_article(store, live_url, target):
    store.add_article({
        "target_urls_json": json.dumps([target]),
        "live_url": live_url,
        "host": "medium.com",
    })


@pytest.fixture
def store(tmp_path):
    s = EventStore(path=tmp_path / "events.db")
    # T1: two links on two platforms
    _add_article(s, "https://medium.com/p/l1", T1)
    _add_article(s, "https://blog.example/l2", T1)
    # T1 variant target (trailing slash + utm) → must collapse into T1
    _add_article(s, "https://medium.com/p/l5", "https://site.com/a/?utm_source=q")
    # T2: one history row bundling two article_urls
    _add_article(s, "https://velog.io/l3a", T2)
    _add_article(s, "https://velog.io/l3b", T2)
    # T3: attempted (publish.failed) but never published → 0/0
    s.append("publish.failed", {"reason": "boom"}, target_url=T3)
    return s


HISTORY = [
    {"id": "h1", "platform": "medium", "target_url": T1,
     "article_urls": ["https://medium.com/p/l1"],
     "status": "published", "verified_at": "2026-05-20T00:00:00"},
    {"id": "h2", "platform": "blogger", "target_url": T1,
     "article_urls": ["https://blog.example/l2"], "status": "published"},
    {"id": "h5", "platform": "medium", "target_url": T1,
     "article_urls": ["https://medium.com/p/l5"], "status": "published"},
    {"id": "h3", "platform": "velog", "target_url": T2,
     "article_urls": ["https://velog.io/l3a", "https://velog.io/l3b"],
     "status": "published"},
]


def test_join_collapses_variants_and_universe(store):
    buckets = build_target_buckets(store=store, history=HISTORY)
    # Exactly three target buckets — the T1 utm/slash variant did NOT fragment.
    assert set(buckets) == {T1, T2, T3}

    t1 = buckets[T1]
    assert len(t1.links) == 3  # l1, l2, l5 (variant merged in)
    assert {lk.platform for lk in t1.links.values()} == {"medium", "blogger"}
    assert {lk.history_item_id for lk in t1.links.values()} == {"h1", "h2", "h5"}
    # l1 carries its verify timestamp; l2 never verified.
    l1 = t1.links["https://medium.com/p/l1"]
    assert l1.verified_at == "2026-05-20T00:00:00"
    l2 = t1.links["https://blog.example/l2"]
    assert l2.verified_at is None


def test_multi_article_row_is_one_bucket_two_links(store):
    buckets = build_target_buckets(store=store, history=HISTORY)
    t2 = buckets[T2]
    assert len(t2.links) == 2
    assert all(lk.platform == "velog" for lk in t2.links.values())
    assert all(lk.history_item_id == "h3" for lk in t2.links.values())


def test_attempted_target_appears_with_zero_links(store):
    buckets = build_target_buckets(store=store, history=HISTORY)
    assert T3 in buckets
    assert len(buckets[T3].links) == 0


def test_anchor_profile_attaches_per_target(store):
    anchor_profile.record_article("https://site.com", [
        ProfileEntry(ts="2026-05-20T00:00:00", link_role="main",
                     url_category="topic", anchor_type="exact",
                     anchor_text="kw", target_url=T1),
        ProfileEntry(ts="2026-05-20T00:00:00", link_role="secondary",
                     url_category="topic", anchor_type="branded",
                     anchor_text="brand", target_url=T1),
    ])
    buckets = build_target_buckets(store=store, history=HISTORY)
    assert buckets[T1].has_anchor_data is True
    assert len(buckets[T1].profile_entries) == 2
    # A target with no profile entries reports no anchor data (not silent 0.0).
    assert buckets[T2].has_anchor_data is False


def test_empty_stores_yield_empty(tmp_path):
    empty = EventStore(path=tmp_path / "empty.db")
    assert build_target_buckets(store=empty, history=[]) == {}
