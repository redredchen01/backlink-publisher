# Telegraph Phase 0 routine prompt changelog — 2026-05-18

Unit 1 of plan `docs/plans/2026-05-18-009-feat-telegraph-phase0-ship-seal-plan.md` (v3).

Three RemoteTrigger routines run the T+7 / T+14 / T+21 verdict cycle for the 2026-05-18 Telegraph Phase 0 batch. Their **previous** prompts emitted Pass verdict comments on PR #36 without any machine-readable marker. The Phase 0 ship-seal CLI (`backlink_publisher.cli.phase0_seal init`) needs to recognise *which* PR comment is the routine's authoritative verdict and refuse comments that look similar but aren't. The marker is also the alignment point validated by `tests/test_phase0_marker_alignment.py`.

This changelog records what was sent to RemoteTrigger `action=update`. The routines were updated via `RemoteTrigger` (full prompt replace, not diff) on 2026-05-18.

## Marker contract

Each Pass-path verdict comment now ends with an HTML comment of this form:

```
<!-- phase0-verdict: result=pass run_id=t7-2026-05-25T10:00:00Z -->
```

The regex it MUST match (single source of truth in Python):

```python
# src/backlink_publisher/phase0/validation.py:29
MARKER_RE = re.compile(r"<!--\s*phase0-verdict:\s*result=pass\s+run_id=(\S+)\s*-->")
```

`run_id` is opaque to the seal CLI — it just has to be non-whitespace and stable for a single routine fire. The routines substitute it from `$(date -u +%Y%m%dT%H%M%SZ)` prefixed with the checkpoint tag (`t7-`, `t14-`, `t21-`).

Fail-path comments (regression at T+7, indexed<7 at T+14, any gate fail at T+21) deliberately do NOT emit a marker — the seal CLI must refuse to seal off a non-pass verdict.

## Example marker that the alignment gate parses

The CI gate `tests/test_phase0_marker_alignment.py` reads the latest `routine-prompt-changelog-*.md` and asserts at least one match. The marker below is the canonical example:

<!-- phase0-verdict: result=pass run_id=t14-2026-06-01T10:00:00Z -->

If you change `MARKER_RE` in `validation.py`, edit the example above (and every routine prompt) in the same commit. The test will fail loudly until both sides realign.

## Per-routine summary

### `trig_01GKGPjL9uaWQfm65fhUZxpf` — `telegraph-phase0-t7-recheck` (fires 2026-05-25T10:00Z)

- Renamed step 8 from an unconditional `gh pr comment 36` invocation to a two-branch dispatch keyed on the script summary's `nofollow_introduced` count.
- PASS branch (`nofollow_introduced == 0`) emits the comment body + the trailing marker `<!-- phase0-verdict: result=pass run_id=t7-... -->`.
- FAIL branch (`nofollow_introduced > 0`) emits a regression-only comment with no marker; followup brainstorm path unchanged from prior step 5.
- All other steps (fetch, install, script invocation, table edit, commit/push) unchanged.

### `trig_01U8Wc8f5sai6shXwiYJDAZk` — `telegraph-phase0-t14-verdict` (fires 2026-06-01T10:00Z)

- Renamed step 9 from an unconditional comment to a two-branch dispatch keyed on `indexed` count.
- PASS branch (`indexed >= 7`, G1 provisional pass) emits comment body + trailing marker `<!-- phase0-verdict: result=pass run_id=t14-... -->`.
- FAIL branch (`indexed < 7`) emits a fail-only comment with no marker.
- **New step 10** (PASS branch only): writes the phase marker `phase=g1_passed expected_units=[unit2,unit4,unit5,unit6]` into `refs/notes/phase0-seal` on origin's `origin/main` ref, then pushes the notes ref. This is the signal the operator's pre-push hook (Unit 5, not yet shipped) reads to decide whether to enforce seal validation on Telegraph-staged branches.
- FAIL branch deliberately skips step 10: no phase marker = no operator seal authority = the entire ship-seal pipeline stays dormant.

### `trig_01JpjiDKJNEXUr1mfQFacg6b` — `telegraph-phase0-t21-final-verdict` (fires 2026-06-08T10:00Z)

- Renamed step 10 (PR #36 comment) from a two-line if/else into the same explicit two-branch dispatch.
- PASS branch (G1 AND G2 AND G3 all pass) emits the unblock comment + trailing marker `<!-- phase0-verdict: result=pass run_id=t21-... -->`.
- FAIL branch emits the existing fail body with no marker.
- Step 11 (PR #38 velog comment) unchanged.
- The T+21 PASS comment is the canonical verdict the operator passes to `phase0-seal init --comment-url …`.

## Why all three carry markers, not just T+14

T+7 is observational (no gate), T+14 is provisional G1, T+21 is final G1+G2+G3. The operator could legitimately seal off any of the three depending on which marker arrives first cleanly: T+7 for tentative early sealing, T+14 once G1 provisionally passes, T+21 as the canonical seal point. The seal CLI doesn't care which checkpoint produced the marker — it cares that the verdict author is on the allowlist (Unit 0, `authorized-routine-bots.yaml`) and that the marker is well-formed.

## What this commit does NOT do

- Does NOT modify any code path. The marker constant `validation.MARKER_RE` already exists at `src/backlink_publisher/phase0/validation.py:29` from Unit 2.
- Does NOT enforce the marker. Enforcement happens in `phase0-seal init` (Unit 3, already shipped via PR #66) which calls `validation.parse_verdict_comment_body()`.
- Does NOT install the pre-push hook. That is Unit 5.
- Does NOT bootstrap the allowlist file. That is Unit 0 (also 2026-05-25 deadline) — both Unit 0 and Unit 1 must land before the routines fire on 5/25, otherwise the T+7 verdict comment will have a marker but no allowlist entry and `phase0-seal init` will reject the verdict author as untrusted.

## Rollback

If a routine update is wrong, re-run `RemoteTrigger action=update` with the PRE-update prompt captured at the top of this session's transcript. The previous text is also embedded in the PR description for this commit.

The previous prompts for posterity (verbatim) live in the corresponding PR thread; not duplicated here to keep the diff readable.
