"""Layer 1 — static AST gate: ban inner-import scope shadowing.

Any function (or async function) that has an unaliased inner import of a name
that is also bound by a module-level import, AND references that name BEFORE
the inner import line, will crash at runtime with ``UnboundLocalError``.

Python's ``compile()`` determines local-vs-global scope at compile time.  If
``import X`` appears ANYWHERE in a function body, ``X`` is treated as a local
variable across the *entire* function — even code *before* the import line.
References to the module-level ``X`` above the inner import raise
``UnboundLocalError``::

    import logging                          # global X

    def func():
        logger = logging.getLogger(...)     # ✗ UnboundLocalError
        import logging                      #   shadows X locally

The fix (for code above the import) is to use an alias::

    def func():
        logger = logging.getLogger(...)     # ✓ works — logging is still global
        import logging as _logging          #   local name _logging, not logging

Aliased imports (``import X as Y``, ``from X import Y as Z``) are SAFE because
the local name differs from the module-level name.  Inner imports that never
shadow a module-level name are also safe.

Mirror of ``test_no_raw_home_path_primitives.py`` (collector + parametrize +
anti-no-op tests + recursion-coverage test).
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

# ── Scan roots ──────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOTS = [
    _REPO_ROOT / "src" / "backlink_publisher",
    _REPO_ROOT / "webui_app",
    _REPO_ROOT / "webui_store",
]


# ── AST helpers ─────────────────────────────────────────────────────────────

class InnerImportShadowing(NamedTuple):
    name: str
    lineno: int
    func_name: str  # best-effort dotted name


def _get_module_imports(tree: ast.AST) -> set[str]:
    """Return the set of names bound by top-level (module-level) import statements.

    Covers::

        import foo             →  foo
        import foo.bar         →  foo      (top-level package)
        import foo as bar      →  bar
        from x import y        →  y
        from x import y as z   →  z
        from x import (a, b)   →  a, b
    """
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _is_load_context(node: ast.Name) -> bool:
    """True when an ast.Name is in LOAD context."""
    ctx = getattr(node, "ctx", None)
    if ctx is None:
        return True
    return isinstance(ctx, ast.Load)


def _collect_load_names(
    node: ast.AST,
    seen: dict[str, int],
) -> None:
    """Collect LOAD-context *ast.Name* references from *node*'s subtree.

    Does NOT descend into ``FunctionDef``, ``AsyncFunctionDef``, or
    ``ClassDef`` nodes — those create new scopes.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return
    if isinstance(node, ast.Name) and _is_load_context(node):
        if node.id not in seen:
            seen[node.id] = node.lineno
    for child in ast.iter_child_nodes(node):
        _collect_load_names(child, seen)


def _process_import_node(
    node: ast.Import | ast.ImportFrom,
    module_imports: set[str],
    seen_refs: dict[str, int],
    violations: list[InnerImportShadowing],
    func_name: str,
) -> None:
    """Check a single import statement for shadowing and add its names to seen_refs."""
    bound: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            bound.add(alias.asname or alias.name.split(".")[0])
    else:
        for alias in node.names:
            bound.add(alias.asname or alias.name)

    for name in bound:
        if name in module_imports and name in seen_refs:
            violations.append(
                InnerImportShadowing(name=name, lineno=node.lineno, func_name=func_name)
            )
        # The import ALSO acts as an assignment — add it to seen_refs so later
        # code referencing the name doesn't produce a spurious violation.
        if name not in seen_refs:
            seen_refs[name] = node.lineno


def _process_body(
    body: list[ast.stmt],
    module_imports: set[str],
    seen_refs: dict[str, int],
    violations: list[InnerImportShadowing],
    func_name: str,
) -> None:
    """Walk *body* statements in source order, tracking references and imports.

    Compound statements (``if``, ``for``, ``while``, ``try``, ``with``,
    ``match``) have their bodies processed with the **same** ``seen_refs``
    because they share the function's scope.
    """
    for stmt in body:
        # ── Import statements ────────────────────────────────────────────
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            _process_import_node(stmt, module_imports, seen_refs, violations, func_name)
            continue

        # ── New function scope — recurse with FRESH seen_refs ────────────
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_function(stmt, module_imports, violations)
            continue

        # ── Class body — method definitions get fresh scopes; other stmts
        #    are at class scope and do NOT share the enclosing function's
        #    local variable namespace.  We still walk them for completeness,
        #    but class-level assignments do not shadow function-level refs
        #    in the enclosing function.
        if isinstance(stmt, ast.ClassDef):
            _check_class(stmt, module_imports, violations)
            continue

        # ── Compound statements whose bodies share the same function scope ─
        #    if / elif / else
        if isinstance(stmt, ast.If):
            _collect_load_names(stmt.test, seen_refs)
            _process_body(stmt.body, module_imports, seen_refs, violations, func_name)
            _process_body(stmt.orelse, module_imports, seen_refs, violations, func_name)
            continue

        #    for / else
        if isinstance(stmt, (ast.For, ast.AsyncFor)):
            _collect_load_names(stmt.target, seen_refs)
            _collect_load_names(stmt.iter, seen_refs)
            _process_body(stmt.body, module_imports, seen_refs, violations, func_name)
            _process_body(stmt.orelse, module_imports, seen_refs, violations, func_name)
            continue

        #    while / else
        if isinstance(stmt, ast.While):
            _collect_load_names(stmt.test, seen_refs)
            _process_body(stmt.body, module_imports, seen_refs, violations, func_name)
            _process_body(stmt.orelse, module_imports, seen_refs, violations, func_name)
            continue

        #    try / except / else / finally
        if isinstance(stmt, ast.Try):
            _process_body(stmt.body, module_imports, seen_refs, violations, func_name)
            for handler in stmt.handlers:
                # The exception variable (``except X as e``) binds locally.
                if handler.name is not None and handler.name not in seen_refs:
                    seen_refs[handler.name] = handler.lineno
                if handler.type is not None:
                    _collect_load_names(handler.type, seen_refs)
                _process_body(handler.body, module_imports, seen_refs, violations, func_name)
            _process_body(stmt.orelse, module_imports, seen_refs, violations, func_name)
            _process_body(stmt.finalbody, module_imports, seen_refs, violations, func_name)
            continue

        #    with / async with
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                _collect_load_names(item.context_expr, seen_refs)
                if item.optional_vars is not None:
                    # ``with X as y`` binds y locally.
                    _collect_load_names(item.optional_vars, seen_refs)
            _process_body(stmt.body, module_imports, seen_refs, violations, func_name)
            continue

        #    match / case  (Python 3.10+)
        if isinstance(stmt, ast.Match):
            _collect_load_names(stmt.subject, seen_refs)
            for case in stmt.cases:
                _process_body(case.body, module_imports, seen_refs, violations, func_name)
            continue

        # ── Everything else: collect load-name references ────────────────
        _collect_load_names(stmt, seen_refs)


def _check_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    module_imports: set[str],
    violations: list[InnerImportShadowing],
    *,
    prefix: str = "",
) -> None:
    """Check a single function for inner-import shadowing violations."""
    func_name = f"{prefix}.{node.name}" if prefix else node.name
    seen_refs: dict[str, int] = {}
    _process_body(node.body, module_imports, seen_refs, violations, func_name)


def _check_class(
    node: ast.ClassDef,
    module_imports: set[str],
    violations: list[InnerImportShadowing],
    *,
    prefix: str = "",
) -> None:
    """Walk class body — process methods recursively, skip non-function stmts."""
    cls_prefix = f"{prefix}.{node.name}" if prefix else node.name
    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_function(stmt, module_imports, violations, prefix=cls_prefix)
        elif isinstance(stmt, ast.ClassDef):
            _check_class(stmt, module_imports, violations, prefix=cls_prefix)


def collect_inner_import_shadowing(
    tree: ast.AST,
    relpath: str,
) -> list[InnerImportShadowing]:
    """Walk *tree* and return every inner-import that shadows a module-level name.

    Returns list of ``InnerImportShadowing`` — one entry per violating import.
    """
    module_imports = _get_module_imports(tree)
    violations: list[InnerImportShadowing] = []

    # Walk top-level functions and classes.
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_function(node, module_imports, violations)
        elif isinstance(node, ast.ClassDef):
            _check_class(node, module_imports, violations)

    return violations


# ── Scan discovery ──────────────────────────────────────────────────────────

def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in _SCAN_ROOTS:
        files.extend(sorted(root.rglob("*.py")))
    assert files, f"no Python source files discovered under {_SCAN_ROOTS}"
    return files


# ── Tests ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path",
    _source_files(),
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_no_inner_import_shadowing(path: Path) -> None:
    """No function may import a name that is also a module-level import
    if the name is referenced before the import line.

    Python's compile-time scope analysis marks ``X`` as local everywhere in
    a function that contains ``import X``, so references to the module-level
    ``X`` that appear before the import line raise ``UnboundLocalError``.
    """
    relpath = str(path.relative_to(_REPO_ROOT))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = collect_inner_import_shadowing(tree, relpath)
    assert not violations, (
        f"{relpath}: inner-import scope shadowing found — use an alias "
        f"(\"import X as _X\") instead of a bare \"import X\" to avoid "
        f"UnboundLocalError at Python compile time:\n  "
        + "\n  ".join(
            f"line {v.lineno}: function {v.func_name} "
            f"has bare \"import {v.name}\" shadowing module-level \"{v.name}\" "
            f"after a reference to it"
            for v in violations
        )
    )


def test_scanner_recurses_into_webui_and_adapters() -> None:
    """The scan must reach webui_app/, webui_store/, and the adapters sub-package."""
    scanned = {str(p.relative_to(_REPO_ROOT)) for p in _source_files()}
    assert any(p.startswith("webui_app/") for p in scanned), scanned
    assert any(p.startswith("webui_store/") for p in scanned), scanned
    assert any("backlink_publisher/publishing/adapters/" in p for p in scanned), scanned


# ── Anti-no-op tests ────────────────────────────────────────────────────────


def test_scanner_flags_import_inside_function() -> None:
    """Anti-no-op: bare ``import X`` inside a function after a ref to X must be caught."""
    snippet = (
        "import logging\n"
        "\n"
        "def func():\n"
        "    logger = logging.getLogger(__name__)\n"
        "    import logging  # shadows\n"
    )
    tree = ast.parse(snippet)
    violations = collect_inner_import_shadowing(tree, "test.py")
    assert violations, (
        "Inner import shadowing was NOT detected — gate is a no-op"
    )
    assert any(v.name == "logging" and v.func_name == "func" for v in violations), (
        f"Expected logging/func violation, got: {violations}"
    )


def test_scanner_flags_import_inside_if_block() -> None:
    """Anti-no-op: ``import X`` inside an ``if`` after a ref must be caught."""
    snippet = (
        "import logging\n"
        "\n"
        "def func():\n"
        "    logger = logging.getLogger(__name__)\n"
        "    if True:\n"
        "        import logging\n"
    )
    tree = ast.parse(snippet)
    violations = collect_inner_import_shadowing(tree, "test.py")
    assert violations, (
        "Inner import in if-block was NOT detected — gate is a no-op"
    )


def test_scanner_does_not_flag_aliased_import() -> None:
    """``import X as _X`` must NOT be flagged — alias is safe."""
    snippet = (
        "import logging\n"
        "\n"
        "def func():\n"
        "    logger = logging.getLogger(__name__)\n"
        "    import logging as _logging  # alias — safe\n"
    )
    tree = ast.parse(snippet)
    violations = collect_inner_import_shadowing(tree, "test.py")
    assert not violations, (
        "Aliased inner import was falsely flagged: " + str(violations)
    )


def test_scanner_does_not_flag_import_before_reference() -> None:
    """``import X`` before any reference to X is safe — must NOT be flagged."""
    snippet = (
        "import logging\n"
        "\n"
        "def func():\n"
        "    import logging\n"
        "    logger = logging.getLogger(__name__)\n"
    )
    tree = ast.parse(snippet)
    violations = collect_inner_import_shadowing(tree, "test.py")
    assert not violations, (
        "Import-before-reference was falsely flagged: " + str(violations)
    )


def test_scanner_does_not_flag_no_shadow() -> None:
    """Inner import of a name NOT in module-level imports must NOT be flagged."""
    snippet = (
        "import os\n"
        "\n"
        "def func():\n"
        "    import json  # json is not a module-level import — safe\n"
    )
    tree = ast.parse(snippet)
    violations = collect_inner_import_shadowing(tree, "test.py")
    assert not violations, (
        "Non-shadowing import was falsely flagged: " + str(violations)
    )
