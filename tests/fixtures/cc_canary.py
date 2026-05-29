"""Cyclomatic-complexity canary fixture for tests/test_no_complexity_regrowth.py.

This file pins radon's CC counter against a hand-crafted function. If radon
changes its complexity logic (new version, new Python AST shape), the
test_radon_cc_behavior_pinned test fails with a clear diff between expected vs
actual CC -- isolating measurement-tool drift from real code growth. This is the
CC analog of tests/fixtures/sloc_canary.py.

The canary function below deliberately exercises the decision-point constructs
radon counts toward CC: if/elif, for, while, boolean and/or operators, an
except handler, and match/case arms. The expected CC is recorded in
tests/test_no_complexity_regrowth.py as CC_CANARY_EXPECTED. To re-baseline (e.g.
after a deliberate radon bump): run
`python -m radon cc -s tests/fixtures/cc_canary.py`, read the complexity of
`cc_canary_branchy`, and update the constant in the same PR that bumps radon
(per the budget's re-seed policy). This is intentional friction -- counter drift
should be a conscious decision, not a silent shift.

PEP 695 / 3.12-only syntax intentionally avoided; the fixture must parse on 3.11.
"""

from __future__ import annotations


def cc_canary_branchy(x: int, items: list[int]) -> object:
    """A deliberately branchy function with a fixed, pinned cyclomatic complexity."""
    total = 0
    if x > 0:
        total += 1
    elif x < 0:
        total -= 1
    for i in items:
        if i and x:
            total += i
        elif i or x:
            total -= 1
    while total > 100:
        total -= 10
    try:
        total = total // x
    except ZeroDivisionError:
        total = 0
    match total:
        case 0:
            return "zero"
        case 1:
            return "one"
        case _:
            return total
    return total
