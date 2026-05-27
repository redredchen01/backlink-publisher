"""Guard every *literal* exit code baked into CLI source against the
documented 0-8 universe.

This is the static-scan complement to ``tests/test_exit_code_contract.py``.
That file locks the exit codes carried as *class attributes* on
``_util.errors.PipelineError`` subclasses, and how ``handle_error`` /
``emit_error`` propagate them. It does **not** see integer literals that a CLI
hands directly to ``emit_error(..., exit_code=N)``, ``raise SystemExit(N)``, or
``sys.exit(N)`` -- exactly where the ``exit_code=45`` regression lived for five
days (``cli/_publish_helpers.py``, fixed in #223) without any contract test
noticing, because 45 never appears as a class attribute.

The documented universe is small and closed:
- pipeline contract 0-6 (AGENTS.md exit-code table), and
- plan-check's 0/1/2/7/8 drift-gate codes (reference_plan_check_cli).

So any integer literal flowing into a process exit from CLI source must be in
``{0, 1, 2, 3, 4, 5, 6, 7, 8}``. A stray 45 -- or any other undocumented code
-- breaks this test, forcing the author to either pick a documented code or
extend the table here in the same change.

Scope: ``src/backlink_publisher/cli/`` recursively (includes the
``plan_backlinks/`` and ``_bind/`` packages). Only *literal* ints are checked;
codes computed at runtime (variables, attributes) are out of static reach and
intentionally skipped -- the 45 bug was a literal, which is the failure mode
this guards.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

CLI_DIR = Path(__file__).resolve().parents[1] / "src" / "backlink_publisher" / "cli"

# The closed universe of documented process exit codes (see module docstring).
ALLOWED_EXIT_CODES = {0, 1, 2, 3, 4, 5, 6, 7, 8}

# Callables whose first positional integer literal is a process exit code.
_POSITIONAL_EXIT_CALLS = {"SystemExit", "exit", "_exit"}


def _is_int_constant(node: ast.AST) -> bool:
    # bool is an int subclass; AGENTS' codes are never booleans.
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    )


def _call_name(func: ast.AST) -> str:
    """Best-effort dotted-tail name for a call target (``sys.exit`` -> ``exit``)."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _exit_code_literals(tree: ast.AST) -> list[tuple[int, int]]:
    """Return ``(exit_code_literal, lineno)`` pairs found in *tree*.

    Catches three shapes:
    - any call with keyword ``exit_code=<int>`` (e.g. ``emit_error(msg, exit_code=3)``),
    - ``SystemExit(<int>)`` / ``sys.exit(<int>)`` / ``os._exit(<int>)`` positional,
    - ``emit_error(<msg>, <int>)`` positional-style exit code.
    """
    found: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        for kw in node.keywords:
            if kw.arg == "exit_code" and _is_int_constant(kw.value):
                found.append((kw.value.value, kw.value.lineno))

        name = _call_name(node.func)
        if name in _POSITIONAL_EXIT_CALLS and node.args:
            first = node.args[0]
            if _is_int_constant(first):
                found.append((first.value, first.lineno))
        elif name == "emit_error" and len(node.args) >= 2:
            second = node.args[1]
            if _is_int_constant(second):
                found.append((second.value, second.lineno))
    return found


def _cli_source_files() -> list[Path]:
    files = sorted(CLI_DIR.rglob("*.py"))
    assert files, f"no CLI source files discovered under {CLI_DIR}"
    return files


@pytest.mark.parametrize(
    "path", _cli_source_files(), ids=lambda p: str(p.relative_to(CLI_DIR))
)
def test_cli_exit_code_literals_are_documented(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = [
        f"{path.relative_to(CLI_DIR)}:{lineno} -> exit code {code}"
        for code, lineno in _exit_code_literals(tree)
        if code not in ALLOWED_EXIT_CODES
    ]
    assert not violations, (
        "undocumented exit-code literal(s) in CLI source -- pick a code from the "
        "0-8 contract or extend ALLOWED_EXIT_CODES with a rationale:\n  "
        + "\n  ".join(violations)
    )


def test_scanner_recurses_into_cli_subpackages() -> None:
    """The scan must reach the nested ``plan_backlinks/`` and ``_bind/`` packages
    where exit-code literals live -- a non-recursive glob would miss them."""
    scanned = {str(p.relative_to(CLI_DIR)) for p in _cli_source_files()}
    assert any(p.startswith("plan_backlinks/") for p in scanned), scanned
    assert any(p.startswith("_bind/") for p in scanned), scanned


def test_scanner_flags_an_undocumented_literal() -> None:
    """The 45-class regression must actually trip the collector -- otherwise this
    whole file is a no-op. Feed it the exact bug shape and a legitimate one."""
    snippet = (
        "emit_error('drifted', exit_code=45)\n"
        "emit_error('ok', exit_code=3)\n"
        "raise SystemExit(7)\n"
        "raise SystemExit(99)\n"
    )
    found = dict((code, lineno) for code, lineno in _exit_code_literals(ast.parse(snippet)))
    undocumented = {code for code in found if code not in ALLOWED_EXIT_CODES}
    assert undocumented == {45, 99}, found
