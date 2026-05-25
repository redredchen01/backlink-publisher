"""Unit 4 — equity-ledger CLI verb. Each test gets a fresh config/cache dir so
the session-scoped sandbox doesn't bleed seeded state across tests."""

import io
import json
import sys

import pytest

from backlink_publisher.cli.equity_ledger import main
from backlink_publisher.events import EventStore


@pytest.fixture(autouse=True)
def fresh_dirs(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cache = tmp_path / "cache"
    cfg.mkdir()
    cache.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(cache))


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    code = 0
    try:
        main(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            code = exc.code
        elif exc.code is None:
            code = 0
        else:
            err.write(str(exc.code))  # interpreter prints a str SystemExit to stderr
            code = 1
    finally:
        sys.stdout, sys.stderr = saved
    return out.getvalue(), err.getvalue(), code


def _seed():
    EventStore().add_article({
        "target_urls_json": json.dumps(["https://site.com/p"]),
        "live_url": "https://medium.com/post1",
    })
    from webui_store import history_store
    history_store.save([{
        "id": "h1", "platform": "medium", "target_url": "https://site.com/p",
        "article_urls": ["https://medium.com/post1"], "status": "published",
    }])


def test_happy_path_emits_jsonl_exit_0():
    _seed()
    out, err, code = _run([])
    assert code == 0
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])  # stdout is pure JSONL — parses cleanly
    assert row["target_url"] == "https://site.com/p"
    assert row["total_links"] == 1
    assert row["dofollow"]["dofollow"] == 1


def test_empty_stores_zero_rows_exit_0():
    out, _, code = _run([])
    assert code == 0
    assert [l for l in out.splitlines() if l.strip()] == []


def test_bad_stale_days_exits_1():
    _, err, code = _run(["--stale-days", "-5"])
    assert code == 1  # UsageError-style, not argparse's exit 2
    assert "stale-days" in err


def test_stale_days_flag_accepted():
    _seed()
    _, _, code = _run(["--stale-days", "7"])
    assert code == 0
