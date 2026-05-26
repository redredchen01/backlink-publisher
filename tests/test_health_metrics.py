"""Tests for the health aggregation module (Plan 2026-05-25-006 / U2).

Seeds ``events.db`` directly via ``EventStore.append`` (controlling kind /
platform / error_class / target / ts_utc) and asserts the four aggregates,
with emphasis on the honesty rules: unverified-is-not-success, banner noise
excluded, latest-outcome total order, and "no data" vs "0%".
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backlink_publisher.events import EventStore, kinds
from webui_app import health_metrics as hm

# Wide-open lower bound so the window never excludes a seeded row unless a test
# is specifically about windowing.
SINCE_ALL = "2000-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _store() -> EventStore:
    return EventStore()


def _append(
    store: EventStore,
    kind: str,
    *,
    target_url: str | None = None,
    platform: str | None = "medium",
    error_class: str | None = None,
    ts_utc: str = "2026-05-20T10:00:00+00:00",
) -> int:
    # error_class is always present (value may be None — the "unclassified"
    # bucket); a present-but-None key satisfies the presence-only floor.
    payload: dict[str, object] = {"platform": platform, "error_class": error_class}
    # Satisfy the kind's R9 required-field floor with placeholders so these
    # metric tests exercise the real insert path instead of being quarantined.
    # Presence-only check, so the values are irrelevant to what the metrics read.
    for field in kinds.REQUIRED_FIELDS.get(kind, frozenset()):
        payload.setdefault(field, f"_test_{field}")
    return store.append(kind, payload, target_url=target_url, ts_utc=ts_utc)


# ── success_rate ─────────────────────────────────────────────────────────────


def test_success_rate_basic_two_targets():
    s = _store()
    _append(s, "publish.confirmed", target_url="https://a.com")
    _append(s, "publish.failed", target_url="https://b.com", error_class="network")

    rate = hm.success_rate(s, since_utc=SINCE_ALL)

    assert rate.targets == 2
    assert rate.confirmed == 1
    assert rate.pct == 50.0
    assert rate.has_data is True


def test_unverified_done_is_not_a_success_but_counts_against(tmp_path):
    s = _store()
    _append(s, "publish.unverified", target_url="https://a.com")

    rate = hm.success_rate(s, since_utc=SINCE_ALL)

    assert rate.targets == 1  # counted in the denominator
    assert rate.confirmed == 0
    assert rate.pct == 0.0  # 0%, NOT "no data"


def test_failed_then_confirmed_counts_once_as_latest_success():
    s = _store()
    _append(s, "publish.failed", target_url="https://a.com",
            error_class="x", ts_utc="2026-05-20T10:00:00+00:00")
    _append(s, "publish.confirmed", target_url="https://a.com",
            ts_utc="2026-05-20T11:00:00+00:00")

    rate = hm.success_rate(s, since_utc=SINCE_ALL)

    assert rate.targets == 1
    assert rate.confirmed == 1
    assert rate.pct == 100.0


def test_latest_outcome_breaks_ts_tie_by_id():
    # Same ts_utc; the later-inserted (higher id) is the latest outcome.
    s = _store()
    ts = "2026-05-20T10:00:00+00:00"
    _append(s, "publish.failed", target_url="https://a.com", error_class="x", ts_utc=ts)
    _append(s, "publish.confirmed", target_url="https://a.com", ts_utc=ts)  # higher id

    rate = hm.success_rate(s, since_utc=SINCE_ALL)
    assert rate.confirmed == 1  # confirmed wins the tie

    # Reverse insertion order on a new target → failed has the higher id.
    _append(s, "publish.confirmed", target_url="https://b.com", ts_utc=ts)
    _append(s, "publish.failed", target_url="https://b.com", error_class="x", ts_utc=ts)
    rate2 = hm.success_rate(s, since_utc=SINCE_ALL)
    # a.com still success, b.com now failure → 1 of 2.
    assert rate2.targets == 2
    assert rate2.confirmed == 1


def test_banner_and_image_gen_events_excluded():
    s = _store()
    _append(s, "publish.confirmed", target_url="https://a.com")
    # Non-publish kinds share the events table (direct append) — must not count.
    _append(s, "image_gen_invoked", target_url="https://a.com")
    _append(s, "banner.uploaded", target_url="https://a.com")

    rate = hm.success_rate(s, since_utc=SINCE_ALL)
    assert rate.targets == 1
    assert rate.confirmed == 1
    assert rate.pct == 100.0

    adapters = hm.per_adapter(s, since_utc=SINCE_ALL)
    assert sum(a.total for a in adapters) == 1  # only the confirmed publish


def test_window_excludes_old_events():
    s = _store()
    _append(s, "publish.confirmed", target_url="https://a.com",
            ts_utc="2026-05-01T10:00:00+00:00")

    rate = hm.success_rate(s, since_utc="2026-05-15T00:00:00+00:00")
    assert rate.targets == 0
    assert rate.pct is None  # no data, not 0%


def test_empty_db_returns_no_data_sentinels():
    s = _store()
    rate = hm.success_rate(s, since_utc=SINCE_ALL)
    assert rate.targets == 0
    assert rate.confirmed == 0
    assert rate.pct is None
    assert rate.has_data is False
    assert hm.per_adapter(s, since_utc=SINCE_ALL) == []
    assert hm.error_distribution(s, since_utc=SINCE_ALL) == []


# ── per_adapter ──────────────────────────────────────────────────────────────


def test_per_adapter_null_platform_is_unattributed_and_worst_first():
    s = _store()
    # medium: 3/3 confirmed; velog: 0/2 (both failed); null platform: 1/1.
    for _ in range(3):
        _append(s, "publish.confirmed", target_url="https://m.com", platform="medium")
    for i in range(2):
        _append(s, "publish.failed", target_url=f"https://v{i}.com",
                platform="velog", error_class="auth")
    _append(s, "publish.confirmed", target_url="https://n.com", platform=None)

    adapters = hm.per_adapter(s, since_utc=SINCE_ALL)
    by_name = {a.platform: a for a in adapters}

    assert by_name["Unattributed"].confirmed == 1
    assert by_name["velog"].pct == 0.0
    assert by_name["medium"].pct == 100.0
    # Worst-first: velog (0%) precedes medium (100%).
    names = [a.platform for a in adapters]
    assert names.index("velog") < names.index("medium")


def test_per_adapter_small_sample_flagged():
    s = _store()
    _append(s, "publish.confirmed", target_url="https://a.com", platform="medium")
    adapters = hm.per_adapter(s, since_utc=SINCE_ALL)
    assert adapters[0].small_sample is True  # 1 < threshold


# ── error_distribution ───────────────────────────────────────────────────────


def test_error_distribution_buckets_and_null_unclassified():
    s = _store()
    _append(s, "publish.failed", target_url="https://a.com", error_class="network")
    _append(s, "publish.failed", target_url="https://b.com", error_class="network")
    _append(s, "publish.failed", target_url="https://c.com", error_class=None)

    dist = hm.error_distribution(s, since_utc=SINCE_ALL)
    as_dict = {b.error_class: b.count for b in dist}
    assert as_dict["network"] == 2
    assert as_dict["unclassified"] == 1
    # Ordered by count desc.
    assert dist[0].error_class == "network"


# ── broken_channels ──────────────────────────────────────────────────────────


def test_broken_channels_filters_to_expired_and_identity_mismatch(monkeypatch):
    fake = {
        "medium": {"status": "expired", "last_verified_at": "2026-05-19T00:00:00+00:00"},
        "velog": {"status": "bound", "last_verified_at": None},
        "blogger": {"status": "identity_mismatch", "last_verified_at": None},
    }
    monkeypatch.setattr("webui_store.channel_status.list_all", lambda: fake)

    broken = hm.broken_channels()
    names = {b.channel: b.status for b in broken}

    assert names == {"medium": "expired", "blogger": "identity_mismatch"}


# ── build_health (integration) ───────────────────────────────────────────────


def test_build_health_assembles_all_aggregates(monkeypatch):
    s = _store()
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    _append(s, "publish.confirmed", target_url="https://a.com",
            ts_utc="2026-05-20T10:00:00+00:00")
    _append(s, "publish.failed", target_url="https://b.com",
            error_class="network", ts_utc="2026-05-20T10:00:00+00:00")
    monkeypatch.setattr("webui_store.channel_status.list_all", lambda: {})

    health = hm.build_health(s, now=now, window_days=30)

    assert health.window_days == 30
    assert health.success.targets == 2
    assert health.success.confirmed == 1
    assert health.success.pct == 50.0
    assert {b.error_class for b in health.errors} == {"network"}
    assert health.broken == []


def test_build_health_window_drops_events_before_lookback(monkeypatch):
    s = _store()
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    _append(s, "publish.confirmed", target_url="https://old.com",
            ts_utc="2026-03-01T10:00:00+00:00")  # ~80 days before now
    monkeypatch.setattr("webui_store.channel_status.list_all", lambda: {})

    health = hm.build_health(s, now=now, window_days=30)
    assert health.success.targets == 0
    assert health.success.pct is None
