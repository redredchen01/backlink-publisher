"""Tests for the ``cull-channels`` read-only advisory verb (Blast-radius R9)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backlink_publisher.cli import cull_channels
from backlink_publisher.publishing.registry import (
    Publisher,
    register,
    _REGISTRY,
    _UI_META_BY_PLATFORM,
    _BIND_BY_PLATFORM,
    _POLICY_BY_PLATFORM,
    _VISIBILITY_BY_PLATFORM,
)


class _Stub(Publisher):
    @classmethod
    def available(cls, config: Any) -> bool:  # pragma: no cover - never dispatched
        return True

    def publish(self, payload: dict, mode: str, config: Any):  # pragma: no cover
        raise NotImplementedError


# register() requires rationale >= 80 chars for nofollow/uncertain platforms.
_R = "test fixture rationale for a registered sample platform, padded well beyond eighty chars"

# (name, kwargs) covering every classification branch.
_TEMP_PLATFORMS = [
    ("zz_cull", dict(dofollow=False, rationale=_R, referral_value="low")),
    ("zz_keep_dofollow", dict(dofollow=True)),
    ("zz_keep_high", dict(dofollow=False, rationale=_R, referral_value="high")),
    ("zz_unverifiable", dict(dofollow="uncertain", rationale=_R, referral_value="low")),
]

_ALL_MAPS = (
    _REGISTRY,
    _UI_META_BY_PLATFORM,
    _BIND_BY_PLATFORM,
    _POLICY_BY_PLATFORM,
    _VISIBILITY_BY_PLATFORM,
)


@pytest.fixture
def temp_platforms():
    """Register the four sample platforms; snapshot+restore every registry map."""
    names = [n for n, _ in _TEMP_PLATFORMS]
    saved = [{n: m[n] for n in names if n in m} for m in _ALL_MAPS]
    for name, kwargs in _TEMP_PLATFORMS:
        register(name, _Stub, **kwargs)
    try:
        yield names
    finally:
        for m, snap in zip(_ALL_MAPS, saved):
            for n in names:
                m.pop(n, None)
            m.update(snap)


def _rows_by_platform(capsys) -> dict[str, dict]:
    out = capsys.readouterr().out
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return {r["platform"]: r for r in rows}


def test_cull_candidate_classification(temp_platforms, capsys):
    """Happy path: nofollow + referral low → cull-candidate; dofollow → keep."""
    cull_channels.main(["--format", "json"])
    by = _rows_by_platform(capsys)
    assert by["zz_cull"]["classification"] == "cull-candidate"
    assert by["zz_keep_dofollow"]["classification"] == "keep"


def test_high_referral_nofollow_is_kept(temp_platforms, capsys):
    """Edge: nofollow + referral high → keep (carries equity despite nofollow)."""
    cull_channels.main(["--format", "json"])
    by = _rows_by_platform(capsys)
    assert by["zz_keep_high"]["classification"] == "keep"


def test_uncertain_is_unverifiable_never_culled(temp_platforms, capsys):
    """Edge: dofollow 'uncertain' → unverifiable even with referral low."""
    cull_channels.main(["--format", "json"])
    by = _rows_by_platform(capsys)
    assert by["zz_unverifiable"]["classification"] == "unverifiable"


def test_json_output_is_machine_clean(temp_platforms, capsys):
    """Edge: --format json stdout is pure JSONL (every line parses, no prose)."""
    cull_channels.main(["--format", "json"])
    out = capsys.readouterr().out
    for line in out.splitlines():
        if line.strip():
            json.loads(line)  # raises if any line is non-JSON


def test_markdown_is_default(temp_platforms, capsys):
    """Happy path: default output is a markdown table, not JSON."""
    cull_channels.main([])
    out = capsys.readouterr().out
    assert "| platform | classification |" in out
    assert "cull-candidate" in out


def test_invalid_format_raises_usage_error(capsys):
    """Error path: invalid --format → UsageError exit 1, never argparse's exit 2."""
    with pytest.raises(SystemExit) as exc:
        cull_channels.main(["--format", "xml"])
    assert exc.value.code == 1


def test_invalid_log_level_raises_usage_error(capsys):
    """Error path: invalid --log-level → UsageError exit 1, never argparse's exit 2."""
    with pytest.raises(SystemExit) as exc:
        cull_channels.main(["--log-level", "VERBOSE"])
    assert exc.value.code == 1
