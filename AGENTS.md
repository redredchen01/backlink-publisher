# AGENTS.md — backlink-publisher

See `README.md` for project overview and `docs/` for plans, brainstorms, ideation, and solutions.

## Lessons capture (dual-track)

The project keeps lessons in two places:

- **Private auto-memory** — Claude Code automatically writes `feedback_*.md` files at `~/.claude/projects/<project-memory-slug>/memory/` during sessions. These are fast-capture, operator-private, and never committed.
- **Public `docs/solutions/`** — High-value or recurring lessons get *promoted* into committed markdown entries under `docs/solutions/<category>/` (categories: `best-practices/`, `logic-errors/`, `test-failures/`, `ui-bugs/`). The promotion tool is `/ce:compound` (a Claude Code skill from the `compound-engineering` plugin — see plugin docs); it generates the frontmatter schema each existing entry uses.

**Promotion is rewriting, not copy-paste. Strip session UUIDs, real domains, absolute paths, and user-identifying quotes; teach the pattern, not the incident.** The grep gates in `docs/plans/2026-05-15-001-refactor-lessons-kit-curation-plan.md` (Unit 5) are the safety net; the gitignored token file at `~/.local/share/backlink-publisher/private-tokens.txt` enumerates what to scrub.

**First-time setup** (per-operator; the token file is local-only and never shared): see `docs/plans/2026-05-15-001-refactor-lessons-kit-curation-plan.md` Unit 1.5 for the bootstrap recipe. A new contributor must populate `~/.local/share/backlink-publisher/private-tokens.txt` with their operator-private patterns (real target domains, operator email, run-ID patterns) before running `/ce:compound`, or the grep gates will vacuously pass against an empty pattern file.

Next curation review: **2026-08-15** — *aspirational quarterly cadence; not enforced by CI or any tool*. This file is static markdown; the actual trigger is "next time `/ce:compound` or `/ce:plan` runs in this repo, scan recent `feedback_*.md` and decide what's worth promoting." Update this date when the review completes; treat skipping a quarter as a soft signal, not a failure.

Soft observation (2026-05-15): historical `docs/brainstorms/` and `docs/plans/` files contain real operator domain references (e.g. target hostnames). The sanitization rule above applies to `docs/solutions/` entries; if the project ever needs to extend it to historical decision artifacts, scope a separate pass — do not retrofit silently.

## Worktree Auto-Cleanup

Sibling `bp-<topic>/` git worktrees accumulate after parallel feature work — even with discipline, fresh clones and concurrent agent sessions reintroduce sprawl. Two scripts manage cleanup:

- **`bash scripts/prune-stale-worktrees.sh`** — interactive helper. Lists worktrees whose branch tip is reachable from `origin/main` (handles squash-merge via `gh pr list` when available; falls back to direct `git merge-base --is-ancestor` otherwise). Skips dirty worktrees and the main worktree. Flags: `--dry-run` (list only), `--force` (cron-safe, no prompts), `--help`.
- **`bash scripts/install-post-merge-hook.sh`** — per-clone installer that writes a `post-merge` hook to `.git/hooks/`. The hook fires after `git merge` / `git pull` on `main` and **notifies by default** about stale worktrees. To enable auto-removal after the hook's dirty-state check, set `export BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1` in your shell rc. Re-run the installer after fresh clones (git hooks are not committed).

Safety: both refuse to remove the worktree the script is running in, both honor the dirty-state guard (no force-remove of uncommitted work), and the prune helper exits 2 if any removal fails so cron-style invocations can alert. Coverage: `tests/scripts/test_prune_stale_worktrees.py`.

## Monolith Budget

`monolith_budget.toml` at repo root tracks radon SLOC ceilings for five named source files: `src/backlink_publisher/cli/plan_backlinks.py`, `src/backlink_publisher/cli/publish_backlinks.py`, `src/backlink_publisher/content/fetch.py`, `src/backlink_publisher/config/writer.py`, `src/backlink_publisher/_util/markdown.py`. Enforced by `tests/test_no_monolith_regrowth.py` (hard-fail R4 + warning canary R7 + radon counter pinning).

**When to edit:** if your PR pushes a monitored file's SLOC past its `ceiling`, the test fails. Edit `monolith_budget.toml` in the same PR — raise the ceiling and rewrite the `rationale` to explain what motivated the growth and the shape this file is expected to settle to over the next few sprints (the rationale field must be ≥80 chars).

**Journal, not gate.** A solo developer can rubber-stamp any bump — the defense is `git blame` on `monolith_budget.toml`. Every intentional bump leaves a reviewable record. There is no override label and no warning-only mode for the primary check.

**F7 does not decompose anything.** The surgical extraction plans (F2 `ErrorClass` oracle, F3 `safe_write` carve from `config/writer.py`, F5 `ThrottleClock`) are separate work. F7 only prevents regrowth after such carves land.

**Bumping `radon` is treated as a budget edit** (pinned exactly in `pyproject.toml`'s `[project.optional-dependencies].dev`). The bump PR must re-measure all five ceilings via `python -m radon raw -s <paths>` and update the SLOC canary fixture's `SLOC_CANARY_EXPECTED` in the test file.

**Recommended branch protection on `main`:** enable "Require branches to be up to date before merging." Protects against two concurrent PRs each bumping the same file's ceiling and producing a post-merge state that fails R4. The existing `push: branches: [main]` CI lane catches violations post-merge regardless, but pre-merge prevention is cheaper than a revert under pressure.

References: `docs/plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md`, `docs/brainstorms/2026-05-18-monolith-loc-ceiling-requirements.md`.

## Adding a new publisher adapter

Post-R9 (plan `docs/plans/2026-05-18-009-refactor-cli-extension-readiness-plan.md`), a new platform is one `register("x", XAdapter)` call away from reaching both the CLI argparse layer and `schema.validate_publish_payload`. The dispatcher, schema enum, throttle gating, and LinkedIn-style rejection all read from `publishing.registry.registered_platforms()` — you do not edit any CLI file or `schema.py` to add a new platform.

The five-step recipe below builds on `BloggerAPIAdapter` as a concrete reference at every step.

### 1. Subclass `Publisher`

Reference: `src/backlink_publisher/publishing/adapters/blogger_api.py::BloggerAPIAdapter`.

```python
# src/backlink_publisher/publishing/adapters/yourplatform.py
from typing import Any

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult


class YourPlatformAdapter(Publisher):
    @classmethod
    def available(cls, config: Config) -> bool:
        # Return False to skip this adapter in the dispatch chain. Use for
        # macOS-only adapters, feature flags, license checks. Default True.
        return True

    def publish(self, payload: dict[str, Any], mode: str, config: Config) -> AdapterResult:
        ...
```

### 2. Implement `publish()`

Mirror the structure of `BloggerAPIAdapter.publish()`:

- Read the row fields you need from `payload` (`title`, `content_markdown` / `content_html`, `tags`, `main_domain`).
- Call `extract_publish_html(payload, "yourplatform")` from `publishing.content_negotiation` to get the platform-appropriate body. The tier table (`ROUTE_TIER_MATRIX`) defaults to `"c"` (fail-closed) for unknown platforms — add an entry only when you have an XSS contract test in place (see step 6).
- Wrap remote calls in `retry_transient_call` from `.retry` to inherit the 429/5xx backoff.
- Return an `AdapterResult(status="drafted"|"published", adapter="yourplatform-api", platform="yourplatform", draft_url=..., published_url=...)`. If your platform needs a post-publish throttle (rate-limit avoidance), set `post_publish_delay_seconds=N` on the result — the CLI's verify-poll window and inter-row throttle key off this field instead of a hardcoded platform name (plan 2026-05-18-009 R9c).

Error contract (preserved across the dispatch chain):

- Raise `DependencyError` for missing prerequisites (no token, no browser, no AppleScript host) — the dispatcher will try the next adapter registered for the same platform.
- Raise `ExternalServiceError` for remote failures (401, 429, 5xx, network) — it propagates immediately, no fallthrough.

### 3. Register

Add one line to `src/backlink_publisher/publishing/adapters/__init__.py`:

```python
from .yourplatform import YourPlatformAdapter

register("yourplatform", YourPlatformAdapter)
```

This is the **only** registration point. Post-R9 you do not edit:

- `cli/publish_backlinks.py` argparse `choices=` (reads `registered_platforms()` dynamically).
- `cli/plan_backlinks.py` `--default-platform` choices.
- `cli/validate_backlinks.py` unsupported-platform rejection.
- `schema.py` `supported_platforms()` or `reject_unsupported_platform()`.

If your platform has a fallback chain (like Medium's `MediumAPIAdapter → MediumBraveAdapter → MediumBrowserAdapter`), pass all classes in one call: `register("yourplatform", PrimaryAdapter, FallbackAdapter1, FallbackAdapter2)`. Order matters — `available()=False` skips an entry, `DependencyError` falls through, `ExternalServiceError` propagates.

### 4. Add config (if needed)

Reference: `src/backlink_publisher/config/types.py::BloggerOAuthConfig` (the dataclass) and `Config.blogger_blog_ids` (line 131, the per-blog map).

If your platform needs persistent credentials or per-blog config, follow the Blogger pattern:

- Add a frozen dataclass for the auth bundle (`@dataclass(frozen=True) class YourPlatformOAuthConfig: client_id: str; client_secret: str`).
- Add a field to `Config` (`yourplatform_oauth: YourPlatformOAuthConfig | None = None`).
- Add the TOML key to `config.example.toml` and the loader path in `config/loader.py`.
- Add `load_yourplatform_token` / `save_yourplatform_token` helpers mirroring `load_blogger_token` if you need a separate token cache file.

### 5. Add an optional dependency (if needed)

Reference: `pyproject.toml` `[project.optional-dependencies].dev` block (line 23-25).

If your platform's SDK adds dependencies that not every operator needs, declare them as an extra:

```toml
[project.optional-dependencies]
yourplatform = ["yourplatform-sdk>=2.0"]
```

Document the install incantation in the adapter docstring and in `README.md` under "Prerequisites". Operators run `pip install -e .[yourplatform]` to opt in.

**Escalation path for resolver conflicts:** if your platform's SDK pins a dependency that conflicts with the base `[project.dependencies]` block, do not split the package into `core/` + `packages/` reactively — the registry already isolates the dispatch contract, and the schema layer reads from it. Open an issue with the conflict trace first so we can evaluate either pinning the conflicting dep in `dev` extras or, only if no other path exists, a real package split.

### 6. Add a test

Reference: `tests/test_adapter_blogger_api.py` (unit) and `tests/test_adapter_blogger_api_xss_contract.py` (XSS contract — required if you add a `ROUTE_TIER_MATRIX` entry at tier `"a"` so the adapter forwards `content_html` verbatim).

Minimum coverage for a new adapter:

- One happy-path test that mocks the SDK / HTTP boundary and asserts on the returned `AdapterResult` (status, `adapter`, `platform`, `draft_url`/`published_url`, and `post_publish_delay_seconds` if your platform sets one).
- One test that raises `DependencyError` from the SDK and asserts the dispatcher would try the next adapter (or returns the error cleanly when no fallback exists).
- One test that raises `ExternalServiceError` from the SDK and asserts propagation (not fallthrough).
- If you add a `ROUTE_TIER_MATRIX["yourplatform"] = "a"` entry, you owe an XSS contract test: a malicious `content_html` payload reaches the adapter unchanged (the adapter is a forwarder; sanitize is the remote service's job).

The R9 falsifiable acceptance proof in `tests/test_r9_extension_readiness.py` already exercises the cross-layer wiring (argparse + schema + `supported_platforms` + `reject_unsupported_platform`) for a fixture-scoped `register("fake", FakeAdapter)`. You do not need to repeat that test for your platform — registering is sufficient to inherit the proof.

### PR description checklist

When opening the PR for your new adapter, include:

- [ ] Adapter file under `src/backlink_publisher/publishing/adapters/`
- [ ] One-line `register(...)` added to `adapters/__init__.py`
- [ ] Config dataclass / loader / TOML example updated (if your platform needs config)
- [ ] `pyproject.toml` optional-dependency entry (if your platform's SDK is heavyweight)
- [ ] At least 3 adapter tests (happy / `DependencyError` / `ExternalServiceError`)
- [ ] XSS contract test if you added a tier-`"a"` `ROUTE_TIER_MATRIX` entry
- [ ] `README.md` Prerequisites section updated (if the install instructions changed)
- [ ] Did **not** edit any CLI file or `schema.py` — confirm via `git diff --stat src/backlink_publisher/cli/ src/backlink_publisher/schema.py` is empty

Related: `docs/plans/2026-05-18-009-refactor-cli-extension-readiness-plan.md` (the R9 plan that made this recipe possible), `src/backlink_publisher/publishing/registry.py` (the `Publisher` ABC and dispatcher).

## Binding a channel

Browser-based credential binding is **orthogonal** to publisher adapters. Adding a new publish-platform follows the recipe above; teaching the platform's credential lifecycle to the operator-facing surface follows this section. Plan: `docs/plans/2026-05-19-001-feat-settings-browser-binding-plan.md`.

### Channels

The closed set lives in one place: `src/backlink_publisher/cli/_bind/channels/__init__.py::CHANNELS = frozenset({"velog", "medium", "blogger"})`. Every entry point (CLI argparse, webui routes, `AuthExpiredError` ctor, `mark_bound` / `mark_expired`) imports from there and validates membership before constructing paths or argv — defense in depth against `channel=../traversal` injection. Adding a fourth channel means: (1) extend `CHANNELS`; (2) ship its `ChannelRecipe` in `src/backlink_publisher/cli/_bind/recipes/<name>.py`; (3) update the CLI argparse `--channel` choices (auto-derived from `CHANNELS` already).

### Entry points

- `bind-channel --channel <velog|medium|blogger>` — single binding CLI, drives a headed Playwright session, emits RECON events on stdout as JSONL, writes `<config_dir>/<channel>-storage-state.json` with mode `0600`.
- `velog-login` — transparent alias for `bind-channel --channel velog`. Honored for backwards compatibility with plan-012. Prints an alias banner to stderr; otherwise identical.

Storage state always lands inside `BACKLINK_PUBLISHER_CONFIG_DIR` (defaults to `~/.config/backlink-publisher/`). The driver writes to a temp file then `os.rename`s — partial writes never leave a half-bound file. `mark_bound` happens after the rename so a kill in between leaves the file but keeps the status as `unbound` / `expired` (next click re-binds idempotently).

### Settings UI flow

`GET /settings` shows each channel card with a binding subsection (rendered from `webui_app/templates/_settings_channel_binding.html`):

- **Badge states** (rendered via `role="status" aria-live="polite"`):
  - `已绑定 ✓` — last `mark_bound` succeeded and the storage_state file still exists on disk.
  - `已过期 ⚠` — adapter raised `AuthExpiredError` at publish time, **or** `reconcile_on_load` found the storage_state file missing on app start.
  - `未绑定` — no record in `channel-status.json`.
  - `绑定中…` — JS poller saw `status: "running"` from `GET /settings/channels/<channel>/bind/<job_id>`.
- **Re-bind button** issues `POST /settings/channels/<channel>/bind` with the page CSRF token; both routes are loopback-only (`Blueprint.before_request` rejects non-`127.0.0.1`/`::1` with 403). The button writes `sessionStorage["bind:lastChannel"]` so a page reload re-opens the same card.
- **Failed binds** map their `error_code` to a Chinese operator message via `webui_app.services.bind_job.BIND_ERROR_MESSAGES` — adding a new `error_code` requires a Chinese mapping (the `tests/test_bind_error_messages.py` gate enforces this).

### Publish-time auth flip

When a publish adapter hits a 401/403 it raises `AuthExpiredError(channel="...", reason="...")` (the ctor revalidates `channel ∈ CHANNELS`). The `publish_backlinks` dispatch site catches this **before** the generic `except DependencyError`, calls `webui_store.channel_status.mark_expired(exc.channel)`, writes a checkpoint row with `error_class="auth_expired"`, then exits with code 3. Because `AuthExpiredError` inherits from `DependencyError`, callers that still `except DependencyError` keep working — they just lose the channel-specific side effects.

### Operator script — "how do I re-bind Medium?"

1. Open the WebUI (`webui` or `python webui.py`).
2. Navigate to `/settings`, expand the Medium card.
3. Click **重新绑定**. A headed Chromium window opens; complete the Medium login.
4. The badge transitions `绑定中…` → `已绑定 ✓`. The card stays open after the page reload thanks to `sessionStorage["bind:lastChannel"]`.

Alternative CLI path: `bind-channel --channel medium` (then complete login in the headed browser).

### What about Velog?

Velog is the **adapter** in plan-012 but its **credential lifecycle** lives here. plan-012 originally specified a standalone `velog-login` CLI and a `DependencyError("velog cookie expired")` raise on auth failure; plan 2026-05-19-001 unified that with the cross-channel surface. See the inline amendment in plan-012 (Unit 3 + Unit 4) for the exact contract changes.
