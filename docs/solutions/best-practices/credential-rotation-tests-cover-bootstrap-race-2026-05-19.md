---
title: "Test-first credential rotation must enumerate every state-mutation site — rotation, bootstrap, migration"
date: 2026-05-19
category: docs/solutions/best-practices
module: cli/_bind/recipes + auth state machines
problem_type: best_practice
component: authentication
severity: high
applies_when:
  - "Implementing a credential rotation flow (401 → refresh → retry)"
  - "Adding a `Barrier`-based concurrency test for credential state mutation"
  - "Reviewing a PR that introduces credential storage / rotation logic"
related_components:
  - background_job
  - testing_framework
tags:
  - credential-rotation
  - threading-barrier
  - bootstrap-race
  - state-mutation
  - test-first
  - 401-retry
---

# Credential rotation tests must enumerate every state-mutation site

## Context

A common plan shape for credential rotation is "test-first for the 401 → refresh → retry race." The plan writes a `threading.Barrier`-based concurrency test for the rotation path, the implementation passes, the rotation race is closed.

The trap: **bootstrap and migration are the same class of race**, and plans that focus on rotation often forget them. Bootstrap (first-time credential write) and migration (credential format change) both mutate the same on-disk state under the same potential concurrent-access conditions. If the rotation path is hardened and the bootstrap path is not, the operator hits the unfixed race on the very first run and the rotation work was for nothing.

PR #77 (Telegraph channel, plan 2026-05-19-002) shipped a rotation race test but missed the bootstrap race; a separate fix was needed to cover the same threading hazard at first-credential-write.

## Guidance

When designing credential storage with a `Barrier`-based concurrency test, enumerate **every** call site that mutates credential state:

| Site | When fires | Race shape |
|------|-----------|------------|
| **Rotation** | 401 from API → refresh token → retry | Two workers both see 401 simultaneously, both refresh, second write clobbers first |
| **Bootstrap** | First run, no existing credentials | Two workers both find empty state, both run OAuth, second write clobbers first or both succeed with different tokens |
| **Migration** | On version upgrade / schema change | Migration runs concurrent to a normal write; one worker reads pre-migration, the other writes post-migration |
| **Rebind** | Operator runs `<channel>-login` again | Background publish loop is mid-publish when operator rebinds; rebind clobbers the in-flight token |
| **Cleanup** | TTL expiry → re-bind required | Cleanup deletes the credential while a publish is using it |

Write a `threading.Barrier(2)` test for each site that is reachable concurrently in production. The pattern:

```python
# tests/test_<channel>_credential_race.py
def test_bootstrap_race_against_concurrent_publish():
    """Two workers boot from empty state simultaneously; outcome must be consistent."""
    barrier = threading.Barrier(2)
    results: list[CredentialState] = []
    errors: list[Exception] = []

    def worker():
        try:
            barrier.wait()
            state = bootstrap_credentials(channel="telegraph")
            results.append(state)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"workers raised: {errors!r}"
    assert len(results) == 2
    # Invariant: both workers end up with the same credential state
    # (whichever wrote first; the second observes-and-uses, not overwrites)
    assert results[0].token == results[1].token
```

Then write the equivalent for `rotation`, `migration`, `rebind`, `cleanup`. Each gets its own Barrier test. The pattern is repetitive on purpose — the goal is to exercise the same locking primitive in every state-mutation site.

### Cross-cutting fix: one locking primitive

If the tests reveal multiple sites need protection, refactor to a single mutex (file lock, threading lock, or atomic-rename pattern) that **all** mutation paths route through. Don't put a separate lock at each site — that opens the door to A-locking-vs-B-locking race shapes.

```python
# credential_store.py — right
@contextmanager
def credential_mutation_lock(channel: str):
    lock_path = config_dir() / f".{channel}.lock"
    with file_lock(lock_path):
        yield

def bootstrap(channel: str) -> Credential:
    with credential_mutation_lock(channel):
        ...

def rotate(channel: str) -> Credential:
    with credential_mutation_lock(channel):
        ...

def migrate(channel: str) -> None:
    with credential_mutation_lock(channel):
        ...
```

The Barrier tests then exercise each site through the same primitive.

## Why This Matters

Plans focus on the most-discussed code path (here: rotation), and brainstorm-time the rotation race feels like the hard problem. Bootstrap feels easy ("we just write the credential") and migration feels rare ("only on version bumps"). Both are deceptive:

- **Bootstrap** is hit on every fresh operator setup, which is the highest-stakes moment (first impressions matter, and bootstrap failures often discourage the operator from continuing).
- **Migration** is rare per-version but high-blast-radius — a bad migration race can corrupt every operator's credential file on upgrade day.
- **Rebind** is operator-triggered and concurrent-with-publish by definition (the operator only rebinds because publish is failing).

The test-cost is small. Each Barrier test is ~30 lines and reuses the helpers. The skip-cost is real: discovering a bootstrap race in production means an operator hits it on first setup, possibly with no log line that points to the race.

## When to Apply

- Any PR introducing or modifying credential storage for a new channel.
- Reviewing a plan that mentions "rotation race" or "401 retry" without enumerating other state-mutation sites — push back during plan review.
- Refactoring credential storage (e.g., changing the file format, moving from JSON to TOML, splitting per-channel files).
- After an incident where credential state was found in an unexpected shape — the postmortem should ask "which mutation site was concurrent with which other?"

Skip when:

- Credential state is read-only after install (e.g., a token baked into config.toml that never auto-rotates). No mutation sites = no races.
- The channel has only one mutation site by design (e.g., token-paste bind with no rotation, no migration, no cleanup).

## Examples

**Right (post-2026-05-19 telegraph fix):**

```
Plan 2026-05-19-002 R3 mentioned: "test-first for rotation race"
Implementation:
  - test_telegraph_rotation_race      (Barrier 2, 401 → refresh)
  - test_telegraph_bootstrap_race     (Barrier 2, fresh OAuth)
  - test_telegraph_migration_race     (Barrier 2, format upgrade)
  - test_telegraph_rebind_concurrent  (Barrier 2, operator rebind vs publish)
All four route through credential_mutation_lock("telegraph").
```

**Wrong (counterfactual, what happened initially):**

```
Test:           test_telegraph_rotation_race only
Production:     operator hits bootstrap race on first publish, two workers
                both run OAuth, one wins; the loser's state is half-written
                because there was no lock at bootstrap
Discovery:      hours later, log shows two OAuth callbacks for the same user;
                root cause traced to bootstrap path having no equivalent of
                the rotation lock
```

## Related

- PR #77 (`feat/telegraph-channel-onto-main`) — original rotation work that missed bootstrap.
- PR #81 (`0318138`) — successor PR that landed the broader credential locking.
- `cli/_bind/credential_store.py` (post-refactor) — single locking primitive.
- `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — adjacent: another state-mutation correctness case.
- `threading.Barrier` Python docs — primitive used in the tests.
