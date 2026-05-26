"""R8c (minimal): content gate pinning the STATUS_MAP classification.

This is a HAND-AUTHORED, independent expected table — deliberately NOT derived
from the reducer code it guards, so it can detect a wrong mapping (the
dofollow-canary lesson: a gate that proves "maps to *something*" is blind to
"maps to the *right* thing"). Flipping e.g. ("checkpoint","done") to unverified,
or turning an intentional NO_EMIT into quarantine, fails here.

The full R8a literal-ban lint + R8b bidirectional reader check land in the
follow-up PR; this gate locks the Seam-B content that the anti-P0 core depends on.
"""

from __future__ import annotations

import pytest

from backlink_publisher.events import kinds

# (source_record_type, source_status) -> expected outcome label.
# Outcome labels: a kind string, or one of "CONFIRMED_FAMILY"/"NO_EMIT"/"QUARANTINE".
_EXPECTED: dict[tuple[str, str], object] = {
    ("checkpoint", "pending"): "publish.intent",
    ("checkpoint", "done"): "CONFIRMED_FAMILY",
    ("checkpoint", "succeeded"): "CONFIRMED_FAMILY",
    ("checkpoint", "failed"): "publish.failed",
    ("checkpoint", "bogus"): "QUARANTINE",  # authoritative source -> drift
    ("history", "published"): "publish.confirmed",
    ("history", "failed"): "publish.failed",
    ("history", "drafted"): "NO_EMIT",  # owned by drafts, intentional
    ("history", "anything_else"): "NO_EMIT",  # catch-all suppressor
    ("drafts", "published"): "publish.confirmed",
    ("drafts", "scheduled"): "draft.scheduled",
    ("drafts", "drafted"): "draft.created",
    ("drafts", "failed"): "NO_EMIT",  # owned by history, intentional
    ("drafts", "anything_else"): "NO_EMIT",
}


def _label(outcome: object) -> object:
    if outcome is kinds.CONFIRMED_FAMILY:
        return "CONFIRMED_FAMILY"
    if outcome is kinds.NO_EMIT:
        return "NO_EMIT"
    if outcome is kinds.QUARANTINE:
        return "QUARANTINE"
    return outcome  # a kind string


@pytest.mark.parametrize("key,expected", list(_EXPECTED.items()))
def test_status_map_content_is_pinned(key, expected):
    source, status = key
    assert _label(kinds.classify(source, status)) == expected


def test_every_mapped_kind_is_registered():
    # Every concrete kind the map can emit must be in the vocabulary (R8a slice).
    for per_source in kinds.STATUS_MAP.values():
        for outcome in per_source.values():
            if isinstance(outcome, str):
                assert outcome in kinds.KINDS


def test_red_path_a_wrong_mapping_would_fail_this_gate():
    # Demonstrates the gate has teeth: if classify ever returned the wrong
    # outcome for a pinned pair, the parametrized test above would fail. Here we
    # assert the discriminator the P0 hinged on.
    assert _label(kinds.classify("checkpoint", "done")) != "publish.unverified"
    assert _label(kinds.classify("checkpoint", "done")) == "CONFIRMED_FAMILY"
