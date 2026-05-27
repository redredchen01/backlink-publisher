"""Layer 1 — static AST gate: ban raw operator-state-root primitives.

Any code outside ``config/loader.py`` that *constructs* an operator-state root
path from a raw primitive (``Path.home()``, ``os.environ["HOME"]``,
``~/.config`` / ``~/.cache`` string literals, ``platformdirs.*``,
``appdirs.*``) bypasses the env-aware resolver and silently writes to the
operator's real ``~/.config/backlink-publisher/`` or
``~/.cache/backlink-publisher/`` in tests and CI.

The single source of truth is ``config/loader.py``'s ``_config_dir()`` /
``_cache_dir()`` — every other path construction must call them.

The gate also flags **all** ``.expanduser()`` method calls in scanned source,
except those grandfathered in ``GRANDFATHERED_EXPANDUSER_SITES`` (imported from
``conftest.py``).  The legitimate ones are operator-input expansions — they
expand an env var that may contain ``~``, not a raw ``Path.home()`` result.

Mirror of ``test_cli_exit_code_literals.py`` (collector + parametrize +
recursion-coverage test + mandatory positive-fires anti-no-op test).

Plan 2026-05-27-005 Unit 2.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

# Import shared constants from conftest.py (tests/ is not a package).
from conftest import (  # type: ignore[import]
    _RAW_HOME_ALLOWED_MODULE,
    GRANDFATHERED_EXPANDUSER_SITES,
)

# ── Scan roots ───────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOTS = [
    _REPO_ROOT / "src" / "backlink_publisher",
    _REPO_ROOT / "webui_app",
    _REPO_ROOT / "webui_store",
]

# The resolver module is the ONLY allowed site for raw primitives.
_ALLOWED_MODULE = _REPO_ROOT / _RAW_HOME_ALLOWED_MODULE

# String fragments that indicate a raw operator-state-root path literal.
# These must be used to *construct* a path (caught by the Path-literal detector).
_HOME_PATH_FRAGMENTS = ("~/.config", "~/.cache")

# Known platformdirs/appdirs module attribute names that resolve to home dirs.
_PLATFORMDIRS_ATTRS = frozenset(
    {
        "user_config_dir",
        "user_cache_dir",
        "user_data_dir",
        "user_log_dir",
        "user_documents_dir",
        "site_config_dir",
    }
)


# ── AST helpers ─────────────────────────────────────────────────────────────

class RawHomePrimitive(NamedTuple):
    kind: str    # human-readable kind for error messages
    lineno: int


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _is_attr(node: ast.AST, attr: str) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == attr


def _call_func_name(func: ast.AST) -> str:
    """Best-effort dotted name for a call target, e.g. ``Path.home`` → ``home``."""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_path_home_call(node: ast.Call) -> bool:
    """True when node is ``Path.home()``."""
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "home"
        and _is_name(func.value, "Path")
    )


def _is_os_home_subscript(node: ast.Subscript) -> bool:
    """True when node is ``os.environ["HOME"]`` / ``["APPDATA"]`` etc."""
    keys = {"HOME", "APPDATA", "LOCALAPPDATA"}
    slice_node = node.slice
    # Python 3.9+ wraps the slice value directly; earlier wraps in Index.
    if isinstance(slice_node, ast.Index):  # type: ignore[attr-defined]
        slice_node = slice_node.value  # type: ignore[attr-defined]
    if not (isinstance(slice_node, ast.Constant) and slice_node.value in keys):
        return False
    # Verify the container is os.environ or environ.
    value = node.value
    if _is_attr(value, "environ") and _is_name(value.value, "os"):  # type: ignore[union-attr]
        return True
    if _is_name(value, "environ"):
        return True
    return False


def _is_platformdirs_call(node: ast.Call) -> bool:
    """True when the call is a platformdirs.* or appdirs.* home-resolver."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in _PLATFORMDIRS_ATTRS:
        # platformdirs.user_config_dir(...) or appdirs.user_data_dir(...)
        pkg = func.value
        if isinstance(pkg, ast.Name) and pkg.id in ("platformdirs", "appdirs"):
            return True
        if isinstance(pkg, ast.Attribute) and pkg.attr in ("platformdirs", "appdirs"):
            return True
    if isinstance(func, ast.Name) and func.id in _PLATFORMDIRS_ATTRS:
        return True
    return False


def _is_home_path_string_literal(node: ast.AST) -> bool:
    """True when node is a string constant that starts with a home-path fragment.

    We flag only string literals that are syntactically used to *construct*
    a ``Path`` or join into a path expression — not docstrings or error
    message strings.  The caller is responsible for the context check.
    """
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(node.value.startswith(frag) for frag in _HOME_PATH_FRAGMENTS)
    )


def _is_expanduser_call(node: ast.Call) -> bool:
    """True when node is an ``.expanduser()`` method call anywhere."""
    return isinstance(node.func, ast.Attribute) and node.func.attr == "expanduser"


def _is_inside_string_context(node: ast.AST, parent_map: dict[int, ast.AST]) -> bool:
    """Return True when the node is inside a function-call argument that is
    itself purely a string (i.e., an error-message / logging call).

    We use a simple heuristic: the parent is a Call whose function name is
    not ``Path`` and the string doesn't appear as a BinOp operand with ``/``.
    """
    parent = parent_map.get(id(node))
    if parent is None:
        return False
    # If the parent is a BinOp with Div operator (path joining), it IS
    # path construction.
    if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div):
        return False
    # If the parent is a Call where func is Path or Path(...), it IS
    # path construction.
    if isinstance(parent, ast.Call):
        name = _call_func_name(parent.func)
        if name in ("Path", "join", "expanduser"):
            return False
    # Otherwise it's a string used in some other context (error message etc).
    return True


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Build a child-id → parent mapping for the entire AST."""
    parent_map: dict[int, ast.AST] = {}
    for parent_node in ast.walk(tree):
        for child in ast.iter_child_nodes(parent_node):
            parent_map[id(child)] = parent_node
    return parent_map


def collect_raw_home_primitives(
    tree: ast.AST,
    relpath: str,
) -> tuple[list[RawHomePrimitive], list[tuple[str, int]]]:
    """Walk *tree* and return:
    - ``violations``: list of RawHomePrimitive found (excludes GRANDFATHERED)
    - ``expanduser_sites``: list of (relpath, lineno) for all .expanduser() calls
      (to compare against GRANDFATHERED_EXPANDUSER_SITES)
    """
    parent_map = _build_parent_map(tree)
    violations: list[RawHomePrimitive] = []
    expanduser_sites: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        # 1. Path.home() calls
        if isinstance(node, ast.Call) and _is_path_home_call(node):
            violations.append(RawHomePrimitive("Path.home()", node.lineno))
            continue

        # 2. os.environ["HOME"] / ["APPDATA"] / ["LOCALAPPDATA"] subscripts
        if isinstance(node, ast.Subscript) and _is_os_home_subscript(node):
            violations.append(
                RawHomePrimitive("os.environ[HOME/APPDATA] subscript", node.lineno)
            )
            continue

        # 3. platformdirs.* / appdirs.* home-resolver calls
        if isinstance(node, ast.Call) and _is_platformdirs_call(node):
            violations.append(
                RawHomePrimitive("platformdirs/appdirs home call", node.lineno)
            )
            continue

        # 4. "~/.config" / "~/.cache" string literals in path-construction context
        if _is_home_path_string_literal(node):
            if not _is_inside_string_context(node, parent_map):
                violations.append(
                    RawHomePrimitive(
                        f'string literal {node.value!r} in path context',  # type: ignore[union-attr]
                        node.lineno,
                    )
                )
            continue

        # 5. .expanduser() method calls — collected for GRANDFATHERED check
        if isinstance(node, ast.Call) and _is_expanduser_call(node):
            site = (relpath, node.lineno)
            expanduser_sites.append(site)
            # Violation only if NOT in GRANDFATHERED
            if site not in GRANDFATHERED_EXPANDUSER_SITES:
                violations.append(
                    RawHomePrimitive(".expanduser() outside grandfathered set", node.lineno)
                )

    return violations, expanduser_sites


# ── Scan discovery ───────────────────────────────────────────────────────────

def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in _SCAN_ROOTS:
        files.extend(sorted(root.rglob("*.py")))
    assert files, f"no Python source files discovered under {_SCAN_ROOTS}"
    # Exclude the resolver itself — it IS the only allowed location.
    return [f for f in files if f.resolve() != _ALLOWED_MODULE.resolve()]


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path",
    _source_files(),
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_no_raw_home_path_primitive(path: Path) -> None:
    """No file outside ``config/loader.py`` may construct a home-path primitive."""
    relpath = str(path.relative_to(_REPO_ROOT))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations, _expanduser_sites = collect_raw_home_primitives(tree, relpath)
    assert not violations, (
        f"{relpath}: raw operator-state-root primitive(s) found — "
        "fold them to call _config_dir()/_cache_dir() instead:\n  "
        + "\n  ".join(f"line {v.lineno}: {v.kind}" for v in violations)
    )


def test_scanner_recurses_into_webui_and_adapters() -> None:
    """The scan must reach webui_app/, webui_store/, and the adapters sub-package."""
    scanned = {str(p.relative_to(_REPO_ROOT)) for p in _source_files()}
    assert any(p.startswith("webui_app/") for p in scanned), scanned
    assert any(p.startswith("webui_store/") for p in scanned), scanned
    assert any("publishing/adapters/" in p for p in scanned), scanned


def test_grandfathered_expanduser_sites_still_exist() -> None:
    """GRANDFATHERED_EXPANDUSER_SITES shrink-only ratchet.

    Every entry must still exist in the codebase.  If a site was removed or
    the line number shifted, update ``GRANDFATHERED_EXPANDUSER_SITES`` in
    ``tests/conftest.py``.
    """
    all_expanduser: set[tuple[str, int]] = set()
    for path in _source_files():
        relpath = str(path.relative_to(_REPO_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        _violations, expanduser_sites = collect_raw_home_primitives(tree, relpath)
        all_expanduser.update(expanduser_sites)

    missing = GRANDFATHERED_EXPANDUSER_SITES - all_expanduser
    assert not missing, (
        "GRANDFATHERED_EXPANDUSER_SITES entries are no longer present in source "
        "(removed or line-shifted) — shrink the set in tests/conftest.py:\n  "
        + "\n  ".join(f"{f}:{ln}" for f, ln in sorted(missing))
    )


def test_config_dir_calls_are_not_flagged() -> None:
    """The compliant namer pattern (_util/secrets.py:frw_token_path) must not be flagged."""
    snippet = (
        "from backlink_publisher import config as _cfg\n"
        "def frw_token_path():\n"
        "    return _cfg._config_dir() / 'frw-token.json'\n"
    )
    relpath = "src/backlink_publisher/_util/secrets.py"
    tree = ast.parse(snippet)
    violations, _ = collect_raw_home_primitives(tree, relpath)
    assert not violations, (
        "Compliant _config_dir() namer was falsely flagged: "
        + str(violations)
    )


def test_scanner_flags_path_home_call() -> None:
    """Anti-no-op: ``Path.home() / '.config' / ...`` in src must be caught."""
    snippet = (
        "from pathlib import Path\n"
        "sentinel = Path.home() / '.config' / 'backlink-publisher' / 'v0.3'\n"
    )
    relpath = "src/backlink_publisher/cli/_hypothetical.py"
    tree = ast.parse(snippet)
    violations, _ = collect_raw_home_primitives(tree, relpath)
    assert violations, "Path.home() construction was NOT detected — gate is a no-op"
    assert any(v.kind == "Path.home()" for v in violations), violations


def test_scanner_flags_expanduser_string_literal() -> None:
    """Anti-no-op: ``os.path.expanduser('~/.cache/...')`` must be caught."""
    snippet = (
        "import os\n"
        "p = os.path.expanduser('~/.cache/backlink-publisher/sentinel')\n"
    )
    relpath = "src/backlink_publisher/cli/_hypothetical.py"
    tree = ast.parse(snippet)
    violations, _ = collect_raw_home_primitives(tree, relpath)
    # The expanduser("~/.cache/...") string literal should be detected
    assert violations, "expanduser('~/.cache/...') was NOT detected — gate is a no-op"


def test_scanner_does_not_flag_error_message_string() -> None:
    """``~/.config/...`` inside an error-message string must NOT be flagged."""
    snippet = (
        "raise ValueError(\n"
        "    'Add credentials to ~/.config/backlink-publisher/config.toml'\n"
        ")\n"
    )
    relpath = "src/backlink_publisher/publishing/adapters/blogger_api.py"
    tree = ast.parse(snippet)
    violations, _ = collect_raw_home_primitives(tree, relpath)
    assert not violations, (
        "Error-message string was falsely flagged: " + str(violations)
    )


def test_scanner_does_not_flag_resolver_bodies() -> None:
    """``Path.home()`` inside ``config/loader.py`` is excluded from scanning."""
    # loader.py is excluded from _source_files() — verify the exclusion works.
    loader = _ALLOWED_MODULE
    assert loader.exists(), f"loader.py not found at {loader}"
    scanned = {p.resolve() for p in _source_files()}
    assert loader.resolve() not in scanned, (
        "config/loader.py appeared in the scan list — it must be excluded"
    )
