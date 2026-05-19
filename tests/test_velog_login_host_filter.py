"""Unit tests for velog_login host-filter primitives (R16).

Pure-function tests — no Playwright, no I/O.

Covers:
- _velog_host_allowed: happy path, prefix/suffix confusion, case normalisation
- _filter_velog_cookies: keep / drop, missing domain key
- _filter_velog_storage_state: origins[] IdP isolation (P1 test)
"""

from __future__ import annotations

import pytest

from backlink_publisher.cli.velog_login import (
    _filter_velog_cookies,
    _filter_velog_storage_state,
    _velog_host_allowed,
)


# ── _velog_host_allowed ───────────────────────────────────────────────────────

class TestVelogHostAllowed:
    def test_velog_io_bare(self):
        assert _velog_host_allowed("velog.io") is True

    def test_velog_io_with_dot_prefix(self):
        assert _velog_host_allowed(".velog.io") is True

    def test_velog_io_uppercase(self):
        assert _velog_host_allowed("VELOG.IO") is True

    def test_v2_subdomain(self):
        assert _velog_host_allowed("v2.velog.io") is True

    def test_v3_subdomain(self):
        assert _velog_host_allowed("v3.velog.io") is True

    def test_prefix_confusion_evilvelog(self):
        assert _velog_host_allowed("evilvelog.io") is False

    def test_suffix_confusion_attacker(self):
        assert _velog_host_allowed("velog.io.attacker.com") is False

    def test_google_idp(self):
        assert _velog_host_allowed("accounts.google.com") is False

    def test_github_idp(self):
        assert _velog_host_allowed("github.com") is False

    def test_empty_string(self):
        assert _velog_host_allowed("") is False

    def test_none(self):
        assert _velog_host_allowed(None) is False  # type: ignore[arg-type]


# ── _filter_velog_cookies ─────────────────────────────────────────────────────

class TestFilterVelogCookies:
    def _cookie(self, name: str, domain: str) -> dict:
        return {"name": name, "domain": domain, "value": "x"}

    def test_happy_path_keeps_velog_drops_idp(self):
        raw = [
            self._cookie("access_token", "velog.io"),
            self._cookie("refresh_token", ".velog.io"),
            self._cookie("CONSENT", "accounts.google.com"),
        ]
        result = _filter_velog_cookies(raw)
        assert len(result) == 2
        names = {c["name"] for c in result}
        assert names == {"access_token", "refresh_token"}

    def test_missing_domain_key_dropped(self):
        raw = [{"name": "mystery", "value": "y"}]  # no 'domain' key
        result = _filter_velog_cookies(raw)
        assert result == []

    def test_empty_input(self):
        assert _filter_velog_cookies([]) == []

    def test_non_string_domain_dropped(self):
        raw = [{"name": "x", "domain": 42, "value": "z"}]
        result = _filter_velog_cookies(raw)
        assert result == []


# ── _filter_velog_storage_state ───────────────────────────────────────────────

class TestFilterVelogStorageState:
    def _origin(self, origin_url: str, ls_name: str = "tok") -> dict:
        return {
            "origin": origin_url,
            "localStorage": [{"name": ls_name, "value": "eyJ..."}],
        }

    def test_keeps_velog_drops_google(self):
        """P1 test: Google localStorage (id_token) must NOT bleed through."""
        raw = {
            "cookies": [
                {"name": "access_token", "domain": "velog.io", "value": "at"},
                {"name": "CONSENT", "domain": "accounts.google.com", "value": "YES"},
            ],
            "origins": [
                self._origin("https://velog.io", "app_state"),
                self._origin("https://accounts.google.com", "id_token"),
            ],
        }
        result = _filter_velog_storage_state(raw)

        # cookies
        assert len(result["cookies"]) == 1
        assert result["cookies"][0]["name"] == "access_token"

        # origins
        assert len(result["origins"]) == 1
        assert result["origins"][0]["origin"] == "https://velog.io"
        ls_names = {e["name"] for e in result["origins"][0]["localStorage"]}
        assert "id_token" not in ls_names

    def test_uppercase_velog_origin_kept(self):
        raw = {
            "cookies": [],
            "origins": [self._origin("https://Velog.IO")],
        }
        result = _filter_velog_storage_state(raw)
        assert len(result["origins"]) == 1

    def test_suffix_confusion_origin_dropped(self):
        raw = {
            "cookies": [],
            "origins": [self._origin("https://velog.io.attacker.com")],
        }
        result = _filter_velog_storage_state(raw)
        assert result["origins"] == []

    def test_malformed_origin_url_dropped(self):
        raw = {
            "cookies": [],
            "origins": [{"origin": "not-a-url", "localStorage": []}],
        }
        result = _filter_velog_storage_state(raw)
        assert result["origins"] == []

    def test_empty_state(self):
        result = _filter_velog_storage_state({})
        assert result == {"cookies": [], "origins": []}
