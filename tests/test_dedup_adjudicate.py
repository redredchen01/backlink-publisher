"""Unit 5b — --adjudicate-uncertain (single) and --adjudicate-bulk (guarded).

Terminally resolve held (uncertain) dedup rows. Closed-set --to validated
post-parse (UsageError exit 1, not argparse exit 2). Bulk cannot fire without a
--confirm N matching the affected count.

Note: single adjudication selects by (platform, target_url) — consistent with
--forget and checkpoint-independent — rather than the plan's <run_id> <item_id>
(which would fail to resolve a still-held dedup row after --cleanup removed its
checkpoint).

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U5b).
"""

from __future__ import annotations

import sys
from io import StringIO

import pytest

from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher.idempotency import DedupKey, DedupStore
from backlink_publisher.idempotency import audit_log


@pytest.fixture(autouse=True)
def _fresh_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "cfg"))


def _run(argv) -> tuple[str, str, int]:
    old = (sys.stdin, sys.stdout, sys.stderr)
    try:
        sys.stdin = StringIO("")
        out, err = StringIO(), StringIO()
        sys.stdout, sys.stderr = out, err
        try:
            main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old


def _seed_uncertain(platform, target, age=0.0):
    store = DedupStore()
    key = DedupKey(platform=platform, target_url=target)
    store.intent_write(key)
    store.transition(key, "uncertain")
    if age:
        # Backdate updated_at directly for age-filter tests.
        with store.connect_immediate() as conn:
            conn.execute(
                "UPDATE dedup_keys SET updated_at = ? "
                "WHERE platform = ? AND account = ? AND target_url = ?",
                (__import__("time").time() - age, *key.as_tuple()),
            )
    return key


# --------------------------------------------------------------------------- #
# Single adjudication
# --------------------------------------------------------------------------- #
def test_adjudicate_single_to_succeeded():
    key = _seed_uncertain("velog", "https://money.example/h")
    _out, stderr, code = _run(
        ["--adjudicate-uncertain", "velog", "https://money.example/h",
         "--to", "succeeded", "--reason", "confirmed live"]
    )
    assert code == 0, stderr
    assert DedupStore().get(key).state == "done"
    entries = audit_log.read_entries()
    assert len(entries) == 1
    assert entries[0]["action"] == "adjudicate"
    assert entries[0]["from_state"] == "uncertain"
    assert entries[0]["to_state"] == "done"


def test_adjudicate_single_to_failed_is_republishable():
    key = _seed_uncertain("velog", "https://money.example/h")
    _run(["--adjudicate-uncertain", "velog", "https://money.example/h",
          "--to", "failed", "--reason", "did not land"])
    store = DedupStore()
    assert store.get(key).state == "failed"
    # failed -> re-publishable: a fresh intent loses (row present, not absent) but
    # the gate (U7) treats failed as dispatch — here we just assert terminal state.


def test_adjudicate_bogus_to_exits_1_not_2():
    _seed_uncertain("velog", "https://money.example/h")
    _out, stderr, code = _run(
        ["--adjudicate-uncertain", "velog", "https://money.example/h",
         "--to", "bogus", "--reason", "x"]
    )
    assert code == 1  # UsageError, not argparse's exit 2
    assert "--to must be one of" in stderr


def test_adjudicate_requires_reason():
    _seed_uncertain("velog", "https://money.example/h")
    _out, stderr, code = _run(
        ["--adjudicate-uncertain", "velog", "https://money.example/h", "--to", "failed"]
    )
    assert code == 1
    assert "reason" in stderr.lower()


def test_adjudicate_non_uncertain_refused():
    """Only held rows can be adjudicated; a done row is refused."""
    store = DedupStore()
    key = DedupKey(platform="blogger", target_url="https://money.example/done")
    store.intent_write(key)
    store.transition(key, "done", live_url="u")
    _out, stderr, code = _run(
        ["--adjudicate-uncertain", "blogger", "https://money.example/done",
         "--to", "failed", "--reason", "x"]
    )
    assert code == 1
    assert "not uncertain" in stderr
    assert store.get(key).state == "done"  # untouched


def test_adjudicate_absent_key_refused():
    _out, stderr, code = _run(
        ["--adjudicate-uncertain", "blogger", "https://never.seen/x",
         "--to", "failed", "--reason", "x"]
    )
    assert code == 1
    assert "no dedup row" in stderr


# --------------------------------------------------------------------------- #
# Guarded bulk
# --------------------------------------------------------------------------- #
def test_bulk_without_confirm_refused_and_shows_count():
    _seed_uncertain("velog", "https://money.example/a")
    _seed_uncertain("velog", "https://money.example/b")
    _out, stderr, code = _run(
        ["--adjudicate-bulk", "--platform", "velog", "--to", "failed", "--reason", "sweep"]
    )
    assert code == 1
    assert "affect 2 row" in stderr
    assert "--confirm 2" in stderr
    # nothing mutated
    assert DedupStore().list_by_state("uncertain")  # still held


def test_bulk_with_matching_confirm_resolves_all():
    _seed_uncertain("velog", "https://money.example/a")
    _seed_uncertain("velog", "https://money.example/b")
    _out, stderr, code = _run(
        ["--adjudicate-bulk", "--platform", "velog", "--to", "failed",
         "--reason", "sweep", "--confirm", "2"]
    )
    assert code == 0, stderr
    store = DedupStore()
    assert store.list_by_state("uncertain") == []
    assert len(store.list_by_state("failed")) == 2
    # one audit entry per row, shared reason.
    entries = [e for e in audit_log.read_entries() if e["action"] == "adjudicate"]
    assert len(entries) == 2
    assert {e["reason"] for e in entries} == {"sweep"}


def test_bulk_wrong_confirm_count_refused():
    _seed_uncertain("velog", "https://money.example/a")
    _seed_uncertain("velog", "https://money.example/b")
    _out, stderr, code = _run(
        ["--adjudicate-bulk", "--platform", "velog", "--to", "failed",
         "--reason", "sweep", "--confirm", "1"]
    )
    assert code == 1
    assert "affect 2 row" in stderr
    assert DedupStore().list_by_state("uncertain")  # untouched


def test_bulk_list_affected_previews_without_mutating():
    _seed_uncertain("velog", "https://money.example/a")
    stdout, _stderr, code = _run(
        ["--adjudicate-bulk", "--platform", "velog", "--to", "failed",
         "--reason", "x", "--list-affected"]
    )
    assert code == 0
    assert "https://money.example/a" in stdout
    assert DedupStore().get(
        DedupKey(platform="velog", target_url="https://money.example/a")
    ).state == "uncertain"  # not mutated


def test_bulk_older_than_filter():
    _seed_uncertain("velog", "https://money.example/old", age=8 * 86400)
    _seed_uncertain("velog", "https://money.example/new", age=60)
    _out, stderr, code = _run(
        ["--adjudicate-bulk", "--platform", "velog", "--older-than", "7d",
         "--to", "failed", "--reason", "stale", "--confirm", "1"]
    )
    assert code == 0, stderr
    store = DedupStore()
    assert store.get(
        DedupKey(platform="velog", target_url="https://money.example/old")
    ).state == "failed"
    assert store.get(
        DedupKey(platform="velog", target_url="https://money.example/new")
    ).state == "uncertain"  # too new, untouched


def test_bulk_bad_older_than_spec_exits_1():
    _seed_uncertain("velog", "https://money.example/a")
    _out, stderr, code = _run(
        ["--adjudicate-bulk", "--platform", "velog", "--older-than", "soon",
         "--to", "failed", "--reason", "x", "--confirm", "1"]
    )
    assert code == 1
    assert "--older-than" in stderr


# --------------------------------------------------------------------------- #
# Mutual exclusion
# --------------------------------------------------------------------------- #
def test_adjudicate_conflicts_with_forget():
    _out, stderr, code = _run(
        ["--adjudicate-uncertain", "velog", "https://money.example/h",
         "--to", "failed", "--reason", "x",
         "--forget", "velog", "https://money.example/h", "--reason", "y"]
    )
    assert code == 2
    assert "mutually exclusive" in stderr
