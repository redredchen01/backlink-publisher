"""plan-gap CLI verb. Each test gets a fresh config/cache dir. The registry is
populated by importing the verb (its module-level adapter import), so these
tests exercise the REAL active-dofollow roster (integration)."""

import io
import json
import sys

import pytest

from backlink_publisher.cli.plan_gap import main
from backlink_publisher.gap.engine import active_dofollow_platforms

AD = active_dofollow_platforms()  # real registry set (>=1 dofollow platform)
SEED_FIELDS = {"target_url", "platform", "main_domain", "language", "url_mode", "publish_mode"}


@pytest.fixture(autouse=True)
def fresh_dirs(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cache = tmp_path / "cache"
    cfg.mkdir()
    cache.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(cache))


def _run(argv, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdout, sys.stderr, sys.stdin
    sys.stdout, sys.stderr, sys.stdin = out, err, io.StringIO(stdin)
    code = 0
    try:
        main(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            code = exc.code
        elif exc.code is None:
            code = 0
        else:
            err.write(str(exc.code))
            code = 1
    finally:
        sys.stdout, sys.stderr, sys.stdin = saved
    return out.getvalue(), err.getvalue(), code


def _ledger_line(target="https://t.com/p", live_dofollow=0, liveness="live",
                 live_dofollow_platforms=None, verified_at="2026-05-29T00:00:00"):
    return json.dumps({
        "target_url": target,
        "live_dofollow": live_dofollow,
        "liveness": liveness,
        "live_dofollow_platforms": list(live_dofollow_platforms or []),
        "liveness_verified_at": verified_at,
    }) + "\n"


def _seeds(out):
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_happy_path_emits_schema_valid_seeds():
    out, err, code = _run(["--desired", "2", "--language", "zh-CN"], stdin=_ledger_line(live_dofollow=0))
    assert code == 0
    seeds = _seeds(out)
    assert len(seeds) == 2
    for s in seeds:
        assert set(s) == SEED_FIELDS
        assert s["target_url"] == "https://t.com/p"
        assert s["platform"] in AD
        assert s["language"] == "zh-CN"
    # distinct platforms (the P0 distinctness contract)
    assert len({s["platform"] for s in seeds}) == 2
    assert "plan-gap:" in err  # RECON/banner on stderr


def test_stdout_is_pure_jsonl_banner_only_on_stderr():
    out, err, _ = _run(["--desired", "1", "--language", "zh-CN"], stdin=_ledger_line())
    for line in out.splitlines():
        if line.strip():
            json.loads(line)  # parses cleanly
    assert "plan-gap:" not in out  # no human text on stdout
    assert "liveness as-of" in err


def test_missing_desired_is_usage_error():
    out, err, code = _run(["--language", "zh-CN"], stdin=_ledger_line())
    assert code == 1
    assert "--desired is required" in err
    assert out == ""


def test_missing_language_is_usage_error():
    _, err, code = _run(["--desired", "2"], stdin=_ledger_line())
    assert code == 1
    assert "--language is required" in err


def test_bad_language_is_usage_error():
    _, err, code = _run(["--desired", "2", "--language", "xx"], stdin=_ledger_line())
    assert code == 1
    assert "--language must be one of" in err


def test_negative_desired_is_usage_error():
    _, err, code = _run(["--desired", "-1", "--language", "zh-CN"], stdin=_ledger_line())
    assert code == 1
    assert "non-negative" in err


def test_malformed_stdin_exits_2():
    _, _, code = _run(["--desired", "2", "--language", "zh-CN"], stdin="this is not json\n")
    assert code == 2


def test_empty_stdin_is_advisory_exit_0():
    out, err, code = _run(["--desired", "2", "--language", "zh-CN"], stdin="")
    assert code == 0
    assert out == ""
    assert "empty ledger input" in err


def test_all_satisfied_is_advisory_exit_0():
    out, err, code = _run(["--desired", "1", "--language", "zh-CN"], stdin=_ledger_line(live_dofollow=5))
    assert code == 0
    assert _seeds(out) == []
    assert "0 seeds emitted" in err


def test_stale_suppressed_by_default_emitted_with_flag():
    line = _ledger_line(liveness="stale", live_dofollow=0)
    out_no, err_no, _ = _run(["--desired", "2", "--language", "zh-CN"], stdin=line)
    assert _seeds(out_no) == [] and "stale=1" in err_no
    out_yes, _, _ = _run(["--desired", "2", "--language", "zh-CN", "--emit-stale"], stdin=line)
    assert len(_seeds(out_yes)) == 2


def test_channel_exhausted_named_on_stderr():
    line = _ledger_line(target="https://t.com/maxed", live_dofollow=len(AD),
                        live_dofollow_platforms=AD)
    out, err, code = _run(["--desired", str(len(AD) + 2), "--language", "zh-CN"], stdin=line)
    assert code == 0
    assert _seeds(out) == []
    assert "channel_exhausted" in err
    assert "https://t.com/maxed" in err


def test_unknown_liveness_failsafe_no_crash():
    out, err, code = _run(["--desired", "2", "--language", "zh-CN"],
                          stdin=_ledger_line(liveness="pending"))
    assert code == 0
    assert _seeds(out) == []
    assert "unknown_liveness=1" in err
