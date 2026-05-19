"""`phase0-seal` CLI — Telegraph Phase 0 ship SHA seal operator-side tool.

Subcommands:
    init        Write seal notes for each worktree HEAD after G1 Pass.
    show        Print current seal (markdown allowlist or JSON).
    verify      Compare seal SHAs to current worktree HEADs.
    reseal      Refresh seal SHAs while preserving verdict_ref + sealed_at.
    verify-hook Hook-side validator (invoked by .git/hooks/pre-push).

Unit 2 lands the dispatcher skeleton; each subcommand handler currently
raises NotImplementedError. Subsequent units fill them in:
    Unit 3 → init (incl. --manual-verdict + post-push verify)
    Unit 4 → show, verify, reseal
    Unit 5 → verify-hook
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from ..phase0 import validation as V
from ..phase0.worktree import WorktreeEntry, discover_worktree_heads

_NOTES_REF = "refs/notes/phase0-seal"
_NOTES_VERIFY_REF = "refs/notes/phase0-seal-verify-init"


# Exit-code namespace (R8 documents the contract for hook + R7a):
EXIT_OK = 0           # success
EXIT_MISUSE = 1       # subcommand-specific misuse (e.g., seal already exists)
EXIT_WORKTREE = 2     # worktree missing / dirty / detached / evidence-file-out-of-repo
EXIT_VERDICT = 3      # gh auth fail / comment validation fail / allowlist load fail
EXIT_NOT_IMPLEMENTED = 99  # unit not yet landed (removed once impl lands)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phase0-seal",
        description="Telegraph Phase 0 ship SHA seal — operator-side CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    init_p = sub.add_parser(
        "init",
        help="Create seal notes after observing G1 Pass routine comment",
    )
    src = init_p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--verdict-comment",
        metavar="URL",
        help="Full URL of the G1 Pass PR comment posted by the routine bot",
    )
    src.add_argument(
        "--manual-verdict",
        action="store_true",
        help="Fallback for routine outage; pair with --evidence-log",
    )
    init_p.add_argument(
        "--verdict-pr",
        type=int,
        help="PR # the verdict comment belongs to (required for --verdict-comment)",
    )
    init_p.add_argument(
        "--evidence-log",
        metavar="PATH",
        help="Relative path to committed evidence file (required for --manual-verdict)",
    )
    init_p.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip the render-and-confirm prompt before writing notes",
    )
    init_p.set_defaults(handler=_handle_init)

    # show
    show_p = sub.add_parser("show", help="Print current seal block(s)")
    show_p.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (markdown applies R15b field allowlist; default markdown)",
    )
    show_p.add_argument(
        "--unit",
        metavar="UNIT",
        help="Restrict output to one unit (e.g., unit2); default = all 4",
    )
    show_p.set_defaults(handler=_handle_show)

    # verify
    verify_p = sub.add_parser("verify", help="Compare seal SHAs to current worktree HEADs")
    verify_p.add_argument(
        "--check-comment",
        action="store_true",
        help="Also re-fetch verdict comment via gh and re-validate author/marker",
    )
    verify_p.set_defaults(handler=_handle_verify)

    # reseal
    reseal_p = sub.add_parser(
        "reseal",
        help="Update seal SHAs to current worktree HEADs; preserves verdict_ref + sealed_at",
    )
    reseal_p.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip the old→new diff prompt before writing",
    )
    reseal_p.set_defaults(handler=_handle_reseal)

    # verify-hook (Unit 5)
    hook_p = sub.add_parser(
        "verify-hook",
        help="Hook-side validator; invoked by .git/hooks/pre-push (Unit 5)",
    )
    hook_p.add_argument(
        "--stdin-lines",
        action="store_true",
        help="Read all stdin lines (multi-ref push); validate each that matches Telegraph pattern",
    )
    hook_p.set_defaults(handler=_handle_verify_hook)

    return parser


# ---------------------------------------------------------------------------
# Stub handlers (raise NotImplementedError; replaced in subsequent units).
# ---------------------------------------------------------------------------


def _handle_init(args: argparse.Namespace) -> int:
    """Create seal notes for each staged worktree HEAD.

    Routine path (`--verdict-comment <url>`): fetch comment via `gh api`,
    validate author/PR/marker/body_sha256 against the allowlist, then write
    per-worktree notes + push to origin + **post-push verify** (v3 BLOCKER
    fix: ensure the push actually landed by fetching back into a temp ref
    and round-tripping each note before exiting 0).

    Manual path (`--manual-verdict --evidence-log <path>`): same flow but
    skips comment fetch; reads the evidence file (must be inside repo and
    committed at HEAD on each unit's branch) and records its sha256.
    """
    repo_root = V.find_main_worktree_root()

    try:
        allowlist = V.load_allowlist(repo_root)
    except (V.AllowlistFileMissingError, V.EmptyAllowlistError, V.AllowlistSchemaError) as exc:
        print("phase0-seal init: {exc}", file=sys.stderr)
        return EXIT_VERDICT

    # Build verdict_ref (routine or manual)
    try:
        if args.manual_verdict:
            verdict_ref = _build_manual_verdict_ref(args.evidence_log, repo_root)
        else:
            verdict_ref = _build_routine_verdict_ref(
                comment_url=args.verdict_comment,
                expected_pr=args.verdict_pr,
                allowlist=allowlist,
                repo_root=repo_root,
            )
    except _InitError as exc:
        print("phase0-seal init: {exc}", file=sys.stderr)
        return exc.exit_code

    # Discover staged worktrees
    entries = discover_worktree_heads(V.TELEGRAPH_BRANCH_PATTERN, repo_root=repo_root)
    if not entries:
        print(
            f"phase0-seal init: no staged worktrees matching {V.TELEGRAPH_BRANCH_PATTERN!r}; "
            f"clone the worktrees first via 'git worktree add ../bp-local-unit{{N}} {V.TELEGRAPH_BRANCH_PATTERN.replace('*', '<N>')}'",
            file=sys.stderr,
        )
        return EXIT_WORKTREE

    # Refuse if any worktree is missing / dirty / detached / mid-rebase (R20a)
    for e in entries:
        if e.path is None:
            print(
                f"phase0-seal init: unit {e.unit} worktree missing at expected path; "
                f"run 'git worktree add ../bp-local-{e.unit} {e.branch}' first",
                file=sys.stderr,
            )
            return EXIT_WORKTREE
        if e.is_clean is False:
            print(
                f"phase0-seal init: unit {e.unit} worktree dirty (uncommitted changes); "
                f"commit, stash, or revert first",
                file=sys.stderr,
            )
            return EXIT_WORKTREE
        if e.is_detached:
            print(
                f"phase0-seal init: unit {e.unit} HEAD detached; "
                f"check out the staged branch by name first",
                file=sys.stderr,
            )
            return EXIT_WORKTREE
        if e.has_rebase_in_progress:
            print(
                f"phase0-seal init: unit {e.unit} mid-rebase; "
                f"complete or abort the rebase before sealing",
                file=sys.stderr,
            )
            return EXIT_WORKTREE

    # For manual-verdict, also require evidence file committed on EACH unit's branch.
    if args.manual_verdict:
        rel = verdict_ref["evidence_path"]
        for e in entries:
            check = subprocess.run(
                ["git", "-C", str(e.path), "ls-files", "--error-unmatch", rel],
                capture_output=True, text=True,
            )
            if check.returncode != 0:
                print(
                    f"phase0-seal init: --manual-verdict evidence file {rel!r} "
                    f"is NOT committed at HEAD on unit {e.unit}'s branch "
                    f"(R7a reads it at PR head — file must exist there); "
                    f"commit it on each unit branch first",
                    file=sys.stderr,
                )
                return EXIT_WORKTREE

    main_sha = _get_main_sha(repo_root)
    sealed_at = _now_iso()

    # Build per-SHA seal bodies
    bodies: dict[str, str] = {}
    for e in entries:
        body = {
            "unit": e.unit,
            "branch": e.branch,
            "main_sha": main_sha,
            "sealed_at": sealed_at,
            "last_resealed_at": None,
            "sealed_by": "operator:init",
            "verdict_ref": verdict_ref,
        }
        # Validate before writing — strict-positive (catches our own construction bugs)
        V.validate_seal_schema(body)
        bodies[e.sha] = json.dumps(body, sort_keys=True, separators=(",", ":"))

    # Confirmation prompt (skip with -y)
    if not args.yes:
        print("phase0-seal init: about to write seal notes:", file=sys.stderr)
        for sha, body in bodies.items():
            print("  {sha}: {body[:160]}{'...' if len(body) > 160 else ''}", file=sys.stderr)
        try:
            resp = input("Continue? [y/N]: ")
        except EOFError:
            resp = ""
        if resp.strip().lower() not in ("y", "yes"):
            print("phase0-seal init: cancelled by operator", file=sys.stderr)
            return EXIT_OK

    # Write notes (no -f; refuses if a note already exists at the SHA)
    for sha, body in bodies.items():
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "add", "-m", body, sha],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or "").lower()
            if "cannot add" in stderr or "exists" in stderr or "already" in stderr:
                print(
                    f"phase0-seal init: seal note already exists at {sha}; "
                    f"use 'phase0-seal reseal' to update",
                    file=sys.stderr,
                )
                return EXIT_MISUSE
            print(
                f"phase0-seal init: git notes add failed at {sha}: {proc.stderr.strip()}",
                file=sys.stderr,
            )
            return EXIT_MISUSE

    # Push notes ref to origin
    push_proc = subprocess.run(
        ["git", "-C", str(repo_root), "push", "origin", f"{_NOTES_REF}:{_NOTES_REF}"],
        capture_output=True, text=True,
    )
    if push_proc.returncode != 0:
        print(
            f"phase0-seal init: pushing notes ref to origin failed: {push_proc.stderr.strip()}; "
            f"retry init after fixing the cause (network, refs/notes/ branch protection)",
            file=sys.stderr,
        )
        return EXIT_MISUSE

    # Post-push verify (v3 BLOCKER fix #1 — closes v2-review adversarial F1)
    try:
        _post_push_verify(repo_root, bodies)
    except _NotesPushDidNotLand as exc:
        print("phase0-seal init: {exc}", file=sys.stderr)
        return EXIT_MISUSE

    print(
        f"phase0-seal init: wrote and verified seal notes for {len(bodies)} unit(s); "
        f"sealed_at={sealed_at}",
        file=sys.stderr,
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# Init helpers
# ---------------------------------------------------------------------------


class _InitError(Exception):
    """Init pre-flight validation failure with a specific exit_code."""

    def __init__(self, msg: str, exit_code: int) -> None:
        super().__init__(msg)
        self.exit_code = exit_code


class _NotesPushDidNotLand(Exception):
    """Notes-push to origin returned 0 but verify-fetch shows it did not land."""


_COMMENT_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?:pull|issues)/(?P<pr>\d+)"
    r"#issuecomment-(?P<id>\d+)"
)


def _parse_comment_url(url: str) -> tuple[str, str, int, int]:
    """Return (owner, repo, pr_number, comment_id) parsed from the PR comment URL."""
    if not isinstance(url, str):
        raise _InitError(f"--verdict-comment must be a URL string, got {type(url).__name__}", EXIT_VERDICT)
    m = _COMMENT_URL_RE.search(url)
    if not m:
        raise _InitError(
            f"--verdict-comment URL not recognized; expected "
            f"https://github.com/<owner>/<repo>/pull/<n>#issuecomment-<id>, got {url}",
            EXIT_VERDICT,
        )
    return m["owner"], m["repo"], int(m["pr"]), int(m["id"])


def _build_routine_verdict_ref(
    *,
    comment_url: str,
    expected_pr: int | None,
    allowlist: dict,
    repo_root: Path,
) -> dict:
    owner, repo, pr_from_url, comment_id = _parse_comment_url(comment_url)
    if expected_pr is None:
        expected_pr = pr_from_url
    elif expected_pr != pr_from_url:
        raise _InitError(
            f"--verdict-pr={expected_pr} does not match PR # parsed from --verdict-comment URL ({pr_from_url})",
            EXIT_VERDICT,
        )

    try:
        comment = V._run_gh(f"repos/{owner}/{repo}/issues/comments/{comment_id}")
    except V.GhNotInstalledError as exc:
        raise _InitError(str(exc), EXIT_VERDICT) from exc
    except V.GhAuthError as exc:
        raise _InitError(str(exc), EXIT_VERDICT) from exc
    except RuntimeError as exc:
        raise _InitError(f"gh api failed: {exc}", EXIT_VERDICT) from exc

    try:
        validated = V.validate_verdict_comment(
            comment, expected_pr=expected_pr, allowlist=allowlist,
        )
    except V.SealValidationError as exc:
        raise _InitError(f"verdict comment validation failed: {exc}", EXIT_VERDICT) from exc

    return {
        "kind": "routine_comment",
        "pr": expected_pr,
        "comment_url": validated["comment_url"] or comment_url,
        "comment_id": validated["comment_id"],
        "comment_author": validated["user_login"],
        "comment_created_at": validated["comment_created_at"],
        "comment_updated_at": validated["comment_updated_at"],
        "comment_body_sha256": validated["body_sha256"],
    }


def _build_manual_verdict_ref(evidence_log: str | None, repo_root: Path) -> dict:
    if not evidence_log:
        raise _InitError(
            "--manual-verdict requires --evidence-log <path>",
            EXIT_VERDICT,
        )
    repo_resolved = repo_root.resolve()
    # Resolve evidence path relative to repo root if not absolute
    rel = Path(evidence_log)
    if rel.is_absolute():
        try:
            rel = rel.resolve().relative_to(repo_resolved)
        except ValueError as exc:
            raise _InitError(
                f"--evidence-log {evidence_log!r} is outside the repo at {repo_resolved}",
                EXIT_WORKTREE,
            ) from exc
    full = (repo_resolved / rel).resolve()
    if not str(full).startswith(str(repo_resolved)):
        raise _InitError(
            f"--evidence-log {evidence_log!r} resolves outside the repo ({full})",
            EXIT_WORKTREE,
        )
    if not full.exists():
        raise _InitError(
            f"--evidence-log {full} does not exist",
            EXIT_WORKTREE,
        )

    # Verify it is committed in the repo (at main repo HEAD).
    check = subprocess.run(
        ["git", "-C", str(repo_resolved), "ls-files", "--error-unmatch", str(rel)],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        raise _InitError(
            f"--evidence-log {rel} is NOT tracked by git; "
            f"commit it (and to each unit branch) before init; "
            f"R7a reads it at PR head — uncommitted files do not exist on the PR side",
            EXIT_WORKTREE,
        )

    content = full.read_bytes()
    return {
        "kind": "manual",
        "evidence_path": str(rel),
        "evidence_sha256": hashlib.sha256(content).hexdigest(),
    }


def _get_main_sha(repo_root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "origin/main"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # Fallback to local main
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "main"],
            capture_output=True, text=True,
        )
    sha = (proc.stdout or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise _InitError(
            f"could not resolve main_sha (origin/main or main); got {sha!r}",
            EXIT_MISUSE,
        )
    return sha


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post_push_verify(repo_root: Path, expected_bodies: dict[str, str]) -> None:
    """Fetch origin notes ref into a TEMP local ref; assert each note round-trips.

    Closes v2-review adversarial F1: a silent push failure (push exits 0
    but origin's ref did not advance) would otherwise produce
    "init succeeded → push succeeded → R7a rejected" surprise at PR open.
    """
    try:
        # Fetch into temp ref (does NOT touch the regular phase0-seal ref).
        fetch = subprocess.run(
            ["git", "-C", str(repo_root), "fetch", "origin",
             f"{_NOTES_REF}:{_NOTES_VERIFY_REF}"],
            capture_output=True, text=True,
        )
        if fetch.returncode != 0:
            raise _NotesPushDidNotLand(
                f"notes push to origin returned 0 but verify-fetch failed: "
                f"{fetch.stderr.strip() or '(empty stderr)'}; "
                f"the ref may not have actually advanced on origin"
            )

        for sha, expected in expected_bodies.items():
            show = subprocess.run(
                ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_VERIFY_REF}", "show", sha],
                capture_output=True, text=True,
            )
            if show.returncode != 0:
                raise _NotesPushDidNotLand(
                    f"note for {sha} is NOT present on origin after push "
                    f"(push exited 0 but verify-fetch can't read it); "
                    f"check ref-protection rules on refs/notes/phase0-seal and retry"
                )
            actual = (show.stdout or "").strip()
            if actual != expected:
                raise _NotesPushDidNotLand(
                    f"note body on origin for {sha} differs from what was written: "
                    f"actual={actual[:120]!r} expected={expected[:120]!r}"
                )
    finally:
        # Always clean up the temp ref, even on failure (best-effort).
        subprocess.run(
            ["git", "-C", str(repo_root), "update-ref", "-d", _NOTES_VERIFY_REF],
            capture_output=True, text=True,
        )


# ---------------------------------------------------------------------------
# Unit 4 helpers
# ---------------------------------------------------------------------------


def _read_all_seal_notes(repo_root: Path) -> list[tuple[str, dict]]:
    """Return [(object_sha, body_dict)] for every note in the phase0-seal namespace."""
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "list"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    results = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        _blob_sha, obj_sha = parts
        show = subprocess.run(
            ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "show", obj_sha],
            capture_output=True, text=True,
        )
        if show.returncode != 0:
            continue
        try:
            body = json.loads(show.stdout.strip())
        except json.JSONDecodeError:
            continue
        results.append((obj_sha, body))
    return results


def _get_nested(d: dict, dotted_key: str) -> object:
    """Get a value from a nested dict using a dotted key path.

    A trailing ``_short`` suffix on the last segment returns the first 12 chars
    of the value (used for sha256 display in markdown output).
    """
    short = dotted_key.endswith("_short")
    key = dotted_key[: -len("_short")] if short else dotted_key
    val: object = d
    for k in key.split("."):
        if not isinstance(val, dict):
            return None
        val = val.get(k)
        if val is None:
            return None
    if short and isinstance(val, str):
        return val[:12]
    return val


def _print_markdown_note(obj_sha: str, body: dict) -> None:
    unit = body.get("unit", "?")
    print(f"\n## {unit} — sealed SHA `{obj_sha[:12]}`\n")
    for field in V.MARKDOWN_FIELDS:
        val = _get_nested(body, field)
        if val is None:
            continue
        label = field.split(".")[-1].rstrip("_short").replace("_", " ")
        print(f"- **{label}**: `{val}`")


def _get_main_sha_safe(repo_root: Path) -> str | None:
    try:
        return _get_main_sha(repo_root)
    except _InitError:
        return None


def _handle_show(args: argparse.Namespace) -> int:
    repo_root = V.find_main_worktree_root()
    notes = _read_all_seal_notes(repo_root)
    if not notes:
        print("phase0-seal show: no seal notes found; run 'phase0-seal init' first", file=sys.stderr)
        return EXIT_MISUSE
    if args.unit:
        notes = [(sha, body) for sha, body in notes if body.get("unit") == args.unit]
        if not notes:
            print(f"phase0-seal show: no seal note found for unit {args.unit!r}", file=sys.stderr)
            return EXIT_MISUSE
    notes.sort(key=lambda x: x[1].get("unit", ""))
    for obj_sha, body in notes:
        if args.format == "json":
            print(json.dumps(body, indent=2, sort_keys=True))
        else:
            _print_markdown_note(obj_sha, body)
    return EXIT_OK


def _handle_verify(args: argparse.Namespace) -> int:
    repo_root = V.find_main_worktree_root()
    notes = _read_all_seal_notes(repo_root)
    if not notes:
        print("phase0-seal verify: no seal notes found; run 'phase0-seal init' first", file=sys.stderr)
        return EXIT_MISUSE
    entries = discover_worktree_heads(V.TELEGRAPH_BRANCH_PATTERN, repo_root=repo_root)
    current_by_branch: dict[str, WorktreeEntry] = {e.branch: e for e in entries}
    current_main = _get_main_sha_safe(repo_root)
    all_ok = True
    for obj_sha, body in sorted(notes, key=lambda x: x[1].get("unit", "")):
        unit = body.get("unit", "?")
        branch = body.get("branch", "?")
        sealed_main = body.get("main_sha", "?")
        current = current_by_branch.get(branch)
        if current is None:
            sha_sym, sha_detail = "?", f"no worktree for {branch!r}"
        elif current.sha == obj_sha:
            sha_sym, sha_detail = "OK", f"{obj_sha[:12]}"
        else:
            sha_sym, sha_detail = "DRIFT", f"{obj_sha[:12]} → {current.sha[:12]}"
            all_ok = False
        if current_main is None:
            main_sym, main_detail = "?", "could not resolve origin/main"
        elif current_main == sealed_main:
            main_sym, main_detail = "OK", f"{sealed_main[:12]}"
        else:
            main_sym, main_detail = "DRIFT", f"{sealed_main[:12]} → {current_main[:12]}"
            all_ok = False
        print(f"{unit}  unit-sha={sha_sym} ({sha_detail})  main={main_sym} ({main_detail})")
        if args.check_comment and body.get("verdict_ref", {}).get("kind") == "routine_comment":
            vref = body["verdict_ref"]
            comment_url = vref.get("comment_url", "")
            print(f"  --check-comment: re-fetching {comment_url!r}")
            try:
                allowlist = V.load_allowlist(repo_root)
                owner, repo_name, pr_num, comment_id = _parse_comment_url(comment_url)
                comment = V._run_gh(f"repos/{owner}/{repo_name}/issues/comments/{comment_id}")
                V.validate_verdict_comment(comment, expected_pr=pr_num, allowlist=allowlist)
                print(f"  --check-comment: verdict comment still valid")
            except Exception as exc:
                print(f"  --check-comment: FAIL — {exc}")
                all_ok = False
    return EXIT_OK if all_ok else EXIT_MISUSE


def _handle_reseal(args: argparse.Namespace) -> int:
    repo_root = V.find_main_worktree_root()
    notes = _read_all_seal_notes(repo_root)
    if not notes:
        print("phase0-seal reseal: no seal notes found; run 'phase0-seal init' first", file=sys.stderr)
        return EXIT_MISUSE
    entries = discover_worktree_heads(V.TELEGRAPH_BRANCH_PATTERN, repo_root=repo_root)
    current_by_branch: dict[str, WorktreeEntry] = {e.branch: e for e in entries}
    new_main = _get_main_sha_safe(repo_root)
    resealed_at = _now_iso()
    migrations: list[tuple[str, str, str]] = []  # (old_sha, new_sha, new_body_json)
    for old_sha, body in notes:
        branch = body.get("branch", "")
        current = current_by_branch.get(branch)
        if current is None:
            print(f"phase0-seal reseal: no current worktree for {branch!r} — skipping", file=sys.stderr)
            continue
        new_body = {
            **body,
            "main_sha": new_main or body["main_sha"],
            "last_resealed_at": resealed_at,
            "sealed_by": "operator:reseal",
            # verdict_ref and sealed_at intentionally preserved via **body
        }
        V.validate_seal_schema(new_body)
        migrations.append((old_sha, current.sha, json.dumps(new_body, sort_keys=True, separators=(",", ":"))))
    if not migrations:
        print("phase0-seal reseal: nothing to reseal (no matching worktrees)", file=sys.stderr)
        return EXIT_OK
    if not args.yes:
        print("phase0-seal reseal: about to reseal:", file=sys.stderr)
        for old, new, _ in migrations:
            label = f"{old[:12]} → {new[:12]}" if old != new else f"{old[:12]} (same SHA)"
            print(f"  {label}", file=sys.stderr)
        try:
            resp = input("Continue? [y/N]: ")
        except EOFError:
            resp = ""
        if resp.strip().lower() not in ("y", "yes"):
            print("phase0-seal reseal: cancelled", file=sys.stderr)
            return EXIT_OK
    for old_sha, new_sha, new_body in migrations:
        if old_sha == new_sha:
            proc = subprocess.run(
                ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "add", "-f", "-m", new_body, new_sha],
                capture_output=True, text=True,
            )
        else:
            proc = subprocess.run(
                ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "add", "-m", new_body, new_sha],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                print(f"phase0-seal reseal: failed to add note at {new_sha}: {proc.stderr.strip()}", file=sys.stderr)
                return EXIT_MISUSE
            subprocess.run(
                ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "remove", old_sha],
                capture_output=True, text=True,
            )
    push = subprocess.run(
        ["git", "-C", str(repo_root), "push", "origin", f"{_NOTES_REF}:{_NOTES_REF}"],
        capture_output=True, text=True,
    )
    if push.returncode != 0:
        print(f"phase0-seal reseal: push failed: {push.stderr.strip()}", file=sys.stderr)
        return EXIT_MISUSE
    print(f"phase0-seal reseal: resealed {len(migrations)} unit(s); last_resealed_at={resealed_at}", file=sys.stderr)
    return EXIT_OK


def _handle_verify_hook(args: argparse.Namespace) -> int:
    raise NotImplementedError("phase0-seal verify-hook: not yet implemented (lands in Unit 5)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Argparse dispatcher.

    Returns an exit code rather than calling sys.exit() so tests can call
    main() in-process and inspect the return value.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args) or EXIT_OK
    except NotImplementedError as exc:
        print("phase0-seal: {exc}", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
