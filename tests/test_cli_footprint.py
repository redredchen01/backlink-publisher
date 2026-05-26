"""CLI tests for ``footprint`` — covers Plan Unit 2 (subparser + regen).

Backwards compat:
- ``cat payloads.jsonl | footprint --json`` and ``footprint --json`` still work.

New ``baseline regenerate`` subcommand:
- Refuses without --reason (argparse-required) or with rubber-stamp reasons.
- Refuses without PYTHONHASHSEED=0.
- Writes per-corpus baselines atomically (no partial files on mid-run failure).
- Idempotent: second regen with same inputs produces byte-identical baseline.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backlink_publisher.cli.footprint import _validate_reason, main
from backlink_publisher.footprint import SCHEMA_VERSION
from backlink_publisher.footprint_corpus import CORPUS_NAMES, compute_fixture_set_id


def test_default_audit_via_stdin_json(monkeypatch, capsys):
    """Happy path: piping a JSONL payload + --json reproduces pre-R11 contract."""
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"content_html": "<p><a href=\\"https://e.com\\" rel=\\"noopener\\">x</a></p>"}\n'),
    )
    main(["--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["total_links"] == 1
    assert parsed["concentration_pct"]["rel_value"] == 100.0


def test_default_audit_markdown_no_subcommand(monkeypatch, capsys):
    """Happy path: no subcommand → markdown summary."""
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"content_html": "<p><a href=\\"https://e.com\\">x</a></p>"}\n'),
    )
    main([])
    out = capsys.readouterr().out
    assert "# Footprint Audit" in out
    assert "Cluster-Key Risk" in out


@pytest.mark.parametrize(
    "bad_reason",
    [
        "regen",
        "REGEN",
        "regenerate",
        "update",
        "fix",
        "bump",
        "wip",
        "fix it",
        "ci was red",
        "nothing",
        "x" * 15,
    ],
)
def test_validate_reason_rejects_rubber_stamp(bad_reason):
    with pytest.raises(SystemExit) as exc_info:
        _validate_reason(bad_reason)
    assert "rubber-stamp" in str(exc_info.value)


@pytest.mark.parametrize(
    "good_reason",
    [
        "Initial Arm A baseline — SCHEMA_VERSION=1, deterministic tie-break",
        "Adjusted anchor pool size after PR #99 anchor work landed",
        "Updated zh_short fixture set to cover edge case from issue #123",
    ],
)
def test_validate_reason_accepts_substantive(good_reason):
    _validate_reason(good_reason)  # Should not raise.


def test_validate_reason_rejects_multi_line_with_banned_first_line():
    """Adversarial finding: `$'regen\\n…'` bypassed regex; now rejected."""
    with pytest.raises(SystemExit, match="single line"):
        _validate_reason("regen\nfollow-up describing the actual change here at length")


def test_validate_reason_rejects_multi_line_even_with_substantive_first_line():
    with pytest.raises(SystemExit, match="single line"):
        _validate_reason("Adjusted anchor pool size after PR #99 anchor work landed\nplus extra detail")


def test_baseline_regen_refuses_without_pythonhashseed_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONHASHSEED", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "baseline",
                "regenerate",
                "--path",
                "work_themed",
                "--reason",
                "Initial Arm A baseline — testing the env guard contract",
                "--output-dir",
                str(tmp_path),
            ]
        )
    msg = str(exc_info.value)
    assert "PYTHONHASHSEED=0" in msg
    assert "footprint baseline regenerate" in msg


def _run_regen_subprocess(
    tmp_path: Path,
    *,
    path: str,
    reason: str,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "backlink_publisher.cli.footprint",
            "baseline",
            "regenerate",
            "--path",
            path,
            "--reason",
            reason,
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_regen_single_corpus_writes_baseline_with_all_fields(tmp_path):
    result = _run_regen_subprocess(
        tmp_path,
        path="work_themed",
        reason="Initial Arm A baseline — testing single-corpus regen flow",
    )
    assert result.returncode == 0, result.stderr
    baseline_file = tmp_path / "footprint_concentration_work_themed.json"
    assert baseline_file.exists()
    rec = json.loads(baseline_file.read_text(encoding="utf-8"))
    assert rec["schema_version"] == SCHEMA_VERSION
    assert rec["fixture_set_id"] == compute_fixture_set_id("work_themed")
    assert "Initial Arm A baseline" in rec["reason"]
    assert set(rec["concentration_pct"].keys()) == {
        "attr_order",
        "rel_value",
        "target_value",
        "preceding_char",
    }
    assert "top_values" in rec
    assert not (tmp_path / "footprint_concentration_zh_short.json").exists()
    assert not (tmp_path / "footprint_concentration_markdown_it.json").exists()


def test_regen_all_writes_three_baselines(tmp_path):
    result = _run_regen_subprocess(
        tmp_path,
        path="all",
        reason="Initial Arm A baseline — SCHEMA_VERSION=1, all corpora",
    )
    assert result.returncode == 0, result.stderr
    for corpus_name in CORPUS_NAMES:
        f = tmp_path / f"footprint_concentration_{corpus_name}.json"
        assert f.exists(), f"missing baseline for {corpus_name}"


def test_regen_idempotent(tmp_path):
    reason = "Initial Arm A baseline — testing idempotence under fixed inputs"
    r1 = _run_regen_subprocess(tmp_path, path="all", reason=reason)
    assert r1.returncode == 0, r1.stderr
    snapshot = {
        name: (tmp_path / f"footprint_concentration_{name}.json").read_text(encoding="utf-8")
        for name in CORPUS_NAMES
    }
    r2 = _run_regen_subprocess(tmp_path, path="all", reason=reason)
    assert r2.returncode == 0, r2.stderr
    for name in CORPUS_NAMES:
        after = (tmp_path / f"footprint_concentration_{name}.json").read_text(encoding="utf-8")
        assert snapshot[name] == after, f"regen not idempotent for {name}"


def test_regen_subprocess_with_bad_reason_fails(tmp_path):
    result = _run_regen_subprocess(tmp_path, path="all", reason="regen")
    assert result.returncode != 0
    assert "rubber-stamp" in result.stderr


def test_regen_subprocess_without_pythonhashseed_fails(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "backlink_publisher.cli.footprint",
            "baseline",
            "regenerate",
            "--path",
            "work_themed",
            "--reason",
            "Initial Arm A baseline — testing the env guard contract",
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "PYTHONHASHSEED=0" in result.stderr


def test_regen_atomicity_no_partial_state_on_failure(tmp_path, monkeypatch):
    """Atomicity test: failure mid-run leaves no on-disk baseline changes."""
    for corpus_name in CORPUS_NAMES:
        (tmp_path / f"footprint_concentration_{corpus_name}.json").write_text('{"stale": true}', encoding="utf-8")

    call_count = {"n": 0}
    from backlink_publisher.cli import _footprint_baseline as fb

    real_make = fb.make_corpus

    def failing_make_corpus(name):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("simulated failure on 2nd corpus")
        return real_make(name)

    monkeypatch.setattr(fb, "make_corpus", failing_make_corpus)
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    with pytest.raises(RuntimeError, match="simulated failure"):
        main(
            [
                "baseline",
                "regenerate",
                "--path",
                "all",
                "--reason",
                "Atomicity test — should not touch any baseline files",
                "--output-dir",
                str(tmp_path),
            ]
        )

    for corpus_name in CORPUS_NAMES:
        content = json.loads(
            (tmp_path / f"footprint_concentration_{corpus_name}.json").read_text(encoding="utf-8")
        )
        assert content == {"stale": True}
    leftover_tmps = list(tmp_path.glob("*.tmp"))
    assert leftover_tmps == []
