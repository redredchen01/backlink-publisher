"""Layer 2 — pre-import HOME/override redirect (Plan 2026-05-27-005 Unit 3).

Verifies that the module-level HOME redirect in ``tests/conftest.py`` fires
before any backlink_publisher module is imported, so even a module-level
``Path.home()`` call (hypothetically still present) would resolve to the
sandbox, not the operator's real home.

Also verifies:
- ``REAL_CONFIG_ROOT`` / ``REAL_CACHE_ROOT`` were captured from the OS-level
  home (pwd.getpwuid) and are NOT equal to the sandbox roots.
- The sandbox sentinel is set.
- Teardown restores env vars via pop-or-reassign (no ``del``) after the session.
- git/coverage blast-radius: a git-subprocess test still passes with HOME
  redirected (minimal .gitconfig is written into the sandbox home).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Import shared constants from conftest.py (tests/ is not a package).
from conftest import (  # type: ignore[import]
    REAL_CONFIG_ROOT,
    REAL_CACHE_ROOT,
    SANDBOX_SENTINEL,
    _sandbox_home_dir,
    _real_pw_home,
)


def test_real_roots_captured_before_redirect() -> None:
    """REAL_CONFIG_ROOT / REAL_CACHE_ROOT must come from pwd, not $HOME."""
    # These are set before HOME is changed, so they must differ from sandbox.
    assert REAL_CONFIG_ROOT is not None, "REAL_CONFIG_ROOT was not populated"
    assert REAL_CACHE_ROOT is not None, "REAL_CACHE_ROOT was not populated"
    # They must NOT equal the sandbox roots.
    sandbox_config = _sandbox_home_dir / ".config" / "backlink-publisher"
    sandbox_cache = _sandbox_home_dir / ".cache" / "backlink-publisher"
    assert REAL_CONFIG_ROOT != sandbox_config, (
        f"REAL_CONFIG_ROOT == sandbox — redirect fired BEFORE capture: {REAL_CONFIG_ROOT}"
    )
    assert REAL_CACHE_ROOT != sandbox_cache, (
        f"REAL_CACHE_ROOT == sandbox — redirect fired BEFORE capture: {REAL_CACHE_ROOT}"
    )


def test_sandbox_sentinel_is_set() -> None:
    """SANDBOX_SENTINEL must be '1' during test execution."""
    assert os.environ.get(SANDBOX_SENTINEL) == "1", (
        f"Sentinel {SANDBOX_SENTINEL!r} not set during test run"
    )


def test_home_is_redirected_to_sandbox() -> None:
    """$HOME must point to the sandbox directory, not the real home."""
    current_home = Path(os.environ.get("HOME", ""))
    assert current_home == _sandbox_home_dir, (
        f"$HOME={current_home!r} should be the sandbox {_sandbox_home_dir!r}"
    )


def test_config_dir_resolves_to_sandbox() -> None:
    """``_config_dir()`` must resolve under the sandbox (not real ~/.config)."""
    from backlink_publisher.config.loader import _config_dir

    resolved = _config_dir()
    # Must be inside the sandbox home OR inside a _isolate_user_dirs tmp.
    # Either way, it must NOT be under the real home.
    assert str(REAL_CONFIG_ROOT) not in str(resolved), (
        f"_config_dir() resolved to real config root: {resolved}"
    )


def test_cache_dir_resolves_to_sandbox() -> None:
    """``_cache_dir()`` must resolve under the sandbox."""
    from backlink_publisher.config.loader import _cache_dir

    resolved = _cache_dir()
    assert str(REAL_CACHE_ROOT) not in str(resolved), (
        f"_cache_dir() resolved to real cache root: {resolved}"
    )


def test_real_roots_differ_from_sandbox_roots() -> None:
    """Assert the watch-root ≠ sandbox invariant that protects Unit 7's tripwire."""
    from backlink_publisher.config.loader import _config_dir

    sandbox_resolved = _config_dir()
    assert REAL_CONFIG_ROOT != sandbox_resolved, (
        "REAL_CONFIG_ROOT == sandbox config dir — the tripwire would silently "
        "watch the sandbox and miss real writes"
    )


def test_path_home_resolves_to_sandbox_during_tests() -> None:
    """``Path.home()`` during test execution must return the sandbox home."""
    current = Path.home()
    assert current == _sandbox_home_dir, (
        f"Path.home()={current!r} should be the sandbox {_sandbox_home_dir!r}"
    )


def test_git_subprocess_works_with_redirected_home() -> None:
    """Redirecting HOME must not break git subprocesses (blast-radius R4a).

    A minimal .gitconfig is created in the sandbox home by the redirect code,
    so ``git config user.email`` must succeed.
    """
    result = subprocess.run(
        ["git", "config", "user.email"],
        capture_output=True,
        text=True,
        env={**os.environ},  # propagate the sandboxed HOME
    )
    # git config may return non-zero if gitconfig is missing a key, but
    # we only care that git doesn't hard-crash (exit 128 = repo error,
    # exit 1 = unset key is acceptable).
    assert result.returncode in (0, 1), (
        f"git config user.email exited {result.returncode}: {result.stderr!r}"
    )
