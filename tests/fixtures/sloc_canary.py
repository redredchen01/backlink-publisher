"""SLOC canary fixture for tests/test_no_monolith_regrowth.py.

This file pins radon's SLOC counter against a hand-crafted Python source.
If radon changes its counter logic (new version, new Python AST shape), the
test_radon_sloc_behavior_pinned test fails with a clear diff between
expected vs actual SLOC -- isolating measurement-tool drift from real code growth.

Constructs covered (representative of monitored files):
    - module docstring
    - `from __future__` import + stdlib + typing imports
    - `if TYPE_CHECKING:` conditional import
    - bare assignments
    - function def with docstring + body
    - class def with docstring + method
    - list comprehension, dict comprehension
    - walrus operator inside `if`
    - `match` / `case` statement
    - f-string with embedded expression
    - multi-line string literal assigned to a variable

PEP 695 `type` aliases intentionally skipped (3.12+ only; fixture must run on 3.11 too).

Expected SLOC is recorded in tests/test_no_monolith_regrowth.py as
SLOC_CANARY_EXPECTED. To re-baseline (e.g., after a deliberate radon bump):
run `python -m radon raw -s tests/fixtures/sloc_canary.py`, read the SLOC
value, update the constant. This is intentional friction -- counter drift
should be a conscious decision, not a silent shift.
"""

from __future__ import annotations

from pathlib import Path  # noqa: F401  -- intentional construct exemplar for radon SLOC counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable  # noqa: F401  -- intentional TYPE_CHECKING-block exemplar

X = 1
Y = "hello"
Z = [1, 2, 3]


def greet(name: str) -> str:
    """Return a greeting for the named subject."""
    return f"hello, {name}!"


class Counter:
    """A small counter, here to exercise class-body SLOC counting."""

    def __init__(self) -> None:
        self.n = 0

    def bump(self) -> int:
        self.n += 1
        return self.n


SQUARES = [n * n for n in range(5)]
BY_KEY = {k: k.upper() for k in ("a", "b", "c")}


def parse(value: object) -> str:
    if (length := len(str(value))) > 10:
        return f"long ({length})"
    match value:
        case int():
            return "int"
        case str():
            return "str"
        case _:
            return "other"


MULTILINE = """one
two
three"""
