"""Unit 3 — preview manifest: a read-only, pre-dispatch verdict pass over planned
rows that consults the dedup store. JSONL data on stdout (carries campaign URLs);
human summary on stderr (counts + HMAC digest only — the leak boundary).

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U3).
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher.idempotency import DedupKey, DedupStore


@pytest.fixture(autouse=True)
def _fresh_dedup_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "cfg"))


def _payload(target: str, item_id: str, platform: str = "blogger") -> dict:
    return {
        "id": item_id,
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": target,
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": f"Article {item_id}",
        "slug": f"article-{item_id}",
        "excerpt": "A test excerpt.",
        "tags": ["tag1", "tag2"],
        "content_markdown": f"Article about {target} and https://example.com.",
        "links": [
            {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
            {"url": target, "anchor": "Article", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub", "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "SEO",
            "description": "SEO description",
            "canonical_url": target,
        },
    }


def _run_manifest(rows: list[dict], argv=None) -> tuple[str, str, int]:
    old = (sys.stdin, sys.stdout, sys.stderr)
    try:
        sys.stdin = StringIO("\n".join(json.dumps(r) for r in rows))
        out, err = StringIO(), StringIO()
        sys.stdout, sys.stderr = out, err
        try:
            main(argv or ["--preview-manifest"])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old


_NEW = "https://example.com/new-post"
_DONE = "https://example.com/done-post"
_HELD = "https://example.com/held-post"


def _seed():
    """Seed one done + one uncertain key (blogger). Returns the done live_url."""
    store = DedupStore()
    done_key = DedupKey(platform="blogger", target_url=_DONE)
    store.intent_write(done_key)
    store.transition(done_key, "done", live_url="https://blogger.example.com/p/done")

    held_key = DedupKey(platform="blogger", target_url=_HELD)
    store.intent_write(held_key)
    store.transition(held_key, "uncertain")
    return "https://blogger.example.com/p/done"


# --------------------------------------------------------------------------- #
# Happy path: verdicts match store state
# --------------------------------------------------------------------------- #
def test_verdicts_match_store_state():
    _seed()
    rows = [
        _payload(_NEW, "r-new"),
        _payload(_DONE, "r-done"),
        _payload(_HELD, "r-held"),
    ]
    stdout, stderr, code = _run_manifest(rows)
    assert code == 0, stderr

    entries = {json.loads(l)["id"]: json.loads(l) for l in stdout.strip().splitlines()}
    assert entries["r-new"]["verdict"] == "NEW"
    assert entries["r-new"]["state"] is None
    assert entries["r-done"]["verdict"] == "SKIP-DUPLICATE"
    assert entries["r-done"]["live_url"] == "https://blogger.example.com/p/done"
    assert entries["r-held"]["verdict"] == "HOLD-UNCERTAIN"


def test_stdout_carries_canonical_target_and_digest():
    _seed()
    stdout, _stderr, _code = _run_manifest([_payload(_DONE, "r-done")])
    entry = json.loads(stdout.strip())
    assert entry["target_url"] == _DONE
    assert entry["key_digest"] and len(entry["key_digest"]) == 16
    assert entry["force"] is False


def test_failed_key_is_new():
    store = DedupStore()
    k = DedupKey(platform="blogger", target_url=_NEW)
    store.intent_write(k)
    store.transition(k, "failed")
    stdout, _stderr, _code = _run_manifest([_payload(_NEW, "r1")])
    assert json.loads(stdout.strip())["verdict"] == "NEW"


# --------------------------------------------------------------------------- #
# Leak boundary: stderr carries counts + digest, never raw URLs
# --------------------------------------------------------------------------- #
def test_stderr_summary_never_leaks_urls():
    _seed()
    rows = [_payload(_NEW, "r-new"), _payload(_DONE, "r-done"), _payload(_HELD, "r-held")]
    stdout, stderr, _code = _run_manifest(rows)

    # The campaign URLs must appear in stdout (data channel)…
    assert _DONE in stdout
    assert "https://blogger.example.com/p/done" in stdout
    # …but NEVER in the stderr human summary (leak boundary).
    assert _NEW not in stderr
    assert _DONE not in stderr
    assert _HELD not in stderr
    assert "blogger.example.com/p/done" not in stderr
    # stderr does carry counts.
    assert "NEW=1" in stderr
    assert "SKIP-DUPLICATE=1" in stderr
    assert "HOLD-UNCERTAIN=1" in stderr


def test_stderr_carries_digest_present_in_stdout():
    _seed()
    stdout, stderr, _code = _run_manifest([_payload(_DONE, "r-done")])
    digest = json.loads(stdout.strip())["key_digest"]
    assert digest in stderr


# --------------------------------------------------------------------------- #
# No network / no side effects
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks._acquire_publish_leases")
def test_no_publish_no_lease(mock_lease, mock_pub):
    _seed()
    _stdout, _stderr, code = _run_manifest([_payload(_DONE, "r-done")])
    assert code == 0
    mock_pub.assert_not_called()
    mock_lease.assert_not_called()


def test_manifest_does_not_write_store():
    """Read-only: a NEW row stays absent after the preview (no intent written)."""
    rows = [_payload(_NEW, "r-new")]
    _run_manifest(rows)
    assert DedupStore().get(DedupKey(platform="blogger", target_url=_NEW)) is None


# --------------------------------------------------------------------------- #
# Digest is HMAC-keyed per store (not a reversible bare hash)
# --------------------------------------------------------------------------- #
def test_two_stores_digest_same_key_differently(tmp_path):
    key = DedupKey(platform="blogger", target_url=_DONE)
    a = DedupStore(path=tmp_path / "a" / "dedup.db")
    b = DedupStore(path=tmp_path / "b" / "dedup.db")
    assert a.key_digest(key) != b.key_digest(key)
    # …but a single store is stable across calls.
    assert a.key_digest(key) == a.key_digest(key)


# --------------------------------------------------------------------------- #
# Mutual exclusion
# --------------------------------------------------------------------------- #
def test_preview_manifest_conflicts_with_list_runs():
    _stdout, stderr, code = _run_manifest(
        [_payload(_NEW, "r1")], argv=["--preview-manifest", "--list-runs"]
    )
    assert code == 2
    assert "mutually exclusive" in stderr
