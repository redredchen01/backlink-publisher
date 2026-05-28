"""Permanent environment invariant tests.

These assertions run as a pre-campaign gate for plan 2026-05-28-006
(direct-deps upgrade). They verify structural invariants that must hold
across every subsequent wave (W1–W4). Fail here = don't proceed.
"""

from __future__ import annotations

import inspect
import os
import socket

import pytest


def test_pythonhashseed_is_zero():
    """pytest-env must inject PYTHONHASHSEED=0 into the test process.

    The footprint regression gate (test_footprint_regression.py) relies on
    deterministic dict ordering. If pytest-env is bumped and the injection
    silently breaks, this test catches it immediately rather than letting the
    footprint baseline silently corrupt.
    """
    assert os.environ.get("PYTHONHASHSEED") == "0", (
        "PYTHONHASHSEED must be '0' — pytest-env injection may be broken. "
        "Check [tool.pytest.ini_options] env = ['PYTHONHASHSEED=0'] in pyproject.toml."
    )


def test_socket_block_is_armed():
    """The conftest autouse _disable_real_network fixture must be active.

    The fixture calls disable_socket(allow_unix_socket=True), blocking real
    inet connections. If a pytest-socket bump changes the API and the fixture
    stops firing, any missed mock would silently hit the network — this test
    catches that regression immediately.
    """
    try:
        from pytest_socket import SocketBlockedError
    except ImportError:
        pytest.skip("pytest-socket not installed — dev dep may be missing")

    with pytest.raises(SocketBlockedError):
        # AF_INET socket creation itself raises when disable_socket is armed.
        # connect() is listed as a fallback but should never be reached.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("8.8.8.8", 53))


def test_conftest_autouse_fixtures_are_sync(request: pytest.FixtureRequest):
    """All autouse conftest fixtures must be sync def, not async def.

    Under pytest 8, an async autouse fixture without explicit asyncio_mode
    configuration raises a DeprecationWarning (or error when
    filterwarnings=error is set). This test fails loudly if anyone converts
    a sync autouse fixture to async without also updating the asyncio
    configuration, so the problem surfaces at wave-bump time rather than as
    a cryptic suite-wide failure.
    """
    fm = request.session._fixturemanager

    # Collect all autouse fixture names across all conftest scopes.
    # _nodeid_autousenames maps conftest-nodeid -> list[fixture_name]; the ""
    # key holds session/plugin-level autouse names. This is the canonical
    # source in pytest 8.x (FixtureDef._autouse is a deprecated shim there).
    autouse_names: set[str] = set()
    for names in fm._nodeid_autousenames.values():
        autouse_names.update(names)

    async_autouse = [
        name
        for name in autouse_names
        for fdef in fm._arg2fixturedefs.get(name, [])
        if inspect.iscoroutinefunction(fdef.func)
    ]
    assert not async_autouse, (
        f"Autouse fixtures must be sync def, not async def. "
        f"Found async autouse fixtures: {async_autouse}. "
        f"Convert them back to sync or add asyncio_mode configuration before proceeding."
    )
