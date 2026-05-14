"""Top-level pytest fixtures.

Plan 2026-05-14-001 Unit 5: prevents new test files from accidentally firing
real HTTP via the new ``publish_backlinks.check_url`` consumer reference.
Existing tests carry per-file autouse mocks (per
``feedback_test-autouse-verify-mock``); this conftest is additive and does
not mass-migrate them.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _mock_publish_check_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``publish_backlinks.check_url`` at the consumer reference.

    Per ``feedback_test-autouse-verify-mock`` + the
    ``ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13``
    solution doc, mocking at the *consumer* module's reference catches calls
    that would otherwise bypass module-level patches.

    Default behavior: every URL is considered reachable. Tests that need to
    drive specific failure paths can re-patch within their own scope.
    """
    monkeypatch.setattr(
        "backlink_publisher.cli.publish_backlinks.check_url",
        lambda _url: (True, None),
        raising=True,
    )


try:
    import pytest_socket  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_SOCKET = False
else:
    _HAS_SOCKET = True


@pytest.fixture(autouse=True)
def _disable_real_network() -> None:
    """Block real network access in tests so missed mocks fail loud.

    If pytest-socket is available we use it as a hard CI safety net (any
    test that bypasses the autouse ``check_url`` patch and tries to open
    a real socket will raise). If pytest-socket is not installed (e.g.,
    dev environment without dev-deps), the fixture is a no-op and the
    ``_mock_publish_check_url`` fixture above is the only line of defense.
    """
    if _HAS_SOCKET:
        from pytest_socket import disable_socket, enable_socket
        disable_socket(allow_unix_socket=True)
        try:
            yield
        finally:
            enable_socket()
    else:
        yield
