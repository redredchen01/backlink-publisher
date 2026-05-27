"""Contract tests for the Phase-1 cookie-authenticated adapters (P1#12).

The six Chinese-platform adapters share an identical credential shape
(``<name>-credentials.json`` = ``{"cookies": [...]}`` at 0600) and an
identical availability/auth-failure contract, so the AGENTS.md-required
trio (availability gate, DependencyError when unconfigured,
ExternalServiceError on auth rejection) is parametrized here.

Happy-path response parsing differs per platform and is intentionally
not covered here — these tests lock the availability + failure contract,
which is what protects a multi-channel batch from a misconfigured channel.
"""
from __future__ import annotations

import importlib
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError

# (slug, adapter class name)
COOKIE_ADAPTERS = [
    ("juejin", "JuejinAPIAdapter"),
    ("csdn", "CSDNAPIAdapter"),
    ("note", "NoteAPIAdapter"),
]


def _adapter(slug: str, cls_name: str):
    mod = importlib.import_module(f"backlink_publisher.publishing.adapters.{slug}_api")
    return getattr(mod, cls_name)


def _config(tmp_path):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    return cfg


def _write_creds(tmp_path, slug: str) -> None:
    path = tmp_path / f"{slug}-credentials.json"
    path.write_text(json.dumps({"cookies": [
        {"name": "session", "value": "tok"},
        {"name": "csrf_token", "value": "c"},
    ]}))
    os.chmod(path, 0o600)


def _payload():
    # Both content_html and content_markdown so extract_publish_html yields
    # non-empty content regardless of the platform's route tier (csdn guards
    # against empty content before the POST).
    return {"id": "a1", "title": "T", "content_html": "<p>hi</p>",
            "content_markdown": "hi there", "tags": []}


@pytest.mark.parametrize("slug,cls_name", COOKIE_ADAPTERS)
def test_available_false_without_creds(slug, cls_name, tmp_path):
    assert _adapter(slug, cls_name).available(_config(tmp_path)) is False


@pytest.mark.parametrize("slug,cls_name", COOKIE_ADAPTERS)
def test_available_true_with_creds(slug, cls_name, tmp_path):
    _write_creds(tmp_path, slug)
    assert _adapter(slug, cls_name).available(_config(tmp_path)) is True


@pytest.mark.parametrize("slug,cls_name", COOKIE_ADAPTERS)
def test_publish_without_creds_raises_dependency_error(slug, cls_name, tmp_path):
    with pytest.raises(DependencyError):
        _adapter(slug, cls_name)().publish(_payload(), "publish", _config(tmp_path))


@pytest.mark.parametrize("slug,cls_name", COOKIE_ADAPTERS)
def test_publish_401_raises_external_service_error(slug, cls_name, tmp_path):
    _write_creds(tmp_path, slug)
    resp = MagicMock()
    resp.status_code = 401
    resp.text = "unauthorized"
    with patch(
        f"backlink_publisher.publishing.adapters.{slug}_api.requests.post",
        return_value=resp,
    ):
        with pytest.raises(ExternalServiceError) as exc:
            _adapter(slug, cls_name)().publish(_payload(), "publish", _config(tmp_path))
    # f-prefix regression: the status code must be interpolated, not literal.
    assert "401" in str(exc.value)
    assert "{resp.status_code}" not in str(exc.value)
