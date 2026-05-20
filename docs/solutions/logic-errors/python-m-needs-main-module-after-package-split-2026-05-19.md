---
title: "`python -m <pkg>` needs `__main__.py` after splitting a module into a package"
date: 2026-05-19
category: docs/solutions/logic-errors
module: cli/* package layout
problem_type: logic_error
component: tooling
symptoms:
  - "CI shell smoke step fails with `No module named <pkg>.__main__; <pkg> is a package and cannot be directly executed`"
  - "pytest passes; only the smoke step that invokes `python -m` breaks"
  - "Local dev fine via the entry-point script; only CI catches it"
root_cause: incomplete_setup
resolution_type: code_fix
severity: medium
related_components:
  - testing_framework
tags:
  - python
  - package-split
  - dash-m
  - main-module
  - entry-points
  - ci-smoke-test
---

# `python -m <pkg>` needs `__main__.py` after splitting a module into a package

## Problem

Splitting a single-file CLI module into a package (e.g., `cli/foo.py` → `cli/foo/`) breaks `python -m backlink_publisher.cli.foo` with `No module named <pkg>.__main__; <pkg> is a package and cannot be directly executed`. Pytest doesn't catch it because tests import the package and call functions directly. The entry-point script registered in `pyproject.toml` doesn't catch it because it points at `cli.foo:main` (a callable, not the `-m` path). The first thing that catches it is the CI shell smoke step, which is later than it should be.

PR #75 (velog adapter, P1 decomposition) split `cli/velog_login.py` into `cli/velog_login/` for organization. The split looked clean — all imports worked, all tests passed — and the smoke step in CI immediately failed because the package had no `__main__.py`.

## Symptoms

- CI step `python -m backlink_publisher.cli.<name> --help` exits with `No module named <name>.__main__; <name> is a package and cannot be directly executed`.
- `pytest tests/` passes.
- `<name>-login --help` (the entry-point script) works.
- Any operator playbook or external script that uses `python -m` is broken; entry-point users are fine.

## What Didn't Work

- **Assuming the entry-point script covers `python -m`**. It doesn't. They are two independent invocation paths sharing the `main()` callable.
- **Adding `from .core import main` to `cli/<name>/__init__.py`**. Makes the import work but doesn't make `python -m` work — `-m` needs `__main__.py` specifically.
- **Running `python -m backlink_publisher.cli.<name>.core`** as a workaround. Operators won't remember to do this; the contract surface is now non-uniform across modules.

## Solution

Add a one-line `__main__.py` to the package:

```python
# src/backlink_publisher/cli/<name>/__main__.py
from .core import main  # or wherever main() now lives

if __name__ == "__main__":
    main()
```

For a more idiomatic version when the package has a clear primary submodule:

```python
# src/backlink_publisher/cli/velog_login/__main__.py
from backlink_publisher.cli.velog_login.core import main
main()
```

Both work. The `if __name__ == "__main__":` guard is conventional but not necessary inside `__main__.py` (`__main__.py` only ever runs as `__main__`).

## Why This Works

Python's `-m` flag has two cases:

| Target | What `python -m foo` does |
|--------|---------------------------|
| `foo.py` (module) | Executes `foo.py` as `__main__` |
| `foo/__init__.py` (package) | Looks for `foo/__main__.py`; errors if absent |

Splitting a module to a package transitions you from row 1 to row 2. Without `__main__.py`, `python -m` has nowhere to dispatch. This is by design: Python wants the package author to make an explicit decision about which submodule serves as the entry. The error message names the missing file, which is the fix.

Pytest doesn't catch this because it imports modules and runs functions; it never invokes `-m`. Entry-point scripts (`pyproject.toml` `[project.scripts]`) point at `package.module:callable`, which also doesn't go through `-m`. Both paths sidestep `__main__.py` entirely.

## Prevention

- **Lint rule for package splits**: any directory under `src/backlink_publisher/cli/` that has an `__init__.py` should also have `__main__.py`. Easy to check:

```bash
for d in src/backlink_publisher/cli/*/; do
  if [ -f "$d/__init__.py" ] && [ ! -f "$d/__main__.py" ]; then
    echo "MISSING __main__.py: $d"
  fi
done
```

Add to `tests/test_cli_layout.py` so a future package split can't ship without it.

- **CI smoke test for every CLI**: parameterize a job over all entry-points:

```yaml
- run: |
    for cli in plan-backlinks validate-backlinks publish-backlinks report-anchors footprint phase0-seal; do
      python -m backlink_publisher.cli.${cli//-/_} --help
    done
```

The smoke step caught the PR #75 case; making it complete (every CLI, every release) catches the next.

- **Reviewer checklist for any `cli/<name>.py` → `cli/<name>/` refactor**: confirm `__main__.py` exists in the same PR. The decomposition is the trigger; pair them.

## Related Issues

- PR #75 — original incident; `cli/velog_login.py` → `cli/velog_login/` without `__main__.py`.
- `pyproject.toml` `[project.scripts]` — entry-point declarations; independent of `-m`.
- `docs/solutions/logic-errors/fetch-head-in-common-gitdir-2026-05-20.md` — adjacent: another "works in canonical path, breaks in derived path" structural bug.
- Python docs `__main__.py` semantics: https://docs.python.org/3/library/__main__.html
