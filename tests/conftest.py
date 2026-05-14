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


@pytest.fixture(autouse=True)
def _mock_content_fetch(request, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default-pass the content-fetch gate in every test.

    Plan 2026-05-14-007 Unit 6: the gate fires inside ``_build_links`` at
    plan time. Without this autouse fixture, every existing plan-backlinks
    test would either hit the network (blocked by ``_disable_real_network``)
    or trip the cache, depending on test order.

    Patches at both the producer module (``backlink_publisher.content_fetch``)
    and the consumer reference in ``plan_backlinks`` so tests that import the
    function either way see the mock. Also clears the in-run cache before
    each test so cache state never leaks across scenarios.

    Tests in ``tests/test_content_fetch.py`` exercise the real functions
    against mocked ``urlopen`` — this fixture skips patching for that file
    so its assertions hit the production code path. Other test files that
    want to drive specific gate-failure paths re-patch
    ``backlink_publisher.content_fetch.verify_urls_batch`` within their own
    scope (last-wins monkeypatch semantics).
    """
    # Reset cache state up front so previous tests don't contaminate this one.
    from backlink_publisher import content_fetch as _content_fetch

    _content_fetch.reset_cache()

    # The content_fetch unit tests exercise the real functions against a
    # mocked urlopen and must not see the default-pass mock.
    test_path = str(request.node.fspath)
    if "test_content_fetch.py" in test_path:
        return

    def _ok_batch(urls, max_workers=5):
        return {u: (True, None, "mock title") for u in urls}

    def _ok_single(_url):
        return (True, None, "mock title")

    monkeypatch.setattr(
        "backlink_publisher.content_fetch.verify_urls_batch",
        _ok_batch,
        raising=True,
    )
    monkeypatch.setattr(
        "backlink_publisher.content_fetch.verify_url_has_content",
        _ok_single,
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
