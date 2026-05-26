"""PipelineAPI — structured wrapper around plan/validate/publish CLI invocations.

Phase A: still delegates to ``run_pipe`` (subprocess).  Phase B will replace
the subprocess bridge with in-process ``main(argv)`` calls.

Every method returns a ``PipeResult`` so callers never touch raw ``run_pipe``
or parse JSONL inline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..helpers.cli_runner import run_pipe, strip_cli_diagnostic_banner


# ── structured result ──────────────────────────────────────────────────────


@dataclass
class PipeResult:
    """Structured result from a single pipeline CLI invocation.

    Callers interact with ``.success`` / ``.error`` / ``.rows`` instead of
    raw stdout / stderr strings.
    """

    stdout: str = ""
    stderr: str = ""
    success: bool = True
    error: str | None = None

    # ── derived helpers ──────────────────────────────────────────────────

    @property
    def stderr_cleaned(self) -> str:
        """Stderr with the config-echo diagnostic banner stripped."""
        return strip_cli_diagnostic_banner(self.stderr)

    @property
    def rows(self) -> list[dict[str, Any]]:
        """Parse stdout as JSONL into a list of dict rows.

        Returns ``[]`` when stdout is empty or unparseable — caller checks
        ``.success`` first.
        """
        if not self.stdout:
            return []
        result: list[dict[str, Any]] = []
        for line in self.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return result


# ── helpers used by both PipelineAPI and external callers (scheduler) ──────


def parse_publish_results(jsonl_str: str) -> list[dict[str, Any]]:
    """Parse publish-backlinks JSONL stdout into result rows.

    Duplicate of ``helpers.history._parse_publish_results`` — consolidated
    here so the scheduler and routes share one canonical parser.
    """
    results: list[dict[str, Any]] = []
    for line in (jsonl_str or "").strip().split("\n"):
        if line.strip():
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return results


def publish_state_summary(
    publish_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute aggregate publish state from per-row results.

    Returns ``{"n_ok", "n_failed", "state"}`` where *state* is one of
    ``"all_success"``, ``"all_failed"``, ``"partial_success"``.
    """
    n_ok = sum(
        1 for r in publish_results
        if (r.get("published_url") or "").strip()
        or (r.get("draft_url") or "").strip()
    )
    n_failed = len(publish_results) - n_ok

    if n_failed == 0:
        state = "all_success"
    elif n_ok == 0:
        state = "all_failed"
    else:
        state = "partial_success"

    failure_msgs = [
        (r.get("error") or "").strip() or f"{r.get('status') or 'failed'} (no URL)"
        for r in publish_results
        if not ((r.get("published_url") or "").strip()
                or (r.get("draft_url") or "").strip())
    ]

    return {
        "n_ok": n_ok,
        "n_failed": n_failed,
        "state": state,
        "failure_detail": "；".join(m for m in failure_msgs if m),
    }


# ── PipelineAPI ────────────────────────────────────────────────────────────


class PipelineAPI:
    """Encapsulates the three pipeline stage invocations.

    Usage::

        api = PipelineAPI()
        result = api.plan(seed_json)
        if result.success:
            plans = result.rows
    """

    # ── plan ─────────────────────────────────────────────────────────────

    def plan(self, seed_json: str) -> PipeResult:
        """Run ``plan-backlinks`` with the given JSONL seed data."""
        try:
            raw = run_pipe(["plan-backlinks"], seed_json)
            return PipeResult(
                stdout=raw["stdout"],
                stderr=raw.get("stderr", ""),
                success=True,
            )
        except Exception as exc:
            stderr = str(exc)
            return PipeResult(
                stderr=stderr,
                success=False,
                error=strip_cli_diagnostic_banner(stderr) or "plan-backlinks failed",
            )

    # ── validate ─────────────────────────────────────────────────────────

    def validate(
        self,
        plans_jsonl: str,
        *,
        no_check_urls: bool = True,
    ) -> PipeResult:
        """Run ``validate-backlinks`` with optional ``--no-check-urls``."""
        cmd = ["validate-backlinks"]
        if no_check_urls:
            cmd.append("--no-check-urls")
        try:
            raw = run_pipe(cmd, plans_jsonl)
            return PipeResult(
                stdout=raw["stdout"],
                stderr=raw.get("stderr", ""),
                success=True,
            )
        except Exception as exc:
            stderr = str(exc)
            return PipeResult(
                stderr=stderr,
                success=False,
                error=strip_cli_diagnostic_banner(stderr) or "validate-backlinks failed",
            )

    # ── publish ──────────────────────────────────────────────────────────

    def publish(
        self,
        plans_jsonl: str,
        platform: str,
        mode: str,
    ) -> PipeResult:
        """Run ``publish-backlinks --platform <p> --mode <m>``."""
        cmd = ["publish-backlinks", "--platform", platform, "--mode", mode]
        try:
            raw = run_pipe(cmd, plans_jsonl)
            return PipeResult(
                stdout=raw["stdout"],
                stderr=raw.get("stderr", ""),
                success=True,
            )
        except Exception as exc:
            stderr = str(exc)
            return PipeResult(
                stderr=stderr,
                success=False,
                error=strip_cli_diagnostic_banner(stderr) or "publish-backlinks failed",
            )
