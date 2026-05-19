"""AGENTS.md "Binding a channel" section existence — Plan 2026-05-19-001 Unit 7.

Sanity guard, not a behavior assertion: if a future refactor of AGENTS.md
accidentally drops the operator-facing binding lifecycle section, this
test fires before the agents lose the reference.

Mirrors the pattern in ``tests/test_no_monolith_regrowth.py`` (file-as-text
+ substring assertion).
"""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_AGENTS_MD = _REPO_ROOT / "AGENTS.md"


def test_agents_md_contains_binding_a_channel_section():
    """Header + the canonical bind-channel CLI signature must both appear."""
    text = _AGENTS_MD.read_text(encoding="utf-8")
    assert "## Binding a channel" in text, (
        "AGENTS.md is missing the '## Binding a channel' section. "
        "Plan 2026-05-19-001 Unit 7 added this section so agents know "
        "where to learn the credential lifecycle. Restore it before merging."
    )
    assert "bind-channel --channel" in text, (
        "AGENTS.md 'Binding a channel' section must mention the CLI signature "
        "`bind-channel --channel <name>`. Plan 2026-05-19-001 Unit 7 pins this "
        "as the canonical entry point name."
    )


def test_agents_md_documents_publish_time_flip():
    """The section must surface the publish-time 401 → AuthExpiredError → exit-3
    contract so adapter authors find it without grepping the source."""
    text = _AGENTS_MD.read_text(encoding="utf-8")
    assert "AuthExpiredError" in text, (
        "AGENTS.md must reference AuthExpiredError so adapter authors know "
        "what to raise on 401/403. Plan 2026-05-19-001 Unit 7."
    )
    assert "mark_expired" in text, (
        "AGENTS.md must reference mark_expired so adapter / publish-loop "
        "authors know which side effect a 401 must trigger. "
        "Plan 2026-05-19-001 Unit 7."
    )
