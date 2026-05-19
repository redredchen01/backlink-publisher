"""Schema-tier + git-tier tests for ``backlink_publisher.cli.plan_check``
(Units 1+2). Unit 3 (CLI wiring) will append to this file.

Tested surface:
- ``SCHEMA_VERSION`` constant
- ``_parse_frontmatter``
- ``_validate_claims_schema`` (incl. ``_validate_sha_format``)
- ``_grandfathered``
- ``_check_filename_date_lock`` (R11b / D17)
- named module-local exceptions with ``exit_code`` class attribute
- ``_fetch_head_age_seconds`` / ``_maybe_fetch_origin_main`` (Unit 2, D5/D16)
- ``_path_exists_on_main`` / ``_sha_reachable_from_main`` (Unit 2, R2/R3)
- ``FetchOutcome`` dataclass shape (Unit 2)
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import time
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


# ---------------------------------------------------------------------------
# Unit 2: git subprocess helpers — origin/main resolution + freshness
#
# We exercise real git via ``subprocess.run`` in tmp_path-isolated fixtures,
# never mocking ``subprocess`` itself — value of these tests is that the
# exit-code discrimination (0 / 1 / 128) actually matches real git behaviour
# (per ``tests/scripts/test_prune_stale_worktrees.py`` pattern).
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Helper: run ``git`` in *cwd* with C locale, returning the completed proc."""
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["GIT_AUTHOR_NAME"] = "t"
    env["GIT_AUTHOR_EMAIL"] = "t@t"
    env["GIT_COMMITTER_NAME"] = "t"
    env["GIT_COMMITTER_EMAIL"] = "t@t"
    res = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=env, check=False
    )
    if check:
        assert res.returncode == 0, f"git {args} failed in {cwd}: {res.stderr}"
    return res


@pytest.fixture
def repo_with_origin(tmp_path: Path) -> Path:
    """Build a tmp repo with:
      - one commit on ``main`` (introduces ``src/foo.py`` and ``src/foo/bar.py``)
      - one commit on a feature branch (``feat/x``) only
      - a bare clone as ``origin``, then ``fetch origin`` so ``origin/main`` resolves
    Returns the working-tree path.
    """
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@t")
    _git(main, "config", "user.name", "t")
    (main / "src").mkdir()
    (main / "src" / "foo.py").write_text("# foo\n")
    (main / "src" / "foo").mkdir(exist_ok=True)
    (main / "src" / "foo" / "bar.py").write_text("# bar\n")
    _git(main, "add", "src")
    _git(main, "commit", "-q", "-m", "init")
    # Feature branch with a commit NOT on main
    _git(main, "checkout", "-q", "-b", "feat/x")
    (main / "extra.py").write_text("# extra\n")
    _git(main, "add", "extra.py")
    _git(main, "commit", "-q", "-m", "extra on feature branch only")
    _git(main, "checkout", "-q", "main")
    # Bare clone + remote wiring so origin/main resolves
    bare = tmp_path / "origin.git"
    _git(main, "clone", "--bare", "-q", str(main), str(bare))
    _git(main, "remote", "add", "origin", str(bare))
    _git(main, "fetch", "-q", "origin")
    return main


def _head_sha(repo: Path, rev: str = "HEAD") -> str:
    return _git(repo, "rev-parse", rev).stdout.strip()


class TestPathExistsOnMain:
    def test_happy_path_root_file(self, repo_with_origin: Path, monkeypatch) -> None:
        monkeypatch.chdir(repo_with_origin)
        assert pc._path_exists_on_main("src/foo.py") == (True, "exists")

    def test_happy_path_nested_directory(self, repo_with_origin: Path, monkeypatch) -> None:
        monkeypatch.chdir(repo_with_origin)
        assert pc._path_exists_on_main("src/foo/bar.py") == (True, "exists")

    def test_missing_path_returns_missing(self, repo_with_origin: Path, monkeypatch) -> None:
        monkeypatch.chdir(repo_with_origin)
        # The file lives only on feat/x, never on main
        assert pc._path_exists_on_main("extra.py") == (False, "missing")

    def test_truly_absent_path_returns_missing(
        self, repo_with_origin: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(repo_with_origin)
        # Never existed in any tree
        assert pc._path_exists_on_main("never/touched.py") == (False, "missing")

    def test_outside_git_repo_returns_git_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # cwd is a fresh tmp dir, not a git repo at all
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)
        ok, status = pc._path_exists_on_main("anything.py")
        assert ok is False
        assert status == "git_error"


class TestShaReachableFromMain:
    def test_happy_path_full_sha(self, repo_with_origin: Path, monkeypatch) -> None:
        monkeypatch.chdir(repo_with_origin)
        full = _head_sha(repo_with_origin, "origin/main")
        assert pc._sha_reachable_from_main(full) == (True, "reachable")

    def test_short_sha_works_same_as_full(self, repo_with_origin: Path, monkeypatch) -> None:
        monkeypatch.chdir(repo_with_origin)
        short = _head_sha(repo_with_origin, "origin/main")[:7]
        assert pc._sha_reachable_from_main(short) == (True, "reachable")

    def test_sha_only_on_feature_branch_is_unreachable(
        self, repo_with_origin: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(repo_with_origin)
        feat_sha = _head_sha(repo_with_origin, "feat/x")
        main_sha = _head_sha(repo_with_origin, "origin/main")
        assert feat_sha != main_sha
        # Object DB knows the sha (we just committed it), but it's NOT an
        # ancestor of origin/main — that's exit 1 → "unreachable".
        assert pc._sha_reachable_from_main(feat_sha) == (False, "unreachable")

    def test_unknown_object_returns_unknown_object(
        self, repo_with_origin: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(repo_with_origin)
        # 7 lowercase hex chars that very plausibly aren't in the object DB
        ok, status = pc._sha_reachable_from_main("dead1234beef5678cafe9012345678901234abcd")
        assert ok is False
        assert status == "unknown_object"


class TestFetchHeadAgeSeconds:
    def test_returns_inf_outside_git_repo(self, tmp_path: Path, monkeypatch) -> None:
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)
        assert pc._fetch_head_age_seconds() == float("inf")

    def test_returns_inf_when_fetch_head_missing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # A fresh repo with no remote / no fetch yet → no FETCH_HEAD file.
        repo = tmp_path / "fresh"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        monkeypatch.chdir(repo)
        # Sanity: FETCH_HEAD must not exist
        assert not (repo / ".git" / "FETCH_HEAD").exists()
        assert pc._fetch_head_age_seconds() == float("inf")

    def test_age_reflects_real_mtime(self, repo_with_origin: Path, monkeypatch) -> None:
        monkeypatch.chdir(repo_with_origin)
        # repo_with_origin fixture ran `git fetch -q origin` so FETCH_HEAD exists
        fh = repo_with_origin / ".git" / "FETCH_HEAD"
        assert fh.exists()
        # Backdate FETCH_HEAD to 1000s ago via os.utime
        now = time.time()
        os.utime(fh, (now - 1000, now - 1000))
        age = pc._fetch_head_age_seconds()
        assert 990 <= age <= 1100, f"age was {age!r}"


class TestMaybeFetchOriginMain:
    def test_under_threshold_skips_fetch(
        self, repo_with_origin: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(repo_with_origin)
        fh = repo_with_origin / ".git" / "FETCH_HEAD"
        now = time.time()
        # 299s ago — under default 300s threshold
        os.utime(fh, (now - 299, now - 299))
        outcome = pc._maybe_fetch_origin_main()
        assert isinstance(outcome, pc.FetchOutcome)
        assert outcome.fetched is False
        assert outcome.skip_reason is None
        # Age populated, finite, and roughly correct
        assert outcome.fetch_head_age_seconds is not None
        assert 290 <= outcome.fetch_head_age_seconds <= 310

    def test_over_threshold_triggers_fetch(
        self, repo_with_origin: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(repo_with_origin)
        fh = repo_with_origin / ".git" / "FETCH_HEAD"
        now = time.time()
        # 301s ago — above default 300s threshold → real `git fetch` runs.
        # Remote is a local bare clone created in the fixture, so this should
        # succeed end-to-end without network.
        os.utime(fh, (now - 301, now - 301))
        outcome = pc._maybe_fetch_origin_main()
        assert isinstance(outcome, pc.FetchOutcome)
        assert outcome.fetched is True
        assert outcome.skip_reason is None
        # After a real fetch, FETCH_HEAD mtime is roughly "now" → age small.
        assert outcome.fetch_head_age_seconds is not None
        assert outcome.fetch_head_age_seconds < 60

    def test_missing_fetch_head_triggers_fetch(
        self, repo_with_origin: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(repo_with_origin)
        fh = repo_with_origin / ".git" / "FETCH_HEAD"
        if fh.exists():
            fh.unlink()
        outcome = pc._maybe_fetch_origin_main()
        assert outcome.fetched is True
        assert outcome.skip_reason is None
        assert outcome.fetch_head_age_seconds is not None

    def test_network_failure_classified_no_raise(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Build a repo whose origin points at an unresolvable host. Real
        # `git fetch` will emit "Could not resolve host" under LC_ALL=C.
        repo = tmp_path / "net-broken"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "f").write_text("x")
        _git(repo, "add", "f")
        _git(repo, "commit", "-q", "-m", "init")
        _git(repo, "remote", "add", "origin", "https://does-not-resolve.invalid/x.git")
        monkeypatch.chdir(repo)
        # Force stale so the fetch path runs
        outcome = pc._maybe_fetch_origin_main(threshold_seconds=0)
        assert outcome.fetched is False
        assert outcome.skip_reason == "network"

    def test_no_remote_failure_classified(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Origin points at a nonexistent local directory → "does not appear
        # to be a git repository" stderr under LC_ALL=C.
        repo = tmp_path / "no-remote"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "f").write_text("x")
        _git(repo, "add", "f")
        _git(repo, "commit", "-q", "-m", "init")
        bogus = tmp_path / "definitely-not-a-repo"
        _git(repo, "remote", "add", "origin", str(bogus))
        monkeypatch.chdir(repo)
        outcome = pc._maybe_fetch_origin_main(threshold_seconds=0)
        assert outcome.fetched is False
        assert outcome.skip_reason == "no_remote"

    def test_auth_failure_classified(self, monkeypatch) -> None:
        # Construct a synthetic stderr through the classifier helper. We don't
        # try to trigger a real auth failure (would need a live private repo);
        # the regression value is locking the substring → reason mapping.
        assert pc._classify_fetch_stderr("fatal: Authentication failed for 'x'") == "auth"
        assert pc._classify_fetch_stderr("Permission denied (publickey).") == "auth"
        assert pc._classify_fetch_stderr("fatal: could not read Username for 'x': terminal prompts disabled") == "auth"

    def test_other_failure_classified(self) -> None:
        assert pc._classify_fetch_stderr("fatal: something weird happened") == "other"
        # Empty stderr also falls into "other"
        assert pc._classify_fetch_stderr("") == "other"

    def test_fetch_failure_age_field_well_typed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Plan §Unit 2 scenario 13: when fetch fails AND ``FETCH_HEAD`` does not
        # resolve, the outcome must carry ``fetch_head_age_seconds=None``.
        # In practice real git creates an empty FETCH_HEAD even on a failed
        # connect, so the empty-file path is what actually exercises the
        # contract: the field is ``int | None`` (never absent, never ``inf``).
        # We additionally simulate the truly-missing case by deleting
        # FETCH_HEAD before the function returns; that's tested by going
        # through the helper ``_fetch_head_age_seconds`` directly.
        repo = tmp_path / "broken-no-fh"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "f").write_text("x")
        _git(repo, "add", "f")
        _git(repo, "commit", "-q", "-m", "init")
        _git(repo, "remote", "add", "origin", "https://does-not-resolve.invalid/x.git")
        fh = repo / ".git" / "FETCH_HEAD"
        if fh.exists():
            fh.unlink()
        monkeypatch.chdir(repo)
        outcome = pc._maybe_fetch_origin_main(threshold_seconds=0)
        assert outcome.fetched is False
        assert outcome.skip_reason == "network"
        # ``age`` is either ``None`` (truly missing) or an ``int`` (git wrote an
        # empty FETCH_HEAD as a side-effect of the failed fetch). Never ``inf``,
        # never a ``float``, never absent.
        assert outcome.fetch_head_age_seconds is None or isinstance(
            outcome.fetch_head_age_seconds, int
        )

    def test_fetch_head_truly_missing_yields_none_via_helper(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Direct contract for the FETCH_HEAD-absent branch: if the file is
        # genuinely not on disk, ``_fetch_head_age_seconds`` returns ``inf``
        # and ``_maybe_fetch_origin_main`` converts that to ``None`` on the
        # failure path (D16). We simulate by monkeypatching the helper so the
        # post-fetch stat sees no file.
        repo = tmp_path / "broken-mp"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "f").write_text("x")
        _git(repo, "add", "f")
        _git(repo, "commit", "-q", "-m", "init")
        _git(repo, "remote", "add", "origin", "https://does-not-resolve.invalid/x.git")
        monkeypatch.chdir(repo)
        # Force both stat calls (pre- and post-fetch) to report ``inf``
        monkeypatch.setattr(pc, "_fetch_head_age_seconds", lambda: float("inf"))
        outcome = pc._maybe_fetch_origin_main(threshold_seconds=0)
        assert outcome.fetched is False
        assert outcome.skip_reason == "network"
        assert outcome.fetch_head_age_seconds is None

    def test_age_always_populated_or_none_on_every_path(
        self, repo_with_origin: Path, monkeypatch
    ) -> None:
        """D16 contract: ``fetch_head_age_seconds`` is always ``int`` or ``None``,
        never absent. We probe both the skip-branch and the fetch-success branch."""
        monkeypatch.chdir(repo_with_origin)
        fh = repo_with_origin / ".git" / "FETCH_HEAD"
        now = time.time()
        # Skip branch
        os.utime(fh, (now - 10, now - 10))
        out_skip = pc._maybe_fetch_origin_main()
        assert out_skip.fetch_head_age_seconds is not None
        assert isinstance(out_skip.fetch_head_age_seconds, int)
        # Fetch branch
        os.utime(fh, (now - 1000, now - 1000))
        out_fetch = pc._maybe_fetch_origin_main()
        assert out_fetch.fetch_head_age_seconds is None or isinstance(
            out_fetch.fetch_head_age_seconds, int
        )


class TestFetchOutcomeDataclass:
    def test_frozen(self) -> None:
        outcome = pc.FetchOutcome(
            fetched=True, fetch_head_age_seconds=5, skip_reason=None
        )
        with pytest.raises(Exception):
            # frozen dataclass must reject attribute assignment
            outcome.fetched = False  # type: ignore[misc]

    def test_fields_present(self) -> None:
        outcome = pc.FetchOutcome(
            fetched=False, fetch_head_age_seconds=None, skip_reason="network"
        )
        assert outcome.fetched is False
        assert outcome.fetch_head_age_seconds is None
        assert outcome.skip_reason == "network"


class TestGitResolutionIntegration:
    def test_full_path_and_sha_resolution_end_to_end(
        self, repo_with_origin: Path, monkeypatch
    ) -> None:
        """Integration: tmp repo with two commits — one on main, one on feature —
        exercises the full ``_path_exists_on_main`` + ``_sha_reachable_from_main``
        layer in one fixture (plan §Unit 2 test scenario 15)."""
        monkeypatch.chdir(repo_with_origin)
        # Path on main: exists
        assert pc._path_exists_on_main("src/foo.py") == (True, "exists")
        # Path on feature branch only: missing on main
        assert pc._path_exists_on_main("extra.py") == (False, "missing")
        # SHA on main: reachable
        main_sha = _head_sha(repo_with_origin, "origin/main")
        assert pc._sha_reachable_from_main(main_sha) == (True, "reachable")
        # SHA on feature branch only: unreachable
        feat_sha = _head_sha(repo_with_origin, "feat/x")
        assert pc._sha_reachable_from_main(feat_sha) == (False, "unreachable")
