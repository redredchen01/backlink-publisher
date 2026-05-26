"""CNBlogs adapter tests — P1#9 available() regression.

Without an available() override the adapter reaches publish() with no
credentials and raises DependencyError, which aborts the whole batch
(exit 3) instead of skipping the channel.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

from backlink_publisher.publishing.adapters.cnblogs_api import (
    CNBlogsAPIAdapter,
    _CRED_FILENAME,
)


def _config(tmp_path):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    return cfg


def test_available_false_without_credentials(tmp_path):
    assert CNBlogsAPIAdapter.available(_config(tmp_path)) is False


def test_available_true_with_credentials(tmp_path):
    path = tmp_path / _CRED_FILENAME
    path.write_text(json.dumps({"username": "u", "password": "p"}))
    os.chmod(path, 0o600)
    assert CNBlogsAPIAdapter.available(_config(tmp_path)) is True
