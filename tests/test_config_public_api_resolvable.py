"""Regression guard: every name in ``backlink_publisher.config.__all__``
must resolve at import time.

The bug class this catches:

A package ``__init__.py`` re-exports symbols via ``from .submodule import X``
plus an explicit ``__all__`` listing. If a later commit removes ``X`` from
the submodule but forgets to remove the re-export line, Python raises
``ImportError: cannot import name 'X' from 'pkg.submodule'`` the FIRST
time the package is imported -- typically when a CLI subprocess spawned
by the WebUI tries to load the package, so the operator sees a publish
failure rather than a CI red.

This happened in the wild on 2026-05-21: a config type was deleted
from its module but the star-import still referenced it. Publish
crashed with ``ImportError``.

The test uses a subprocess so cached imports in the test runner can't
mask a stale ``__all__`` entry, and it also asserts the *companion*
invariant exercised by the same incident: importing
``backlink_publisher.publishing.registry`` as the FIRST module in the
publishing package (rather than via ``adapters`` first) must not blow
up with a circular ImportError.

This is intentionally a thin import-time gate, not a behavioural test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _REPO_ROOT / "src"


def _python_subprocess(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_DIR) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_config_all_names_resolve_in_fresh_process() -> None:
    """Every entry in ``backlink_publisher.config.__all__`` must be
    ``getattr``-able after a cold import. Fails fast on the
    "package re-exports a symbol that the submodule has since deleted" bug.
    """
    result = _python_subprocess(
        "import sys; "
        "import backlink_publisher.config as cfg; "
        "missing = [n for n in cfg.__all__ if not hasattr(cfg, n)]; "
        "print('MISSING:', missing) if missing else print('OK'); "
        "sys.exit(1 if missing else 0)"
    )
    assert result.returncode == 0, (
        f"backlink_publisher.config.__all__ contains unresolvable names.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout


def test_publishing_registry_imports_without_adapters_first() -> None:
    """``from backlink_publisher.publishing import registry`` in a fresh
    process must succeed. Guards the circular import between
    ``publishing/registry.py`` and ``publishing/adapters/__init__.py``
    that only manifested when registry was loaded first.
    """
    result = _python_subprocess(
        "from backlink_publisher.publishing import registry; "
        "assert callable(registry.dispatch); "
        "assert callable(registry.register); "
        "assert callable(registry.registered_platforms); "
        "print('OK')"
    )
    assert result.returncode == 0, (
        f"registry-first import failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout


def test_publishing_registry_direct_symbol_imports() -> None:
    """``from backlink_publisher.publishing.registry import dispatch, ...``
    in a fresh process must succeed. Same circular-cycle, slightly
    different import syntax — included because the ``mock.patch`` form
    used in some tests can hit this exact code path.
    """
    result = _python_subprocess(
        "from backlink_publisher.publishing.registry import ("
        "dispatch, register, registered_platforms, Publisher"
        "); print('OK')"
    )
    assert result.returncode == 0, (
        f"direct registry symbol import failed.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout
