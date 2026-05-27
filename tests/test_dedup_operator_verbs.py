"""Unit 5a — operator escape verbs: ``--forget`` (single-key) and
``--list-uncertain``, plus the append-only audit log (O_APPEND + flock).

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U5a).
"""

from __future__ import annotations

import sys
import threading
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


def _seed_done(platform="blogger", target="https://money.example/a", live="https://b.com/p"):
    store = DedupStore()
    key = DedupKey(platform=platform, target_url=target)
    store.intent_write(key)
    store.transition(key, "done", live_url=live)
    return key


# --------------------------------------------------------------------------- #
# --forget happy path
# --------------------------------------------------------------------------- #
def test_forget_clears_done_to_absent_with_one_audit_entry():
    key = _seed_done()
    _out, stderr, code = _run(
        ["--forget", "blogger", "https://money.example/a", "--reason", "mistaken post"]
    )
    assert code == 0, stderr

    assert DedupStore().get(key) is None  # cleared -> absent (re-publishable)
    entries = audit_log.read_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e["action"] == "forget"
    assert e["from_state"] == "done"
    assert e["to_state"] == "absent"
    assert e["reason"] == "mistaken post"
    assert e["platform"] == "blogger"


def test_forget_then_reintent_wins_again():
    key = _seed_done()
    _run(["--forget", "blogger", "https://money.example/a", "--reason", "redo"])
    assert DedupStore().intent_write(key).won is True  # absent -> can re-acquire


def test_forget_absent_key_still_records_audit_entry():
    _out, _err, code = _run(
        ["--forget", "blogger", "https://never.seen/x", "--reason", "cleanup"]
    )
    assert code == 0
    assert len(audit_log.read_entries()) == 1


# --------------------------------------------------------------------------- #
# --forget guards (single-key only)
# --------------------------------------------------------------------------- #
def test_forget_without_reason_rejected():
    _seed_done()
    _out, stderr, code = _run(["--forget", "blogger", "https://money.example/a"])
    assert code == 1
    assert "reason" in stderr.lower()
    assert audit_log.read_entries() == []  # rejected before any write


def test_forget_glob_rejected():
    _out, stderr, code = _run(
        ["--forget", "blogger", "https://money.example/*", "--reason", "bulk attempt"]
    )
    assert code == 1
    assert "glob" in stderr.lower() or "single" in stderr.lower()
    assert audit_log.read_entries() == []


# --------------------------------------------------------------------------- #
# --list-uncertain
# --------------------------------------------------------------------------- #
def test_list_uncertain_shows_held_rows():
    store = DedupStore()
    k = DedupKey(platform="velog", target_url="https://money.example/held")
    store.intent_write(k)
    store.transition(k, "uncertain")
    stdout, _stderr, code = _run(["--list-uncertain"])
    assert code == 0
    assert "velog" in stdout
    assert "https://money.example/held" in stdout


def test_list_uncertain_empty():
    _seed_done()  # a done row, not uncertain
    stdout, _stderr, code = _run(["--list-uncertain"])
    assert code == 0
    assert "No uncertain" in stdout


def test_list_uncertain_platform_filter():
    store = DedupStore()
    for plat, tgt in [("velog", "https://money.example/v"), ("blogger", "https://money.example/b")]:
        k = DedupKey(platform=plat, target_url=tgt)
        store.intent_write(k)
        store.transition(k, "uncertain")
    stdout, _stderr, code = _run(["--list-uncertain", "--platform", "velog"])
    assert code == 0
    assert "velog" in stdout
    assert "https://money.example/b" not in stdout


# --------------------------------------------------------------------------- #
# Mutual exclusion
# --------------------------------------------------------------------------- #
def test_forget_conflicts_with_list_runs():
    _out, stderr, code = _run(
        ["--forget", "blogger", "https://money.example/a", "--reason", "x", "--list-runs"]
    )
    assert code == 2
    assert "mutually exclusive" in stderr


# --------------------------------------------------------------------------- #
# Audit log is a genuine append (concurrency) — atomic_write would lose entries
# --------------------------------------------------------------------------- #
def test_concurrent_appends_lose_nothing():
    """N threads append simultaneously; every entry must survive (O_APPEND+flock).
    A whole-file read-modify-rewrite (atomic_write) would clobber entries here."""
    n = 12
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        audit_log.append_entry(
            action="forget",
            platform="blogger",
            target_url=f"https://money.example/{i}",
            from_state="done",
            to_state="absent",
            reason=f"r{i}",
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = audit_log.read_entries()
    assert len(entries) == n
    assert {e["target_url"] for e in entries} == {
        f"https://money.example/{i}" for i in range(n)
    }


def test_forget_vs_concurrent_intent_consistent_and_entry_survives():
    """--forget (delete + append) racing a concurrent intent_write resolves to one
    consistent store state, and the forget's audit entry is never lost."""
    key = _seed_done()
    store = DedupStore()
    barrier = threading.Barrier(2)

    def do_forget():
        barrier.wait()
        audit_log.append_entry(
            action="forget", platform=key.platform, target_url=key.target_url,
            account=key.account, from_state="done", to_state="absent", reason="race",
        )
        store.forget(key)

    def do_intent():
        barrier.wait()
        store.intent_write(key)

    threads = [threading.Thread(target=do_forget), threading.Thread(target=do_intent)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rec = store.get(key)
    # The store ends in exactly one well-defined state (absent or attempting),
    # never a torn/duplicate row.
    assert rec is None or rec.state == "attempting"
    # The forget audit entry survived the race.
    assert any(e["reason"] == "race" for e in audit_log.read_entries())
