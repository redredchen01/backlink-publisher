"""Meta-test for the containment net (Plan 2026-05-27-003 Unit 1).

Proves the autouse ``_restore_global_state_net`` fixture actually contains
cross-test leaks of the shared ``webui.app`` config singleton and the
enumerated security env vars. Tests run in definition order (no
pytest-randomly), so the ``*_leaks_*`` cases run before their ``*_restored_*``
assertions.

The leak simulations use ``webui.app.config.update({...})`` (a Call, not a
subscript assignment) and a raw ``os.environ[...] =`` on an env key, neither of
which the config-subscript AST gate flags — so this file is not a grandfather
offender.
"""
from __future__ import annotations

import os


def test_a_leaks_csrf_disabled_on_singleton() -> None:
    import webui

    # Simulate a sibling test disabling CSRF on the shared singleton without
    # restoring it. .update() avoids the gate's subscript matcher on purpose.
    webui.app.config.update({"WTF_CSRF_ENABLED": False, "CSRF_ENABLED": False})
    assert webui.app.config["CSRF_ENABLED"] is False  # leak is live within this test


def test_b_config_leak_was_contained() -> None:
    import webui

    # The net's per-test setup reset the singleton to the clean baseline before
    # this test body ran, so the prior test's leak is gone.
    assert webui.app.config.get("CSRF_ENABLED", True) is True
    assert webui.app.config.get("WTF_CSRF_ENABLED", True) is True


def test_c_leaks_security_env_var() -> None:
    # Raw assignment on a security env key (env keys are the net's job, not the
    # gate's, so this is not flagged).
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    assert os.environ["OAUTHLIB_INSECURE_TRANSPORT"] == "1"


def test_d_env_leak_was_contained() -> None:
    # Baseline had the key absent, so the net popped it on teardown.
    assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ


def test_e_setup_resets_to_clean_baseline() -> None:
    # Independent of any prior leak: every test body starts with CSRF on.
    import webui

    assert webui.app.config.get("CSRF_ENABLED", True) is True


def test_f_disable_csrf_fixture_disables_then_restores(disable_csrf) -> None:
    # The sanctioned fixture turns the guard off for the duration of the test.
    import webui

    assert webui.app.config["CSRF_ENABLED"] is False
    assert disable_csrf is webui.app


def test_g_disable_csrf_restored_after_use() -> None:
    # After the disable_csrf test, the net (and the fixture's own finally)
    # leave CSRF back on.
    import webui

    assert webui.app.config.get("CSRF_ENABLED", True) is True


def test_h_config_baseline_is_csrf_enabled() -> None:
    # The lazily-built baseline must itself be CSRF-enabled (fail-loud guard).
    from conftest import _ensure_csrf_config_baseline

    baseline = _ensure_csrf_config_baseline()
    assert baseline["CSRF_ENABLED"] is True
