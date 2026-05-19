"""create_app() startup-hook wiring — Plan 2026-05-19-001 Unit 4 / R10.

Asserts that:
  - ``reconcile_on_load()`` is called when ``start_scheduler=True``
  - ``reap_orphans()`` is called when ``start_scheduler=True``
  - Neither is called when ``start_scheduler=False`` (pytest default)
  - A raised exception in either hook does NOT crash ``create_app``
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path):
    fake_config_dir = tmp_path / "config"
    with patch(
        "backlink_publisher.config._config_dir", return_value=fake_config_dir,
    ):
        yield fake_config_dir


@pytest.fixture(autouse=True)
def _no_scheduler_start():
    """Even when start_scheduler=True, prevent APScheduler from really
    starting (we only care about the new hooks firing)."""
    with patch("webui_app.scheduler._scheduler") as fake_sched, \
         patch("webui_app.scheduler._restore_scheduled_jobs"):
        fake_sched.running = True  # short-circuit the `if not running: start()`
        yield


class TestReconcileWiring:
    def test_reconcile_on_load_called_when_scheduler_starts(self):
        with patch("webui_store.channel_status.reconcile_on_load") as mock_reconcile, \
             patch("webui_app.services.bind_job.reap_orphans") as mock_reap:
            from webui_app import create_app
            create_app(start_scheduler=True)
            assert mock_reconcile.call_count == 1
            assert mock_reap.call_count == 1

    def test_neither_called_when_scheduler_disabled(self):
        with patch("webui_store.channel_status.reconcile_on_load") as mock_reconcile, \
             patch("webui_app.services.bind_job.reap_orphans") as mock_reap:
            from webui_app import create_app
            create_app(start_scheduler=False)
            assert mock_reconcile.call_count == 0
            assert mock_reap.call_count == 0

    def test_reconcile_failure_does_not_crash_create_app(self):
        with patch(
            "webui_store.channel_status.reconcile_on_load",
            side_effect=OSError("disk full"),
        ), patch("webui_app.services.bind_job.reap_orphans"):
            from webui_app import create_app
            # Must return an app, not propagate the exception
            app = create_app(start_scheduler=True)
            assert app is not None

    def test_reap_failure_does_not_crash_create_app(self):
        with patch("webui_store.channel_status.reconcile_on_load"), \
             patch(
                "webui_app.services.bind_job.reap_orphans",
                side_effect=RuntimeError("registry corrupt"),
             ):
            from webui_app import create_app
            app = create_app(start_scheduler=True)
            assert app is not None
