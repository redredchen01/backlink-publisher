---
title: "Tests silently coupled to the operator's local config state route to the wrong code path"
date: 2026-05-18
category: test-failures
module: backlink-publisher / testing-discipline
problem_type: test_failure
component: testing_framework
symptoms:
  - "`json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)` from a `json.loads(stdout.strip())` call in an in-process CLI test"
  - "The test asserts `code == 0` and passes that assertion, then fails on the JSON decode — `main()` exited cleanly but wrote nothing to stdout"
  - "Re-running the same CLI manually outside pytest produces a fully-populated payload (1KB+ of JSON) — bug only reproduces under pytest"
  - "Test reaches green/red status based on which domains are present in `~/.config/backlink-publisher/config.toml` on the operator's machine — passes on one developer's box, fails on another's"
  - "Test was green when written, then turned red months later without any code change to the test or the SUT it claims to cover"
root_cause: test_isolation
resolution_type: test_fix
severity: medium
related_components:
  - "src/backlink_publisher/cli/plan_backlinks.py (`_dispatch_row`)"
  - "src/backlink_publisher/config.py (`_config_dir`, `_cache_dir`, `load_config`)"
  - "tests/conftest.py (autouse fixtures, especially `_mock_content_fetch`)"
  - "tests/test_plan_backlinks.py::test_plan_no_synthesized_categories_url_without_config"
related_prs:
  - "#40 — fix(tests): pytest bug sweep 2026-05-18 (specific test fix)"
  - "#43 — feat(config): env-var overrides + session-scope test isolation (root-class fix)"
  - "#44 — refactor(conftest): real_content_fetch marker replaces filename string-match (sibling cleanup)"
tags:
  - test-isolation
  - config-coupling
  - operator-state-bleed
  - dispatcher-routing
  - pytest-fixtures
  - root-cause-class
---

# Tests silently coupled to operator config state

## The bug

`tests/test_plan_backlinks.py::test_plan_no_synthesized_categories_url_without_config` seeded `main_domain="https://51acgs.com/"` and ran the CLI in-process. The CLI loaded the **operator's real** `~/.config/backlink-publisher/config.toml`, which on this developer's machine contained:

```toml
[targets."https://51acgs.com"]
anchor_keywords = ["51漫畫", ...]
main_url = "https://51acgs.com/"
list_url = "https://51acgs.com/comic"
work_urls = ["https://51acgs.com/comic/5"]
```

`_dispatch_row` checks `get_three_url_config(config, row["main_domain"])` first, sees the configured `[targets."https://51acgs.com"]`, and routes to `_plan_work_themed_row` — the work-themed dispatcher. The test was written assuming the seed would go through `_build_links` (the long-form path), but the routing diverged the moment the operator added a `[targets.*]` entry for that domain.

`_plan_work_themed_row` then called `work_scraper.fetch_work_metadata` for each `work_url`. The conftest autouse `_mock_content_fetch` mocks `verify_urls_batch` / `verify_url_has_content`, but **not** `work_scraper`. With `pytest-socket` disabled, every `work_scraper` HTTP call took the fail-continue path, all `work_urls` were skipped, `outputs` ended empty, and `main()` returned normally. `write_jsonl([])` is a no-op → empty stdout → `json.loads("")` → `JSONDecodeError`.

The test reported `code == 0` (correct — `main()` *did* exit cleanly) and then died on JSON decode, with no signal that pointed at the routing divergence.

## Why this is dangerous as a pattern, not just a one-off bug

The failure mode is environmentally polymorphic:

- **Bug present on developer A's machine, absent on developer B's machine** — depends on whether the operator's `config.toml` has a `[targets."<domain>"]` entry matching the test's seed.
- **Bug presents differently across pytest invocations** — same machine, same code, but the operator might add or remove `[targets.*]` entries between runs.
- **Bug bleeds into PRs only when the operator's local state drifts** — the test passes green on the author's machine, gets merged, then turns red on a reviewer's machine.
- **Symptom is misleading**: `JSONDecodeError` in test infrastructure code, not in any SUT code. The traceback head is in `json/decoder.py`, not in `plan_backlinks.py`. Standard "look where it crashed" debugging points at the wrong place.

The same trap applies any time a test:

1. Reads a configuration file from a default path under `~/.config/` or `~/.cache/`
2. Exercises code that branches on the contents of that configuration
3. Doesn't isolate the config dir explicitly

In this codebase, that's any test that calls into `plan_backlinks.main()`, `publish_backlinks.main()`, or any other CLI entry point — they all call `load_config()` with no path argument.

## How to spot it

**Audit recipe**:

```bash
# Tests that seed real domains the operator might have in their config
rg -n 'main_domain.*51acgs|target_url.*51acgs|taiwanmanga2026|blogger\.com|medium\.com' tests/

# Tests that call CLI entrypoints in-process (will load operator config)
rg -nA2 'from backlink_publisher\.cli\.\w+ import main' tests/

# Tests that don't override or isolate config dir
rg -n 'load_config\(' tests/ | rg -v 'tmp_path|monkeypatch\.setenv.*CONFIG'
```

**Pre-merge diagnostic**: if a test calls a CLI entrypoint and the test isn't currently passing on a freshly-installed machine with empty `~/.config/backlink-publisher/`, the test is environmentally coupled. Run the suite once with `BACKLINK_PUBLISHER_CONFIG_DIR=$(mktemp -d) pytest tests/` to verify isolation.

## How to fix it — defense in depth

The fix is structural, not per-test. Three layers, each independently useful:

### Layer 1: Per-test fix (cheapest, most narrow)

Change the test's seed domain to one guaranteed absent from any reasonable `[targets.*]` table. `https://example.com/` is the canonical choice (RFC 2606 reserves it for examples). PR #40 took this path for the single failing test, classified the change as **contract-evolution** in the bug-sweep plan's 4-category test-modification gate, and shipped it with the originating commit linked.

This is **defense-in-depth only** — it does not prevent future tests from making the same mistake.

### Layer 2: Source-side env-var override (PR #43)

Add an environment variable override to the dir-resolution functions in `config.py`:

```python
def _config_dir() -> Path:
    override = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
    if override:
        return Path(override)
    # ... existing platform-default logic
```

Mirror for `_cache_dir` with `BACKLINK_PUBLISHER_CACHE_DIR`. The override is opt-in — empty string falls back to platform default (defends against `Path("")` resolving to CWD). Three regression tests lock the contract.

The env-var override is independently valuable for CI, containers, and ops who want to point at non-default config locations.

### Layer 3: Session-scope conftest isolation (PR #43)

Once the override exists, a session-scope autouse fixture in `tests/conftest.py` sets both env vars to fresh `tmp_path_factory.mktemp(...)` dirs for the whole pytest session:

```python
@pytest.fixture(scope="session", autouse=True)
def _isolate_user_dirs(tmp_path_factory):
    config_dir = tmp_path_factory.mktemp("bp-config-isolated")
    cache_dir = tmp_path_factory.mktemp("bp-cache-isolated")
    previous_config = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
    previous_cache = os.environ.get("BACKLINK_PUBLISHER_CACHE_DIR")
    os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(config_dir)
    os.environ["BACKLINK_PUBLISHER_CACHE_DIR"] = str(cache_dir)
    yield
    # restore
```

After this fixture lands, **no test can be silently coupled to operator state** — every test session sees an empty config, regardless of what the operator has on disk. Tests that *need* a populated config write into the pointed-at directory via `save_config(path=...)` or override the env var with `monkeypatch.setenv`.

This is the root-class fix. Layers 1 and 2 become defense-in-depth.

## The conftest filename string-match anti-pattern (sibling cleanup)

While auditing the autouse mocks, the conftest's `_mock_content_fetch` also had a structural hazard:

```python
test_path = str(request.node.fspath)
if "test_content_fetch.py" in test_path:
    return
```

This worked but would silently re-engage the default-pass mock if `test_content_fetch.py` were ever renamed or split — turning the file's `urlopen`-mocked assertions into mock-against-mock assertions that pass for the wrong reason. PR #44 migrated this to a pytest marker (`real_content_fetch`) mirroring the existing `real_ssrf_check` opt-in pattern. Module-level `pytestmark = pytest.mark.real_content_fetch` applies to all 56 tests in the file. Marker survives file rename and is declarative.

## Resolution summary

| Layer | What | PR | Effect |
|---|---|---|---|
| 1 | Single-test domain swap (`51acgs.com` → `example.com`) | [#40](https://github.com/redredchen01/backlink-publisher/pull/40) | Fixes the one immediately-failing test. Test classified as `contract-evolution` in the bug-sweep gate. |
| 2 | `BACKLINK_PUBLISHER_{CONFIG,CACHE}_DIR` env-var overrides in `config.py` | [#43](https://github.com/redredchen01/backlink-publisher/pull/43) | Enables isolation without source changes anywhere else. Also useful for containers/CI. |
| 3 | Session-scope `_isolate_user_dirs` autouse fixture | [#43](https://github.com/redredchen01/backlink-publisher/pull/43) | Forecloses the bug class entirely. No test, present or future, can be coupled to operator state. |
| Sibling | Filename string-match → `real_content_fetch` marker | [#44](https://github.com/redredchen01/backlink-publisher/pull/44) | Removes the parallel hazard in `_mock_content_fetch`. |

## Take-aways

- **Tests that call CLI entrypoints in-process must isolate the config dir.** Whether via env var, `monkeypatch.setenv`, or fixture, the in-process call shares the parent's environment. There is no shell boundary to filter through.
- **`code == 0` plus empty stdout is a routing-divergence smell**, not a real success. If a test expects structured output and gets none, the SUT silently took a code path the test didn't anticipate — check dispatchers, gates, and "fail-continue" branches before assuming the assertion logic is wrong.
- **`request.node.fspath` string-matching for special-case test behavior is a rename trap.** Use a pytest marker instead — declarative, survives refactors, and registers a contract in `pyproject.toml`.
- **Per-test fixes are defense in depth, not the actual fix.** When a test fails because of operator state, the right fix is structural isolation, with the per-test fix as a backstop in case isolation breaks later.

## Related learnings

- `docs/solutions/test-failures/del-os-environ-poisons-session-scoped-config-dir-fixture-2026-05-27.md` — the inverse failure: a test that *breaks* the session-scoped isolation fixture introduced here by `del os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"]`, unsetting it for every later test and re-exposing this exact fallback-to-`~/.config` bug class (PR #259).
- `docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md` — sibling test-isolation pattern (macOS Brave running silently makes `MediumBraveAdapter.publish()` execute for real). Same family: tests implicitly coupled to operator/machine state.
- `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — when fixing a test surfaces another red, default to fixing the source, not deleting the test. This bug's fix briefly tripped that question for the test-mod gate; classified as `contract-evolution` (4th category added to the gate in the bug-sweep plan).
- `feedback_verify_repo_state_before_planning` (private memory) — surfaced during this sweep's planning: I claimed several SHAs/PR statuses from stale memory; document-review caught them. Lesson: even read-only repo state must be re-verified at plan time.
