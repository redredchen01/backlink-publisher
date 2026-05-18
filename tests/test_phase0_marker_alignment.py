"""CI gate: routine prompt template marker MUST align with validation.MARKER_RE.

Closes v2-review maintainability gap: the marker string lives in TWO places —
(a) Python constant `validation.MARKER_RE`, and (b) the routine prompt
appended via `RemoteTrigger action=update` in Unit 1 (recorded in
`scripts/telegraph_spike/routine-prompt-changelog-*.md`). If either drifts,
init silently rejects legitimate Pass comments.

This test parses the latest routine-prompt-changelog file (glob; sort
descending) and asserts the marker example documented there matches the
Python regex.

When Unit 1 has NOT landed yet (no changelog files), this test SKIPS rather
than fails — the gate is meaningful only once both sides exist.
"""

from __future__ import annotations

import glob
from pathlib import Path

import pytest

from backlink_publisher.phase0 import validation as V


def _find_repo_root() -> Path:
    """Walk up from this test file until we find pyproject.toml."""
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("could not locate repo root from test file")


def _latest_changelog() -> Path | None:
    root = _find_repo_root()
    pattern = str(root / "scripts" / "telegraph_spike" / "routine-prompt-changelog-*.md")
    matches = sorted(glob.glob(pattern), reverse=True)
    if not matches:
        return None
    return Path(matches[0])


def test_marker_in_latest_changelog_matches_python_regex() -> None:
    changelog = _latest_changelog()
    if changelog is None:
        pytest.skip(
            "No routine-prompt-changelog file present yet "
            "(Unit 1 from docs/plans/2026-05-18-009-...-plan.md hasn't landed). "
            "This gate activates once Unit 1 commits its changelog."
        )

    body = changelog.read_text(encoding="utf-8")
    # Changelog MUST contain at least one example of the marker.
    matches = V.MARKER_RE.findall(body)
    assert matches, (
        f"Latest changelog {changelog} contains no marker matching "
        f"{V.MARKER_RE.pattern!r}. Routine prompt template and Python "
        f"validation.MARKER_RE have drifted. Reconcile both before merge."
    )
