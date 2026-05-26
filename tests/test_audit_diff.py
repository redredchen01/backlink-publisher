"""Unit 2 — audit.diff. Builds StoreSnapshot views directly and asserts the R1
+ R3 divergence classes, with edge guards for fan-out, draft URLs,
canonicalization, and the deferred-R2 duplicate-URL case."""

import json

from backlink_publisher.audit.diff import find_divergences
from backlink_publisher.audit.readers import ArticleRow, StoreSnapshot


def _article(article_id, live_url, target="https://site.com/p"):
    return ArticleRow(
        article_id=article_id,
        host=None,
        live_url=live_url,
        target_urls_json=json.dumps([target]),
        published_at_utc=None,
        run_id=None,
    )


def _published(rec_id, urls, status="published"):
    return {"id": rec_id, "status": status, "article_urls": urls}


def _snap(articles=(), history=(), transient=False):
    return StoreSnapshot(
        articles=list(articles), history=list(history), transient=transient
    )


def _classes(records):
    return sorted(r.divergence_class for r in records)


def test_null_url_orphan():
    recs = find_divergences(_snap(articles=[_article(1, None)]))
    assert _classes(recs) == ["null_url_orphan"]
    assert recs[0].article_id == 1
    assert recs[0].source == "articles"


def test_history_orphan_record_absent_from_articles():
    snap = _snap(
        articles=[_article(1, "https://medium.com/post1")],
        history=[_published("h2", ["https://substack.com/post2"])],
    )
    recs = find_divergences(snap)
    classes = _classes(recs)
    # post2 is in history but not articles -> history_orphan;
    # post1 is in articles but not history -> article_orphan.
    assert "history_orphan" in classes
    assert "article_orphan" in classes
    ho = next(r for r in recs if r.divergence_class == "history_orphan")
    assert ho.details["history_id"] == "h2"


def test_clean_store_no_divergence():
    snap = _snap(
        articles=[_article(1, "https://medium.com/post1")],
        history=[_published("h1", ["https://medium.com/post1"])],
    )
    assert find_divergences(snap) == []


def test_fanout_one_record_two_urls_two_articles_no_finding():
    snap = _snap(
        articles=[
            _article(1, "https://medium.com/post1"),
            _article(2, "https://medium.com/post2"),
        ],
        history=[
            _published("h1", ["https://medium.com/post1", "https://medium.com/post2"])
        ],
    )
    assert find_divergences(snap) == []


def test_draft_url_in_article_urls_does_not_false_flag():
    # article_urls = [published(matched), draft(no article)] -> no history_orphan
    # because the published URL matched; draft legitimately has no article row.
    snap = _snap(
        articles=[_article(1, "https://medium.com/post1")],
        history=[
            _published("h1", ["https://medium.com/post1", "https://medium.com/draft-xyz"])
        ],
    )
    assert find_divergences(snap) == []


def test_canonicalization_equivalent_urls_no_finding():
    # utm + trailing slash differences are absorbed by canonicalize_url.
    snap = _snap(
        articles=[_article(1, "https://medium.com/post1")],
        history=[_published("h1", ["https://medium.com/post1/?utm_source=x"])],
    )
    assert find_divergences(snap) == []


def test_deferred_r2_duplicate_url_not_reported_as_r3():
    # Two distinct published records share one canonical URL; only one article
    # exists (the other was UNIQUE-dropped = deferred R2). Neither record is a
    # history_orphan (both match the single article), so R3 does NOT fire.
    snap = _snap(
        articles=[_article(1, "https://medium.com/post1")],
        history=[
            _published("h1", ["https://medium.com/post1"]),
            _published("h2", ["https://medium.com/post1"]),
        ],
    )
    assert find_divergences(snap) == []


def test_non_published_history_rows_ignored():
    snap = _snap(
        articles=[_article(1, "https://medium.com/post1")],
        history=[
            _published("h1", ["https://medium.com/post1"]),
            _published("h2", ["https://x.com/failed"], status="failed"),
        ],
    )
    assert find_divergences(snap) == []


def test_transient_marks_possibly_transient_authority():
    snap = _snap(articles=[_article(1, None)], transient=True)
    recs = find_divergences(snap)
    assert recs[0].authority == "possibly-transient"


def test_malformed_article_urls_non_list_does_not_crash():
    # A record whose article_urls is an int/dict (corrupt history) must not
    # crash find_divergences; it's simply treated as having no published URLs.
    snap = _snap(
        articles=[_article(1, "https://medium.com/post1")],
        history=[
            {"id": "bad1", "status": "published", "article_urls": 123},
            {"id": "bad2", "status": "published", "article_urls": {"x": "y"}},
        ],
    )
    recs = find_divergences(snap)
    # post1 has no history match -> article_orphan; the malformed rows contribute
    # nothing and do not raise.
    assert _classes(recs) == ["article_orphan"]


def test_canonical_collision_reports_all_article_ids():
    # Two articles whose raw live_urls differ but canonicalize identically
    # (trailing slash) — both article_ids must be reported, not just one.
    snap = _snap(
        articles=[
            _article(1, "https://medium.com/dup"),
            _article(2, "https://medium.com/dup/"),
        ],
        history=[],
    )
    recs = find_divergences(snap)
    assert _classes(recs) == ["article_orphan", "article_orphan"]
    assert sorted(r.article_id for r in recs) == [1, 2]


def test_to_jsonl_dict_shape():
    recs = find_divergences(_snap(articles=[_article(7, None)]))
    d = recs[0].to_jsonl_dict()
    assert d["class"] == "null_url_orphan"
    assert d["source"] == "articles"
    assert d["source_tier"] == "high-signal"
    assert d["authority"] == "indeterminate"
    assert d["article_id"] == 7
    assert d["details"]["reason"] == "live_url IS NULL"
