"""Unit 4 — audit-state duplicate/aged detection over the authoritative dedup
store (R16). Read-only; always exit 0.

The store's ``PRIMARY KEY (platform, account, target_url)`` makes "two done rows
sharing one key" structurally impossible, so the meaningful duplicate is two
distinct keys whose ``done`` rows resolve to the same canonical ``live_url``.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U4).
"""

from __future__ import annotations

import io
import json
import sys
import time

import pytest

from backlink_publisher.audit import DedupAuditRow, StoreSnapshot, find_divergences
from backlink_publisher.cli.audit_state import main
from backlink_publisher.idempotency import DedupKey, DedupStore


@pytest.fixture(autouse=True)
def fresh_dirs(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg))


def _row(platform, target, state, live_url=None, age=0.0, account="default"):
    return DedupAuditRow(
        platform=platform,
        account=account,
        target_url=target,
        state=state,
        verify_ok=1 if state == "done" else None,
        live_url=live_url,
        owner_pid=None,
        owner_started_at=None,
        updated_at=time.time() - age,
    )


def _classes(records):
    return sorted(r.divergence_class for r in records)


# --------------------------------------------------------------------------- #
# duplicate_key (shared live_url across keys)
# --------------------------------------------------------------------------- #
def test_two_done_rows_one_live_url_is_one_duplicate():
    snap = StoreSnapshot(articles=[], history=[], dedup=[
        _row("blogger", "https://x.com/a", "done", "https://live.com/p"),
        _row("medium", "https://x.com/b", "done", "https://live.com/p"),
    ])
    recs = [r for r in find_divergences(snap) if r.divergence_class == "duplicate_key"]
    assert len(recs) == 1
    assert recs[0].canonical_url == "https://live.com/p"
    assert recs[0].source == "dedup"
    assert recs[0].source_tier == "high-signal"
    assert len(recs[0].details["keys"]) == 2


def test_distinct_live_urls_are_not_duplicates():
    snap = StoreSnapshot(articles=[], history=[], dedup=[
        _row("blogger", "https://x.com/a", "done", "https://live.com/p1"),
        _row("medium", "https://x.com/b", "done", "https://live.com/p2"),
    ])
    assert not [r for r in find_divergences(snap) if r.divergence_class == "duplicate_key"]


def test_done_rows_without_live_url_do_not_form_duplicate():
    """NULL live_urls must not collapse into one duplicate group (they are
    suspect_done, not duplicate_key)."""
    snap = StoreSnapshot(articles=[], history=[], dedup=[
        _row("blogger", "https://x.com/a", "done", None),
        _row("medium", "https://x.com/b", "done", None),
    ])
    classes = _classes(find_divergences(snap))
    assert "duplicate_key" not in classes
    assert classes.count("suspect_done") == 2


# --------------------------------------------------------------------------- #
# aged uncertain / attempting
# --------------------------------------------------------------------------- #
def test_aged_uncertain_flagged_fresh_not():
    snap = StoreSnapshot(articles=[], history=[], dedup=[
        _row("velog", "https://x.com/old", "uncertain", age=8 * 24 * 3600),
        _row("velog", "https://x.com/new", "uncertain", age=60),
    ])
    recs = [r for r in find_divergences(snap) if r.divergence_class == "aged_uncertain"]
    assert len(recs) == 1
    assert recs[0].canonical_url == "https://x.com/old"


def test_aged_attempting_flagged_past_lease_ttl():
    snap = StoreSnapshot(articles=[], history=[], dedup=[
        _row("blogger", "https://x.com/stuck", "attempting", age=7200),
        _row("blogger", "https://x.com/live", "attempting", age=30),
    ])
    recs = [r for r in find_divergences(snap) if r.divergence_class == "aged_attempting"]
    assert len(recs) == 1
    assert recs[0].canonical_url == "https://x.com/stuck"


# --------------------------------------------------------------------------- #
# suspect_done
# --------------------------------------------------------------------------- #
def test_suspect_done_null_live_url():
    snap = StoreSnapshot(articles=[], history=[], dedup=[
        _row("blogger", "https://x.com/seeded", "done", None),
        _row("blogger", "https://x.com/real", "done", "https://live.com/real"),
    ])
    recs = [r for r in find_divergences(snap) if r.divergence_class == "suspect_done"]
    assert len(recs) == 1
    assert recs[0].canonical_url == "https://x.com/seeded"


def test_clean_store_no_findings():
    snap = StoreSnapshot(articles=[], history=[], dedup=[
        _row("blogger", "https://x.com/a", "done", "https://live.com/a"),
        _row("medium", "https://x.com/b", "done", "https://live.com/b"),
        _row("velog", "https://x.com/c", "uncertain", age=60),
    ])
    assert find_divergences(snap) == []


# --------------------------------------------------------------------------- #
# Integration through the CLI (reads dedup.db from the config dir; exit 0)
# --------------------------------------------------------------------------- #
def _run(argv=None):
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    code = 0
    try:
        main(argv or [])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.stdout, sys.stderr = saved
    return out.getvalue(), err.getvalue(), code


def _seed_done(store, platform, target, live_url):
    key = DedupKey(platform=platform, target_url=target)
    store.intent_write(key)
    store.transition(key, "done", live_url=live_url)


def test_cli_reports_duplicate_and_exits_0():
    store = DedupStore()
    _seed_done(store, "blogger", "https://x.com/a", "https://live.com/shared")
    _seed_done(store, "medium", "https://x.com/b", "https://live.com/shared")

    stdout, stderr, code = _run()
    assert code == 0, stderr
    findings = [json.loads(l) for l in stdout.strip().splitlines() if l]
    dup = [f for f in findings if f["class"] == "duplicate_key"]
    assert len(dup) == 1
    assert dup[0]["canonical_url"] == "https://live.com/shared"
    assert "duplicate_key" in stderr  # remediation line


def test_cli_suspect_done_through_store():
    store = DedupStore()
    key = DedupKey(platform="blogger", target_url="https://x.com/seeded")
    store.intent_write(key)
    store.transition(key, "done")  # no live_url

    stdout, _stderr, code = _run()
    assert code == 0
    findings = [json.loads(l) for l in stdout.strip().splitlines() if l]
    assert any(f["class"] == "suspect_done" for f in findings)


def test_cli_clean_dedup_store_exits_0_no_dup():
    store = DedupStore()
    _seed_done(store, "blogger", "https://x.com/a", "https://live.com/a")

    stdout, _stderr, code = _run()
    assert code == 0
    findings = [json.loads(l) for l in stdout.strip().splitlines() if l]
    assert not [f for f in findings if f["class"] == "duplicate_key"]


def test_cli_finding_jsonl_shape_matches_divergence_record():
    store = DedupStore()
    _seed_done(store, "blogger", "https://x.com/a", "https://live.com/shared")
    _seed_done(store, "medium", "https://x.com/b", "https://live.com/shared")

    stdout, _stderr, _code = _run()
    findings = [json.loads(l) for l in stdout.strip().splitlines() if l]
    dup = next(f for f in findings if f["class"] == "duplicate_key")
    assert set(dup) >= {"class", "source", "source_tier", "authority"}
    assert dup["source"] == "dedup"
