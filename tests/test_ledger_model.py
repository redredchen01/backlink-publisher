"""Unit 1 — LedgerRow model + worst_liveness precedence."""

from backlink_publisher.ledger.model import (
    DofollowBreakdown,
    LedgerRow,
    worst_liveness,
)


def test_ledger_row_jsonl_round_trips():
    row = LedgerRow(
        target_url="https://example.com/p",
        total_links=3,
        live_links=2,
        dofollow=DofollowBreakdown(dofollow=2, nofollow=1, nofollow_low=1),
        live_dofollow=2,
        platform_count=2,
        platforms=["medium", "blogger"],
        exact_match_pct=0.25,
        liveness="live",
        liveness_verified_at="2026-05-20T00:00:00Z",
        history_item_ids=["a", "b"],
    )
    d = row.to_jsonl_dict()
    # Nested dataclass serializes to a plain dict (JSONL-safe).
    assert d["dofollow"] == {
        "dofollow": 2,
        "uncertain": 0,
        "nofollow": 1,
        "unknown": 0,
        "nofollow_high": 0,
        "nofollow_low": 1,
    }
    assert d["target_url"] == "https://example.com/p"
    assert d["live_dofollow"] == 2
    assert d["platforms"] == ["medium", "blogger"]


def test_zero_link_row_renders_0_of_0_unverified():
    row = LedgerRow(target_url="https://example.com/orphan")
    assert row.total_links == 0
    assert row.live_links == 0
    assert row.live_dofollow == 0
    assert row.liveness == "unverified"
    assert row.liveness_verified_at is None
    assert row.to_jsonl_dict()["dofollow"]["unknown"] == 0


def test_worst_liveness_precedence():
    # failed > stale > live > unverified
    assert worst_liveness(["live", "failed", "stale"]) == "failed"
    assert worst_liveness(["live", "stale", "unverified"]) == "stale"
    assert worst_liveness(["live", "unverified"]) == "live"
    assert worst_liveness(["unverified"]) == "unverified"


def test_worst_liveness_empty_is_unverified():
    assert worst_liveness([]) == "unverified"


def test_unknown_classification_is_distinct_from_nofollow():
    # A row with one unknown-platform link must not inflate the nofollow count.
    bd = DofollowBreakdown(unknown=1)
    assert bd.nofollow == 0
    assert bd.unknown == 1
