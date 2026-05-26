"""Plan 2026-05-26-002 Unit 3 (slice) — auth-type-aware overview rendering.

The 渠道綁定總覽 macro now reads ``status.auth_type``:
  * ANON channels render a "免綁定 · 就緒" badge (no bound/unbound);
  * the "Configure ↓" anchor renders only for channels that actually have a
    per-channel partial below — cardless channels no longer dangle on a dead
    ``#channel-<name>`` link (the core defect);
  * mastodon renders a non-actionable deferred stub.

Full inline binding forms for cardless channels land with the Unit 4 save
route.
"""

from __future__ import annotations

import pytest

from backlink_publisher.config import Config
from webui_app import create_app
from webui_app.binding_status import get_channel_status


@pytest.fixture
def body():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client().get("/settings").get_data(as_text=True)


# ── status dict now carries auth_type ─────────────────────────────────────────


def test_get_channel_status_includes_auth_type():
    cfg = Config()
    assert get_channel_status("txtfyi", cfg)["auth_type"] == "anon"
    assert get_channel_status("csdn", cfg)["auth_type"] == "paste_blob"
    assert get_channel_status("blogger", cfg)["auth_type"] == "oauth"


# ── ANON ready badge ──────────────────────────────────────────────────────────


def test_anon_channel_renders_ready_badge(body):
    assert "免綁定 · 就緒" in body


# ── dead-anchor elimination ───────────────────────────────────────────────────


@pytest.mark.parametrize("cardless", ["csdn", "zhihu", "txtfyi", "rentry", "ghost", "tumblr"])
def test_cardless_channel_has_no_configure_anchor(body, cardless):
    """Cardless channels must not emit a Configure ↓ anchor to a non-existent
    #channel-<name> target."""
    assert f'href="#channel-{cardless}"' not in body


@pytest.mark.parametrize("carded", ["blogger", "medium", "velog", "ghpages", "devto", "notion"])
def test_carded_channel_keeps_configure_anchor(body, carded):
    """The 6 channels with a per-channel partial keep their Configure ↓."""
    assert f'href="#channel-{carded}"' in body


# ── mastodon deferred stub ────────────────────────────────────────────────────


def test_mastodon_renders_deferred_stub_no_dead_anchor(body):
    assert "即將支持" in body
    assert 'href="#channel-mastodon"' not in body
