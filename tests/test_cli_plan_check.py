"""Schema-tier tests for ``backlink_publisher.cli.plan_check`` (Unit 1).

Unit 2 (git resolution) and Unit 3 (CLI wiring) will append to this file.

Tested surface (Unit 1 only):
- ``SCHEMA_VERSION`` constant
- ``_parse_frontmatter``
- ``_validate_claims_schema`` (incl. ``_validate_sha_format``)
- ``_grandfathered``
- ``_check_filename_date_lock`` (R11b / D17)
- named module-local exceptions with ``exit_code`` class attribute
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from backlink_publisher.cli import plan_check as pc


# ---------------------------------------------------------------------------
# Module-level invariants
# ---------------------------------------------------------------------------


class TestModuleInvariants:
    def test_schema_version_is_one(self) -> None:
        assert pc.SCHEMA_VERSION == 1

    def test_named_exceptions_carry_exit_codes(self) -> None:
        # mirror _util/errors.py: each domain error has an `exit_code` class attr
        assert pc.PlanClaimsFrontmatterSchemaError.exit_code == 2
        assert pc.PlanClaimsMissingOnPostCutoff.exit_code == 8
        assert pc.PlanClaimsGlobUnsupported.exit_code == 2
        assert pc.PlanClaimsFilenameDateMismatch.exit_code == 2


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------


def _plan_text(frontmatter_body: str, body: str = "\n# Plan\n") -> str:
    return f"---\n{frontmatter_body}\n---\n{body}"


class TestParseFrontmatter:
    def test_well_formed(self) -> None:
        fm = pc._parse_frontmatter(_plan_text("date: 2026-05-21\nclaims:\n  paths: []\n  shas: []"))
        assert isinstance(fm, dict)
        assert fm["date"] == _dt.date(2026, 5, 21)
        assert fm["claims"] == {"paths": [], "shas": []}

    def test_no_frontmatter_raises(self) -> None:
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError, match="missing YAML frontmatter"):
            pc._parse_frontmatter("# Just a heading, no fence\n")

    def test_missing_closing_fence_raises(self) -> None:
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError):
            pc._parse_frontmatter("---\ndate: 2026-05-21\n# never closed\n")

    def test_empty_frontmatter_block_raises(self) -> None:
        # yaml.safe_load("") returns None — treat as schema error
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError):
            pc._parse_frontmatter("---\n---\nbody\n")

    def test_top_level_is_list_raises(self) -> None:
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError, match="mapping"):
            pc._parse_frontmatter(_plan_text("- foo\n- bar"))


# ---------------------------------------------------------------------------
# UTF-8 BOM stripping and non-UTF8 handling (via _read_plan_text helper)
# ---------------------------------------------------------------------------


class TestReadPlanText:
    def test_utf8_bom_stripped(self, tmp_path: Path) -> None:
        p = tmp_path / "2026-05-21-001-foo-plan.md"
        # write BOM-prefixed UTF-8
        p.write_bytes(b"\xef\xbb\xbf" + _plan_text("date: 2026-05-21\nclaims: {}").encode("utf-8"))
        text = pc._read_plan_text(p)
        # BOM stripped so the leading --- is detected
        assert text.startswith("---\n")
        fm = pc._parse_frontmatter(text)
        assert fm["date"] == _dt.date(2026, 5, 21)

    def test_non_utf8_raises_schema_error(self, tmp_path: Path) -> None:
        p = tmp_path / "2026-05-21-001-foo-plan.md"
        # latin-1 with a byte that's not valid UTF-8 start
        p.write_bytes(b"---\ndate: 2026-05-21\n# caf\xe9\n---\n")
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError, match="UTF-8|decode"):
            pc._read_plan_text(p)


# ---------------------------------------------------------------------------
# _grandfathered (date-typed comparison, R9)
# ---------------------------------------------------------------------------


class TestGrandfathered:
    def test_pre_cutoff_is_grandfathered(self) -> None:
        assert pc._grandfathered({"date": _dt.date(2026, 5, 19)}) is True

    def test_cutoff_day_is_not_grandfathered(self) -> None:
        # cutoff is `< date(2026, 5, 20)` — equality is NOT grandfathered
        assert pc._grandfathered({"date": _dt.date(2026, 5, 20)}) is False

    def test_post_cutoff_is_not_grandfathered(self) -> None:
        assert pc._grandfathered({"date": _dt.date(2026, 5, 21)}) is False

    def test_non_date_typed_raises(self) -> None:
        # string `"May 19 2026"` would never have parsed; assume already-typed date.
        # Non-iso strings should be rejected at parse time. We assert that
        # _grandfathered refuses anything that isn't a datetime.date.
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError, match="date"):
            pc._grandfathered({"date": "May 19 2026"})

    def test_missing_date_field_raises(self) -> None:
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError, match="date"):
            pc._grandfathered({})


# ---------------------------------------------------------------------------
# _validate_sha_format (R3 / D17 / G3)
# ---------------------------------------------------------------------------


class TestValidateShaFormat:
    @pytest.mark.parametrize("sha", ["abc1234", "0123456", "abcdef0123456789abcdef0123456789abcdef01"])
    def test_valid_lowercase_hex(self, sha: str) -> None:
        # 7-char short + 40-char full both pass
        assert pc._validate_sha_format(sha) is True

    def test_six_char_too_short_fails(self) -> None:
        assert pc._validate_sha_format("abc123") is False

    def test_forty_one_char_too_long_fails(self) -> None:
        assert pc._validate_sha_format("a" * 41) is False

    def test_mixed_case_fails(self) -> None:
        assert pc._validate_sha_format("ABC1234") is False
        assert pc._validate_sha_format("Abc1234") is False

    def test_non_hex_char_fails(self) -> None:
        # "z" is not [0-9a-f]
        assert pc._validate_sha_format("abc123z") is False

    def test_empty_string_fails(self) -> None:
        assert pc._validate_sha_format("") is False


# ---------------------------------------------------------------------------
# _validate_claims_schema (R1-R4)
# ---------------------------------------------------------------------------


class TestValidateClaimsSchema:
    def test_happy_path_returns_block(self) -> None:
        fm = {
            "date": _dt.date(2026, 5, 21),
            "claims": {"paths": ["src/foo.py"], "shas": ["abc1234"]},
        }
        block = pc._validate_claims_schema(fm)
        assert block is not None
        assert block.paths == ["src/foo.py"]
        assert block.shas == ["abc1234"]

    def test_empty_claims_returns_empty_block(self) -> None:
        fm = {"date": _dt.date(2026, 5, 21), "claims": {}}
        block = pc._validate_claims_schema(fm)
        assert block is not None
        assert block.paths == []
        assert block.shas == []
        # explicit opt-out marker — implementation may expose either via attr or by emptiness
        assert getattr(block, "is_explicit_optout", True) is True

    def test_missing_claims_block_on_post_cutoff_raises(self) -> None:
        fm = {"date": _dt.date(2026, 5, 21)}  # no claims key
        with pytest.raises(pc.PlanClaimsMissingOnPostCutoff):
            pc._validate_claims_schema(fm)

    def test_unknown_key_under_claims_raises(self) -> None:
        fm = {
            "date": _dt.date(2026, 5, 21),
            "claims": {"paths": [], "shas": [], "symbols": ["foo"]},
        }
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError, match="symbols"):
            pc._validate_claims_schema(fm)

    @pytest.mark.parametrize("glob", ["src/*.py", "src/?oo.py", "src/[abc].py"])
    def test_glob_in_paths_raises(self, glob: str) -> None:
        fm = {
            "date": _dt.date(2026, 5, 21),
            "claims": {"paths": [glob], "shas": []},
        }
        with pytest.raises(pc.PlanClaimsGlobUnsupported):
            pc._validate_claims_schema(fm)

    def test_short_sha_accepted(self) -> None:
        fm = {
            "date": _dt.date(2026, 5, 21),
            "claims": {"paths": [], "shas": ["abc1234"]},
        }
        block = pc._validate_claims_schema(fm)
        assert block.shas == ["abc1234"]

    def test_full_sha_accepted(self) -> None:
        full = "abcdef0123456789abcdef0123456789abcdef01"
        fm = {
            "date": _dt.date(2026, 5, 21),
            "claims": {"paths": [], "shas": [full]},
        }
        block = pc._validate_claims_schema(fm)
        assert block.shas == [full]

    def test_non_hex_sha_raises_schema_error(self) -> None:
        fm = {
            "date": _dt.date(2026, 5, 21),
            "claims": {"paths": [], "shas": ["zzzzzz1"]},
        }
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError, match="sha"):
            pc._validate_claims_schema(fm)

    def test_mixed_case_sha_raises_schema_error(self) -> None:
        fm = {
            "date": _dt.date(2026, 5, 21),
            "claims": {"paths": [], "shas": ["ABC1234"]},
        }
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError):
            pc._validate_claims_schema(fm)

    def test_too_short_sha_raises_schema_error(self) -> None:
        fm = {
            "date": _dt.date(2026, 5, 21),
            "claims": {"paths": [], "shas": ["abc123"]},  # 6 chars
        }
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError):
            pc._validate_claims_schema(fm)

    def test_too_long_sha_raises_schema_error(self) -> None:
        fm = {
            "date": _dt.date(2026, 5, 21),
            "claims": {"paths": [], "shas": ["a" * 41]},
        }
        with pytest.raises(pc.PlanClaimsFrontmatterSchemaError):
            pc._validate_claims_schema(fm)


# ---------------------------------------------------------------------------
# _check_filename_date_lock (R11b / D17)
# ---------------------------------------------------------------------------


class TestFilenameDateLock:
    def test_happy_path_match(self, tmp_path: Path) -> None:
        p = tmp_path / "2026-05-21-001-feat-foo-plan.md"
        p.write_text("placeholder")
        fm = {"date": _dt.date(2026, 5, 21)}
        # should not raise
        pc._check_filename_date_lock(p, fm)

    def test_backdate_attempt_raises(self, tmp_path: Path) -> None:
        # filename says 2026-05-21 but frontmatter says 2026-05-19 (backdate to escape cutoff)
        p = tmp_path / "2026-05-21-001-feat-foo-plan.md"
        p.write_text("placeholder")
        fm = {"date": _dt.date(2026, 5, 19)}
        with pytest.raises(pc.PlanClaimsFilenameDateMismatch) as excinfo:
            pc._check_filename_date_lock(p, fm)
        msg = str(excinfo.value)
        # message must cite both values for operator self-correction
        assert "2026-05-21" in msg
        assert "2026-05-19" in msg

    def test_no_date_prefix_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "foo-plan.md"
        p.write_text("placeholder")
        fm = {"date": _dt.date(2026, 5, 21)}
        with pytest.raises(pc.PlanClaimsFilenameDateMismatch, match="YYYY-MM-DD"):
            pc._check_filename_date_lock(p, fm)
