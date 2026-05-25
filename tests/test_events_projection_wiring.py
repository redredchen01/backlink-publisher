"""Plan 005 / U4: the projection runs after publish/resume.

`flush_for` had zero production callers — events.db was never populated in
production. These tests cover the fail-safe `project_run_safe` helper (real
checkpoint module + real events.db: path resolution, projection, health
marker) and assert the publish/resume CLI actually invokes it.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from backlink_publisher.checkpoint import create_checkpoint, update_item
from backlink_publisher.events import EventStore, project_run_safe
from backlink_publisher.events.projector import _HEALTH_SOURCE


@pytest.fixture(autouse=True)
def _isolate_dirs(tmp_path, monkeypatch):
    # events.db lives under the config dir; checkpoints under the cache dir.
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    yield


def _kinds() -> list[str]:
    with EventStore().connect() as conn:
        conn.row_factory = sqlite3.Row
        return [r["kind"] for r in conn.execute("SELECT kind FROM events ORDER BY id")]


def _seed_done_run(*, verified: bool = True, platform: str = "blogger") -> str:
    rows = [{
        "id": "r0", "platform": platform, "language": "en",
        "publish_mode": "draft", "target_url": "https://example.com/a",
        "main_domain": "https://example.com", "title": "T", "slug": "t",
        "content_markdown": "x", "links": [],
    }]
    run_id, _ = create_checkpoint(rows, platform=platform, mode="publish")
    update_item(
        run_id, "r0", "done",
        published_url="https://blog.example.org/p0",
        adapter=platform, verified=verified,
        completed_at="2026-05-25T12:00:00+00:00",
    )
    return run_id


def test_project_run_safe_populates_events_db_from_real_checkpoint():
    run_id = _seed_done_run(verified=True)

    result = project_run_safe(run_id)

    assert result is not None
    assert "publish.confirmed" in _kinds()


def test_project_run_safe_projects_unverified_as_unverified_kind():
    run_id = _seed_done_run(verified=False)

    project_run_safe(run_id)

    kinds = _kinds()
    assert "publish.unverified" in kinds
    assert "publish.confirmed" not in kinds


def test_project_run_safe_records_health_on_success():
    run_id = _seed_done_run()

    project_run_safe(run_id)

    with EventStore().connect() as conn:
        row = conn.execute(
            "SELECT last_seen_state_json FROM projection_cursor WHERE source = ?",
            (_HEALTH_SOURCE,),
        ).fetchone()
    assert row is not None
    state = json.loads(row[0])
    assert state["last_ok_at"]
    assert state["last_error"] is None


def test_project_run_safe_is_failsafe_on_missing_checkpoint():
    # A well-formed but nonexistent run_id → no checkpoint file.
    result = project_run_safe("20260101T000000-deadbeef")

    assert result is None  # swallowed, no raise


def test_project_run_safe_failsafe_on_locked_db_records_error():
    run_id = _seed_done_run()

    with patch(
        "backlink_publisher.events.projector.flush_for",
        side_effect=sqlite3.OperationalError("database is locked"),
    ):
        result = project_run_safe(run_id)

    assert result is None  # concurrent-writer lock is non-fatal
    with EventStore().connect() as conn:
        row = conn.execute(
            "SELECT last_seen_state_json FROM projection_cursor WHERE source = ?",
            (_HEALTH_SOURCE,),
        ).fetchone()
    state = json.loads(row[0])
    assert "OperationalError" in state["last_error"]


def test_project_run_safe_is_idempotent():
    run_id = _seed_done_run()

    project_run_safe(run_id)
    project_run_safe(run_id)  # second call must not double-count

    assert _kinds().count("publish.confirmed") == 1


def _full_payload(item_id="p0", platform="blogger"):
    return {
        "id": item_id, "platform": platform, "language": "en",
        "publish_mode": "draft", "target_url": "https://example.com/article",
        "main_domain": "https://example.com", "url_mode": "A",
        "title": f"Test Article {item_id}", "slug": "test-article",
        "excerpt": "A test excerpt.", "tags": ["tag1", "tag2"],
        "content_markdown": "This is a test article about https://example.com.",
        "links": [
            {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub", "kind": "supporting", "required": False},
        ],
        "seo": {"title": "Test SEO", "description": "SEO description",
                "canonical_url": "https://example.com/article"},
    }


@patch("backlink_publisher.cli._publish_helpers.verify_published")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_cli_populates_events_db_end_to_end(mock_pub, mock_verify, mock_vp):
    """End-to-end: a real (non-dry) publish writes its outcome to the
    checkpoint AND projects it into events.db via the U4 wiring."""
    import sys
    from io import StringIO
    from backlink_publisher.publishing.adapters.base import AdapterResult
    from backlink_publisher.linkcheck.verify import VerificationResult
    from backlink_publisher.cli.publish_backlinks import main

    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        published_url="https://blogger.example.com/p0",
    )
    mock_vp.return_value = VerificationResult(ok=True, reason="")

    old_stdin, old_out, old_err = sys.stdin, sys.stdout, sys.stderr
    sys.stdin = StringIO(json.dumps(_full_payload()))
    sys.stdout, sys.stderr = StringIO(), StringIO()
    try:
        try:
            main(["--mode", "draft", "--platform", "blogger"])
        except SystemExit:
            pass
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_out, old_err

    # The publish outcome reached events.db through the inline projection.
    assert "publish.confirmed" in _kinds()


@patch("backlink_publisher.cli._publish_helpers.verify_published")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_cli_verification_failure_projects_unverified(mock_pub, mock_verify, mock_vp):
    """D5 end-to-end through the real CLI: a publish whose verification FAILS
    writes verified=False to the checkpoint and projects publish.unverified
    (NOT publish.confirmed), and the CLI exits 5."""
    import sys
    from io import StringIO
    from backlink_publisher.publishing.adapters.base import AdapterResult
    from backlink_publisher.linkcheck.verify import VerificationResult
    from backlink_publisher.cli.publish_backlinks import main

    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        published_url="https://blogger.example.com/p0",
    )
    mock_vp.return_value = VerificationResult(ok=False, reason="link not found")

    old_stdin, old_out, old_err = sys.stdin, sys.stdout, sys.stderr
    sys.stdin = StringIO(json.dumps(_full_payload()))
    sys.stdout, sys.stderr = StringIO(), StringIO()
    code = 0
    try:
        try:
            main(["--mode", "draft", "--platform", "blogger"])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_out, old_err

    kinds = _kinds()
    assert "publish.unverified" in kinds       # projected as unverified
    assert "publish.confirmed" not in kinds     # NOT counted as a success
    assert code == 5                            # CLI signals unverified


@patch("backlink_publisher.cli._resume.verify_adapter_setup")
def test_resume_noop_reemits_unverified_suffix_and_projects(mock_setup):
    """A no-op resume (all items already done) re-emits a prior-resume
    unverified item WITH the _unverified suffix (from the persisted `verified`
    flag, not just this run's transient set) AND projects it (R2 recovery)."""
    import sys
    from io import StringIO
    from backlink_publisher.cli.publish_backlinks import main

    run_id = _seed_done_run(verified=False)  # one done item, verified=False

    old_stdin, old_out, old_err = sys.stdin, sys.stdout, sys.stderr
    sys.stdin = StringIO("")
    sys.stdout, sys.stderr = StringIO(), StringIO()
    out = sys.stdout
    try:
        try:
            main(["--resume", run_id])
        except SystemExit:
            pass
        stdout_val = out.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_out, old_err

    # Output JSONL marks the prior-resume done item as unverified...
    rows = [json.loads(line) for line in stdout_val.splitlines() if line.strip()]
    assert rows and all(r["status"].endswith("_unverified") for r in rows)
    # ...and the no-op resume still projected it (recovery path).
    assert "publish.unverified" in _kinds()
