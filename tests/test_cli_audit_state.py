"""Unit 3 — audit-state CLI verb. Fresh config/cache dir per test; mirrors the
equity-ledger harness. Asserts JSONL stdout, stderr summary + remediation, and
the R9 exit-code distinctions (absent→0, findings→0, unreadable→3, bad flag→1).
"""

import io
import json
import sys

import pytest

from backlink_publisher.audit import readers
from backlink_publisher.cli.audit_state import main
from backlink_publisher.config import _config_dir
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
            err.write(str(exc.code))
            code = 1
    finally:
        sys.stdout, sys.stderr = saved
    return out.getvalue(), err.getvalue(), code


def _write_history(rows):
    (_config_dir() / "publish-history.json").write_text(json.dumps(rows))


def _seed_divergent():
    # null_url_orphan
    EventStore().add_article({"target_urls_json": "[]", "live_url": None})
    # article_orphan (no history for it)
    EventStore().add_article(
        {"target_urls_json": "[]", "live_url": "https://medium.com/only-article"}
    )
    # history_orphan (published URL with no article)
    _write_history(
        [{"id": "h1", "status": "published",
          "article_urls": ["https://substack.com/only-history"]}]
    )


def test_happy_path_emits_jsonl_exit_0():
    _seed_divergent()
    out, err, code = _run([])
    assert code == 0
    records = [json.loads(l) for l in out.splitlines() if l.strip()]
    classes = sorted(r["class"] for r in records)
    assert classes == ["article_orphan", "history_orphan", "null_url_orphan"]
    assert all("authority" in r and "source_tier" in r for r in records)


def test_clean_store_no_divergence_exit_0():
    EventStore().add_article(
        {"target_urls_json": "[]", "live_url": "https://medium.com/p1"}
    )
    _write_history(
        [{"id": "h1", "status": "published", "article_urls": ["https://medium.com/p1"]}]
    )
    out, err, code = _run([])
    assert code == 0
    assert out.strip() == ""
    assert "no divergence" in err.lower()


def test_nothing_to_audit_exit_0():
    out, err, code = _run([])
    assert code == 0
    assert "nothing" in err.lower() or "no stores" in err.lower()


def test_unreadable_store_exits_3(monkeypatch):
    EventStore().add_article(
        {"target_urls_json": "[]", "live_url": "https://medium.com/p1"}
    )
    monkeypatch.setattr(
        readers.shutil,
        "copy2",
        lambda *a, **k: (_ for _ in ()).throw(OSError("locked")),
    )
    out, err, code = _run([])
    assert code == 3
    assert out.strip() == ""


def test_summary_separates_counts_and_shows_remediation():
    _seed_divergent()
    _out, err, _code = _run([])
    assert "high-signal" in err
    # R12 remediation hints present for each reported class.
    assert "re-run publish" in err
    assert "verify the live URL" in err


def test_bad_format_exits_1():
    out, err, code = _run(["--format", "xml"])
    assert code == 1
    assert "format" in err.lower()
    assert out.strip() == ""
