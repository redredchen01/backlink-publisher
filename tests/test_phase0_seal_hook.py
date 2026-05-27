"""Integration tests: pre-push hook ↔ verify-hook subcommand — Plan 009 Unit 5.

Installs the hook into a throwaway tmp repo (via the
`scripts/install-pre-push-hook.sh` installer), seeds tmp-state, then runs
`git push origin <ref>` against a bare tmp remote and asserts the hook's
exit code + side-effects.

Plan v3 §417-429 test scenarios mapped:
- Pre-G1 (no phase-marker note on origin/main):
  - push refused without env override
  - push allowed with PHASE0_ALLOW_LOCAL_PUSH=1
- Post-G1 (phase-marker note present on origin/main):
  - push allowed with valid seal note at HEAD
  - push refused on drifted HEAD (no note at the pushed SHA)
  - **env override does NOT bypass** post-G1 (auto-fix v2-F4 / plan v3 §407)
  - direct-SHA push via non-Telegraph local_ref is still gated (key on remote_ref)
- Hook fetch race: operator init wrote 4 local notes (not yet pushed); hook
  fires; assert local refs/notes/phase0-seal NOT overwritten by TEMP-ref
  fetch (auto-fix v2-F4).
- Non-Telegraph refs: hook stays out of the way.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "scripts" / "install-pre-push-hook.sh"


def _run(cwd: Path, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=check, env=env,
    )


def _seal_body(*, unit: str, branch: str, main_sha: str) -> dict:
    return {
        "unit": unit,
        "branch": branch,
        "main_sha": main_sha,
        "sealed_at": "2026-06-01T10:00:00Z",
        "sealed_by": "operator:init",
        "verdict_ref": {
            "kind": "routine_comment",
            "pr": 36,
            "comment_url": "https://github.com/x/y/pull/36#issuecomment-12345",
            "comment_id": 12345,
            "comment_author": "telegraph-routine-bot[bot]",
            "comment_created_at": "2026-05-25T10:00:00Z",
            "comment_updated_at": "2026-05-25T10:00:00Z",
            "comment_body_sha256": "0" * 64,
        },
    }


def _install_hook(repo: Path) -> Path:
    """Install the pre-push hook into ``repo`` via the project installer.

    The installer resolves repo_root from its own script location, so we
    invoke it from ``repo`` to make it install into ``repo``'s .git/hooks.
    We pass --git-dir explicitly to keep it pinned even with weird CWD.
    """
    # Run installer with cwd=repo so its `git rev-parse --show-toplevel`
    # resolves to repo. We also need the installer to use repo's git dir —
    # since the installer cd's to its own SCRIPT_DIR, copy it into repo first.
    repo_scripts = repo / "scripts"
    repo_scripts.mkdir(parents=True, exist_ok=True)
    target = repo_scripts / "install-pre-push-hook.sh"
    target.write_bytes(INSTALLER.read_bytes())
    target.chmod(0o755)
    subprocess.run(
        ["bash", str(target)], cwd=repo, capture_output=True, text=True, check=True,
    )
    hook = repo / ".git" / "hooks" / "pre-push"
    assert hook.exists(), f"hook not installed at {hook}"
    return hook


@pytest.fixture
def hook_env(tmp_path: Path) -> dict:
    """A bare remote + non-bare clone with the pre-push hook installed.

    Returns paths + a 'PYTHONPATH=src' env dict so the subprocess hook can
    `python -m backlink_publisher.cli.phase0_seal verify-hook` from this
    worktree's source.
    """
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    remote.mkdir()
    repo.mkdir()
    _run(remote, "init", "--bare", "--initial-branch=main")
    _run(repo, "init", "--initial-branch=main")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")
    _run(repo, "remote", "add", "origin", str(remote))
    (repo / "README.md").write_text("init\n")
    _run(repo, "add", ".")
    _run(repo, "commit", "-m", "init")
    _run(repo, "push", "-u", "origin", "main")

    _install_hook(repo)

    # Inherit the test runner's env + ensure PYTHONPATH points at this repo's
    # src/ so the hook can import backlink_publisher.
    env = os.environ.copy()
    src_dir = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = src_dir + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PYTHON"] = sys.executable

    return {
        "tmp_path": tmp_path,
        "remote": remote,
        "repo": repo,
        "env": env,
    }


def _make_staged_branch_with_commit(repo: Path, unit_n: int) -> tuple[str, str]:
    """Create local/telegraph-unit{N}-staged with one commit. Returns (branch, sha)."""
    branch = f"local/telegraph-unit{unit_n}-staged"
    _run(repo, "checkout", "-b", branch)
    (repo / f"unit{unit_n}.txt").write_text(f"unit{unit_n}\n")
    _run(repo, "add", f"unit{unit_n}.txt")
    _run(repo, "commit", "-m", f"unit{unit_n}")
    sha = _run(repo, "rev-parse", "HEAD").stdout.strip()
    return branch, sha


def _attach_seal_note(repo: Path, sha: str, body: dict) -> None:
    _run(repo, "notes", "--ref=phase0-seal", "add",
         "-m", json.dumps(body), sha)


def _mark_g1_started_on_remote(remote: Path, repo: Path) -> None:
    """Attach a phase-marker note to origin/main + push to bare remote so
    `git fetch origin refs/notes/phase0-seal` retrieves it."""
    # Make an arbitrary marker body (verify-hook doesn't validate its schema —
    # presence is enough per plan v3 §405).
    _run(repo, "fetch", "origin", "main")
    origin_main = _run(repo, "rev-parse", "origin/main").stdout.strip()
    _run(repo, "notes", "--ref=phase0-seal", "add",
         "-m", json.dumps({"phase_marker": True}), origin_main)
    _run(repo, "push", "origin", "refs/notes/phase0-seal:refs/notes/phase0-seal")
    # Remove local marker so the test exercises the "fetch from remote" path
    # (else the local note alone would set PHASE_STARTED=1).
    _run(repo, "notes", "--ref=phase0-seal", "remove", origin_main, check=False)
    # Also remove any cached temp ref so the hook actually re-fetches.
    _run(repo, "update-ref", "-d", "refs/notes/phase0-seal-origin", check=False)


def _push(hook_env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "push", *args],
        cwd=hook_env["repo"],
        env=hook_env["env"],
        capture_output=True, text=True,
    )


# ============================================================================
# Pre-G1: legacy §194 fallback applies
# ============================================================================


class TestPreG1Fallback:
    def test_push_staged_branch_refused_without_env_override(self, hook_env: dict) -> None:
        branch, _sha = _make_staged_branch_with_commit(hook_env["repo"], 2)
        result = _push(hook_env, "origin", branch)
        assert result.returncode != 0
        assert "PHASE0_ALLOW_LOCAL_PUSH" in result.stderr or "refusing" in result.stderr.lower()

    def test_push_staged_branch_allowed_with_env_override(self, hook_env: dict) -> None:
        branch, _sha = _make_staged_branch_with_commit(hook_env["repo"], 2)
        env = dict(hook_env["env"])
        env["PHASE0_ALLOW_LOCAL_PUSH"] = "1"
        result = subprocess.run(
            ["git", "push", "origin", branch],
            cwd=hook_env["repo"], env=env, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr={result.stderr!r}"

    def test_non_telegraph_push_unaffected(self, hook_env: dict) -> None:
        _run(hook_env["repo"], "checkout", "-b", "feat/something-else")
        (hook_env["repo"] / "x.txt").write_text("x\n")
        _run(hook_env["repo"], "add", "x.txt")
        _run(hook_env["repo"], "commit", "-m", "x")
        result = _push(hook_env, "origin", "feat/something-else")
        assert result.returncode == 0, f"stderr={result.stderr!r}"


# ============================================================================
# Post-G1: seal enforcement (phase-marker note on origin/main)
# ============================================================================


class TestPostG1SealEnforcement:
    def test_push_with_valid_seal_allowed(self, hook_env: dict) -> None:
        branch, sha = _make_staged_branch_with_commit(hook_env["repo"], 2)
        main_sha = _run(hook_env["repo"], "rev-parse", "main").stdout.strip()
        _attach_seal_note(hook_env["repo"], sha, _seal_body(
            unit="unit2", branch=branch, main_sha=main_sha,
        ))
        _mark_g1_started_on_remote(hook_env["remote"], hook_env["repo"])
        result = _push(hook_env, "origin", branch)
        assert result.returncode == 0, f"stderr={result.stderr!r}"

    def test_push_drifted_head_refused(self, hook_env: dict) -> None:
        """Operator adds a fixup commit after sealing — HEAD has moved past
        the sealed SHA, no note at the new HEAD, push refused."""
        branch, sealed_sha = _make_staged_branch_with_commit(hook_env["repo"], 2)
        main_sha = _run(hook_env["repo"], "rev-parse", "main").stdout.strip()
        _attach_seal_note(hook_env["repo"], sealed_sha, _seal_body(
            unit="unit2", branch=branch, main_sha=main_sha,
        ))
        # Drift: add a new commit; HEAD no longer == sealed_sha
        (hook_env["repo"] / "fixup.txt").write_text("oops\n")
        _run(hook_env["repo"], "add", "fixup.txt")
        _run(hook_env["repo"], "commit", "-m", "fixup")
        _mark_g1_started_on_remote(hook_env["remote"], hook_env["repo"])
        result = _push(hook_env, "origin", branch)
        assert result.returncode != 0
        assert "no-seal-note" in result.stderr

    def test_env_override_does_not_bypass_post_g1(self, hook_env: dict) -> None:
        """Auto-fix v2 / plan v3 §407: PHASE0_ALLOW_LOCAL_PUSH=1 must NOT
        let an unsealed staged-branch push through once the phase marker
        is on origin/main."""
        branch, _sha = _make_staged_branch_with_commit(hook_env["repo"], 2)
        # No seal note attached.
        _mark_g1_started_on_remote(hook_env["remote"], hook_env["repo"])
        env = dict(hook_env["env"])
        env["PHASE0_ALLOW_LOCAL_PUSH"] = "1"
        result = subprocess.run(
            ["git", "push", "origin", branch],
            cwd=hook_env["repo"], env=env, capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "no-seal-note" in result.stderr

    def test_direct_sha_push_with_non_telegraph_local_ref(self, hook_env: dict) -> None:
        """v1 adversarial probe #5: `git push origin <SHA>:refs/heads/local/
        telegraph-unitN-staged` with a non-staged local_ref. The hook keys on
        remote_ref so seal validation MUST still fire (and pass when a valid
        seal exists at the SHA)."""
        branch, sha = _make_staged_branch_with_commit(hook_env["repo"], 2)
        main_sha = _run(hook_env["repo"], "rev-parse", "main").stdout.strip()
        _attach_seal_note(hook_env["repo"], sha, _seal_body(
            unit="unit2", branch=branch, main_sha=main_sha,
        ))
        _mark_g1_started_on_remote(hook_env["remote"], hook_env["repo"])
        # Switch to main so HEAD is no longer on the staged branch
        _run(hook_env["repo"], "checkout", "main")
        result = _push(hook_env, "origin", f"{sha}:refs/heads/{branch}")
        # The push should succeed (valid seal at the SHA) even though local_ref
        # is the bare SHA. This proves the hook keys on remote_ref.
        assert result.returncode == 0, f"stderr={result.stderr!r}"


# ============================================================================
# Auto-fix v2-F4: fetch-into-TEMP-ref does not overwrite local notes
# ============================================================================


class TestFetchRaceSafety:
    def test_temp_ref_fetch_does_not_overwrite_local_notes(self, hook_env: dict) -> None:
        """Operator writes 4 local seal notes (not yet pushed). Hook fires
        on a push that targets origin (which has its own phase-marker note).
        The hook fetches origin's notes into refs/notes/phase0-seal-origin
        (TEMP ref); the operator's local refs/notes/phase0-seal must remain
        unchanged."""
        # Build a few staged branches w/ local seal notes
        branch_u2, sha_u2 = _make_staged_branch_with_commit(hook_env["repo"], 2)
        main_sha = _run(hook_env["repo"], "rev-parse", "main").stdout.strip()
        _attach_seal_note(hook_env["repo"], sha_u2, _seal_body(
            unit="unit2", branch=branch_u2, main_sha=main_sha,
        ))
        local_note_blob_before = _run(
            hook_env["repo"], "rev-parse", f"refs/notes/phase0-seal:{sha_u2}"
        ).stdout.strip()
        assert local_note_blob_before  # sanity

        # Phase marker on origin/main (different content than our local notes)
        _mark_g1_started_on_remote(hook_env["remote"], hook_env["repo"])

        # Push — hook will fetch into temp ref
        result = _push(hook_env, "origin", branch_u2)
        assert result.returncode == 0, f"stderr={result.stderr!r}"

        # Local note unchanged (TEMP ref didn't overwrite phase0-seal)
        local_note_blob_after = _run(
            hook_env["repo"], "rev-parse", f"refs/notes/phase0-seal:{sha_u2}"
        ).stdout.strip()
        assert local_note_blob_after == local_note_blob_before


# ============================================================================
# Installer + hook idempotency
# ============================================================================


class TestInstaller:
    def test_installer_creates_executable_hook(self, hook_env: dict) -> None:
        hook = hook_env["repo"] / ".git" / "hooks" / "pre-push"
        assert hook.exists()
        # On Unix, check the user execute bit
        mode = hook.stat().st_mode
        assert mode & 0o100, f"hook not executable; mode={oct(mode)}"

    def test_installer_idempotent_when_rerun(self, hook_env: dict) -> None:
        """Re-running the installer over its own output should not double-
        write or back up; the marker line signals it's already managed."""
        installer = hook_env["repo"] / "scripts" / "install-pre-push-hook.sh"
        first_content = (hook_env["repo"] / ".git" / "hooks" / "pre-push").read_bytes()
        subprocess.run(["bash", str(installer)], cwd=hook_env["repo"],
                       capture_output=True, text=True, check=True)
        second_content = (hook_env["repo"] / ".git" / "hooks" / "pre-push").read_bytes()
        assert first_content == second_content
        # No backup file should be left behind on idempotent reinstall
        assert not (hook_env["repo"] / ".git" / "hooks" / "pre-push.bak").exists()
