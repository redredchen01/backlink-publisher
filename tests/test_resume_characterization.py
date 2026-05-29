"""Characterization tests pinning _run_resume's load-bearing invariants before decomposition.

These lock the state-lifecycle invariants the plan's System-Wide Impact section identifies as
silently-breakable by a behavior-preserving extraction (plan-005 Unit 2 -> Unit 3). They assert
OBSERVABLE behavior (which dedup terminal is written, call ordering, exit codes) so the
decomposition in Unit 3 is provably behavior-preserving: these must stay green before and after.

See docs/plans/2026-05-29-005-feat-cyclomatic-complexity-budget-plan.md.

These complement (do not duplicate) the existing resume coverage:
  - tests/test_publish_backlinks_resume.py            -- happy paths, throttle, 5xx, exit 4
  - tests/test_publish_backlinks_resume_revalidate.py -- phase-2 retro reclassification
  - tests/test_token_revocation_midrun.py             -- drift aborts with exit 3 (+ call_count)
The four [P0] hazards below were net-new (no prior test pinned them).
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

from backlink_publisher.cli.publish_backlinks import _run_resume
from backlink_publisher.publishing.adapters import AdapterResult


class _Args:
    """Minimal stand-in for the argparse Namespace _run_resume reads."""

    def __init__(self, *, no_verify: bool = True) -> None:
        self.resume = "20260101T000000Z-deadbeef"
        self.dry_run = False
        self.no_verify = no_verify
        self.skip_publish_time_check = True


def _stateful_update_item(ckpt: dict):
    """Fake checkpoint.update_item that mutates the shared ckpt dict in place.

    Mirrors real checkpoint semantics so finalize (which RELOADS the checkpoint) sees the
    statuses the loop wrote -- essential for faithful exit-code (0/4/5) assertions.
    """

    def _update(run_id, item_id, status, **kw):
        for it in ckpt["items"]:
            if it["id"] == item_id:
                it["status"] = status
                it.update(kw)
        return None

    return _update


@contextlib.contextmanager
def _harness(ckpt: dict, *, fake_publish, do_verify=None, check_token_drift=None):
    """Patch _run_resume's collaborators; yield the spies. gate is stubbed to 'dispatch'
    so the real dedup store is not exercised -- it has its own tests; here we pin the
    publish-loop's terminal/ordering decisions."""
    spies = {
        "record_done": MagicMock(name="record_done"),
        "record_failure": MagicMock(name="record_failure"),
        "gate": MagicMock(name="gate", side_effect=lambda row, platform, **kw: ("dispatch", None)),
        "update_item": MagicMock(name="update_item", side_effect=_stateful_update_item(ckpt)),
        "project_run_safe": MagicMock(name="project_run_safe"),
    }
    patches = [
        patch("backlink_publisher.cli._resume.adapter_publish", side_effect=fake_publish),
        patch("backlink_publisher.cli._resume.verify_adapter_setup"),
        patch("backlink_publisher.cli._resume._acquire_publish_leases"),
        patch("backlink_publisher.cli._dedup_gate.enforce_precondition_or_exit"),
        patch("backlink_publisher.cli._resume.record_done", spies["record_done"]),
        patch("backlink_publisher.cli._resume.record_failure", spies["record_failure"]),
        patch("backlink_publisher.cli._resume.gate", spies["gate"]),
        patch("backlink_publisher.cli.validate_backlinks._enhance_payload",
              side_effect=lambda payload, config: {"validation": {"status": "ok", "errors": []}}),
        patch("backlink_publisher.checkpoint.load_checkpoint", return_value=ckpt),
        patch("backlink_publisher.checkpoint.update_item", spies["update_item"]),
        patch("backlink_publisher.checkpoint.mark_complete"),
        patch("backlink_publisher.events.project_run_safe", spies["project_run_safe"]),
    ]
    if do_verify is not None:
        patches.append(patch("backlink_publisher.cli._resume._do_verify", side_effect=do_verify))
    if check_token_drift is not None:
        spies["check_token_drift"] = MagicMock(name="check_token_drift", side_effect=check_token_drift)
        patches.append(patch("backlink_publisher.cli._resume._check_token_drift", spies["check_token_drift"]))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield spies


def _run_catching_exit(args) -> int | None:
    try:
        _run_resume(args)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    return None


def _one_item_ckpt(item_id: str = "r0", platform: str = "blogger") -> dict:
    return {
        "platform": platform,
        "mode": "draft",
        "items": [
            {"id": item_id, "status": "pending", "platform": platform,
             "payload": {"target_url": "https://x.com/a", "platform": platform}},
        ],
    }


def test_resume_in_band_error_records_failure_not_done(tmp_path, monkeypatch):
    """[P0] A returned (not raised) adapter error must write record_failure, NEVER record_done.

    The dedup terminal is immutable: a `done` row for a post that never landed would
    permanently suppress re-publish under enforce and emit a fabricated success. This is the
    plan's single worst failure mode.
    """
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    ckpt = _one_item_ckpt()

    def fake_publish(*a, **k):
        return AdapterResult(status="error", adapter="blogger-api", platform="blogger", error="boom")

    with _harness(ckpt, fake_publish=fake_publish) as spies:
        _run_catching_exit(_Args())

    spies["record_failure"].assert_called_once()
    assert not spies["record_done"].called, "in-band error must NOT seed a `done` dedup row"
    # checkpoint item was marked failed (not done)
    assert ckpt["items"][0]["status"] == "failed"


def test_resume_unverified_success_marks_unverified_and_exits_5(tmp_path, monkeypatch):
    """[P0] A published-but-unverified item: record_done(verify_ok=False) + checkpoint
    verified=False from ONE verify call, and the run exits 5 (not counted as success)."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    ckpt = _one_item_ckpt()

    def fake_publish(*a, **k):
        return AdapterResult(status="drafted", adapter="blogger-api", platform="blogger",
                             published_url="https://blog/x")

    verify_calls = {"n": 0}

    def fake_verify(no_verify, dry_run, result, row):
        verify_calls["n"] += 1
        return (False, "verification failed")

    with _harness(ckpt, fake_publish=fake_publish, do_verify=fake_verify) as spies:
        code = _run_catching_exit(_Args(no_verify=False))

    assert verify_calls["n"] == 1, "verify must run exactly once per item"
    spies["record_done"].assert_called_once()
    assert spies["record_done"].call_args.kwargs["verify_ok"] is False
    # checkpoint `done` carries verified=False (verify ran BEFORE the checkpoint write)
    assert ckpt["items"][0]["status"] == "done"
    assert ckpt["items"][0].get("verified") is False
    assert code == 5, "unverified success must exit 5, not 0"


def test_resume_token_drift_does_not_claim_dedup_for_aborted_item(tmp_path, monkeypatch):
    """[P0] _check_token_drift runs BEFORE the gate claim, so a mid-run abort leaves no
    stranded `attempting` row for the un-started item. Pinned via: gate is reached for the
    first item only; the second item aborts at the drift check before gate."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    ckpt = {
        "platform": "blogger", "mode": "draft",
        "items": [
            {"id": "r0", "status": "pending", "platform": "blogger",
             "payload": {"target_url": "https://x.com/a", "platform": "blogger"}},
            {"id": "r1", "status": "pending", "platform": "blogger",
             "payload": {"target_url": "https://x.com/b", "platform": "blogger"}},
        ],
    }

    def fake_publish(*a, **k):
        return AdapterResult(status="drafted", adapter="blogger-api", platform="blogger",
                             published_url="https://blog/a")

    drift_calls = {"n": 0}

    def fake_drift(initial_revs):
        drift_calls["n"] += 1
        if drift_calls["n"] == 2:  # second item: simulate a mid-run credential change
            from backlink_publisher._util.errors import emit_error
            emit_error("config drift", exit_code=3)

    with _harness(ckpt, fake_publish=fake_publish, check_token_drift=fake_drift) as spies:
        code = _run_catching_exit(_Args())

    assert code == 3, "mid-run drift aborts with exit 3"
    assert spies["gate"].call_count == 1, (
        "gate must be reached for the first item only -- the drifted item aborts at the "
        "token-drift check BEFORE its gate claim, so no stranded `attempting` row"
    )


def test_resume_all_done_noop_projects_and_exits_0(tmp_path, monkeypatch):
    """[P0/ordering] The no-op resume path (nothing to process) still projects the run before
    exit 0 -- recovers a checkpoint written but never projected (crash-before-projection)."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    ckpt = {
        "platform": "blogger", "mode": "draft",
        "items": [
            {"id": "r0", "status": "done", "platform": "blogger", "verified": True,
             "published_url": "https://blog/a", "completed_at": "2026-01-01T00:00:00+00:00",
             "payload": {"target_url": "https://x.com/a", "platform": "blogger"}},
        ],
    }

    def fake_publish(*a, **k):  # must never be called on the no-op path
        raise AssertionError("adapter_publish must not run when nothing is to process")

    with _harness(ckpt, fake_publish=fake_publish) as spies:
        code = _run_catching_exit(_Args())

    assert code == 0
    spies["project_run_safe"].assert_called_once()  # no-op resume still projects before exit 0
