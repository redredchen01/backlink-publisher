"""Tests for the /ce:health route + template (Plan 2026-05-25-006 / U3).

GET-only read-only dashboard. Covers: honest empty states (R10), seeded hero +
per-adapter render, broken-channels banner with bind link + placement (R9/R12),
gap/degraded notice (R5), aggregation-error-degrades-not-500 (R5), and the
/ce:dashboard → /ce:health redirect. Uses the live CSRF guard (create_app) to
prove a GET needs no token.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backlink_publisher.events import EventStore, kinds
from backlink_publisher.events.reconcile import ReadProjectionResult


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    from webui_app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_events(rows: list[dict]) -> None:
    store = EventStore()
    for r in rows:
        payload = {"platform": r.get("platform", "medium")}
        if "error_class" in r:
            payload["error_class"] = r["error_class"]
        # Satisfy the kind's R9 required-field floor with placeholders so the
        # seeded events take the real insert path instead of being quarantined.
        for field in kinds.REQUIRED_FIELDS.get(r["kind"], frozenset()):
            payload.setdefault(field, f"_test_{field}")
        store.append(
            r["kind"],
            payload,
            target_url=r.get("target_url"),
            ts_utc=r.get("ts_utc", _now()),
        )


# ── Empty / honest states (R10) ──────────────────────────────────────────────


def test_empty_dashboard_renders_200_without_zero_percent(client):
    resp = client.get("/ce:health")
    assert resp.status_code == 200  # GET under the live CSRF guard, no token
    html = resp.get_data(as_text=True)
    assert "Publishing Health" in html
    assert "No publishes in the last 30 days" in html
    assert "No known channel problems" in html
    # "no data" must not masquerade as 0% / NaN.
    assert "0%" not in html
    assert "NaN" not in html


def test_no_known_channel_problems_states_its_scope(client):
    # R9 honesty: the empty banner names what it actually monitors.
    html = client.get("/ce:health").get_data(as_text=True)
    assert "velog / medium / blogger" in html


# ── Seeded render ────────────────────────────────────────────────────────────


def test_seeded_dashboard_shows_hero_and_adapter_rows(client):
    _seed_events([
        {"kind": "publish.confirmed", "target_url": "https://a.com", "platform": "medium"},
        {"kind": "publish.failed", "target_url": "https://b.com", "platform": "velog",
         "error_class": "auth_expired"},
    ])
    html = client.get("/ce:health").get_data(as_text=True)

    assert "50.0%" in html  # 1 confirmed of 2 targets
    assert "medium" in html
    assert "velog" in html
    assert "auth_expired" in html  # error distribution bucket


def test_unverified_publish_does_not_inflate_hero(client):
    # D5 end-to-end: an unverified done is in the denominator, not a success.
    _seed_events([
        {"kind": "publish.confirmed", "target_url": "https://a.com"},
        {"kind": "publish.unverified", "target_url": "https://b.com"},
    ])
    html = client.get("/ce:health").get_data(as_text=True)
    assert "50.0%" in html  # 1 of 2, not 100%


# ── Broken-channels banner (R9) + placement (R12) ────────────────────────────


def test_broken_channel_banner_with_bind_link(client, monkeypatch):
    monkeypatch.setattr(
        "webui_store.channel_status.list_all",
        lambda: {"medium": {"status": "expired", "last_verified_at": None}},
    )
    html = client.get("/ce:health").get_data(as_text=True)

    assert "Channels needing attention" in html
    assert "medium" in html
    assert 'href="/settings"' in html  # bind link points at the real settings URL


def test_broken_banner_rendered_above_per_adapter_table(client, monkeypatch):
    monkeypatch.setattr(
        "webui_store.channel_status.list_all",
        lambda: {"blogger": {"status": "identity_mismatch", "last_verified_at": None}},
    )
    html = client.get("/ce:health").get_data(as_text=True)

    # R12 placement: broken banner precedes the per-platform table.
    assert html.index("Channels needing attention") < html.index("Per-platform health")


# ── Degraded / gap notice (R5) ───────────────────────────────────────────────


def test_degraded_projection_shows_incomplete_notice(client, monkeypatch):
    monkeypatch.setattr(
        "webui_app.services.health_projection.project_on_read",
        lambda: ReadProjectionResult(degraded=True, degraded_reason="locked"),
    )
    html = client.get("/ce:health").get_data(as_text=True)
    assert "Data may be incomplete" in html


def test_gap_projection_shows_incomplete_notice(client, monkeypatch):
    monkeypatch.setattr(
        "webui_app.services.health_projection.project_on_read",
        lambda: ReadProjectionResult(gap=True, gap_reason="1 source(s) could not be projected"),
    )
    html = client.get("/ce:health").get_data(as_text=True)
    assert "Data may be incomplete" in html
    assert "could not be projected" in html


def test_aggregation_error_degrades_not_500(client, monkeypatch):
    def _boom():
        raise RuntimeError("db exploded")

    monkeypatch.setattr("webui_app.health_metrics.build_health", _boom)
    resp = client.get("/ce:health")
    assert resp.status_code == 200  # R5: degrade, do not 500
    assert "Data may be incomplete" in resp.get_data(as_text=True)


def test_render_failure_serves_fallback_not_500(client, monkeypatch):
    # R5: even a template/context-rendering error must not 500 the page.
    def _boom(*_a, **_k):
        raise RuntimeError("template exploded")

    monkeypatch.setattr("webui_app.routes.health._render", _boom)
    resp = client.get("/ce:health")
    assert resp.status_code == 200
    assert "temporarily unavailable" in resp.get_data(as_text=True)


# ── Redirect ─────────────────────────────────────────────────────────────────


def test_dashboard_redirects_to_health(client):
    resp = client.get("/ce:dashboard")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/ce:health")
