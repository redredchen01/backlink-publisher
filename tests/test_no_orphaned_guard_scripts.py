"""R5: forbid orphaned guard scripts.

A quality guard must be enforced by a CI surface, not left as an inert
``scripts/check_*.py`` that nothing runs (which gives every parallel agent false
confidence). This test asserts that every ``scripts/check_*.py`` is referenced by at
least one CI surface reachable from the repo root: a GitHub Actions workflow, a git
hook installer, or pre-commit config. It fails — naming the offender — the moment a
future change adds an unreferenced guard script.

There is no ``Makefile`` inside this package's tree; the only Makefile lives at the
workspace root, outside ``REPO_ROOT``, and is not a CI surface for this purpose.

Companion of ``test_no_monolith_regrowth.py``: both turn a repo invariant into a
CI-executed gate rather than a prose convention.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_SCRIPTS = sorted((REPO_ROOT / "scripts").glob("check_*.py"))


def _ci_surface_texts() -> list[tuple[Path, str]]:
    """Read every REPO_ROOT-reachable CI surface that could invoke a guard script."""
    surfaces: list[Path] = []
    surfaces += sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    surfaces += sorted((REPO_ROOT / ".github" / "workflows").glob("*.yaml"))
    surfaces += sorted((REPO_ROOT / "scripts").glob("install-*.sh"))
    precommit = REPO_ROOT / ".pre-commit-config.yaml"
    if precommit.exists():
        surfaces.append(precommit)
    return [(p, p.read_text(encoding="utf-8", errors="ignore")) for p in surfaces]


def test_no_orphaned_guard_scripts() -> None:
    """Every scripts/check_*.py must be invoked by some CI surface."""
    surface_texts = _ci_surface_texts()
    unreferenced = [
        script.name
        for script in GUARD_SCRIPTS
        if not any(script.name in text for _, text in surface_texts)
    ]
    assert not unreferenced, (
        "Orphaned guard script(s) not referenced by any CI surface "
        f"({[p.name for p, _ in surface_texts]}): {unreferenced}. "
        "A quality guard must live as a CI-executed test or workflow step — wire it "
        "into a workflow/hook, or delete it. See AGENTS.md 'CI surfaces'."
    )
