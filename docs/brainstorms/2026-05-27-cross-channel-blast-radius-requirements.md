---
date: 2026-05-27
topic: cross-channel-blast-radius
---

# Cross-Channel Blast Radius (SEO Connected-Penalty Containment)

## Problem Frame

The operator publishes backlinks in a **full mesh**: every money site fans
out to all ~21 registered channels. This maximizes the *blast radius* of an
SEO penalty — if Google/SpamBrain detects an unnatural pattern on **any one
correlation axis** (anchor-text uniformity, content-template reuse, target-URL
convergence, link byte-signature, publish timing burst), every money site that
shares that axis through the channel set is connected to the same footprint and
can be devalued or penalized **at once**.

The existing `footprint` CLI audits byte-level link signatures **within a single
corpus** (`attr_order` / `rel` / `target` / `preceding_char`, ≥95% concentration
alarm) and knows the cluster-key concept (multiple source domains → same money
URL). It has **no view of the cross-channel × cross-money-site connectivity
graph** — exactly the blind spot that "publish to all channels" creates.

The goal is to **root-cause-contain** blast radius, not just patch individual
channels. **Phase 1 ships the cheap structural risk reduction first** (channel
cull + a minimal channel→money-site cell split) — that is what actually lowers
exposure. The blast-radius scorer follows in Phase 2 as a *validation tool and
guardrail*, confirming the structural fix worked and watching future plans.

> **Sequencing decision (2026-05-27).** An earlier draft put the scorer first.
> Review pushed back: under today's full mesh the scorer's worst-case answer is
> trivially "all money sites" on every run — it does not reduce risk, and its
> success criteria can't even be met until a structural lever exists. So the
> structural fix leads; measurement validates. See Key Decisions.

**Threat status: preventive.** No penalty incident is cited as having occurred
yet — this is pre-incident investment, justified by the fact that a connected
penalty is catastrophic and slow to recover from.

### Blast radius: full mesh vs contained cells

```
FULL MESH (today)  — one detection on any axis connects ALL money sites
  ────────────────────────────────────────────────────────────────
   money A ─┐                                    ┌─ channel 1
   money B ─┼──── shared anchor pool / template ─┼─ channel 2
   money C ─┘     / target convergence / timing  └─ ... channel 21

   Detect axis on channel 2  →  A, B, C all connected  →  blast radius = 3 sites


CONTAINED CELLS (Phase 1 target) — detection stays inside one cell
  ────────────────────────────────────────────────────────────────
   cell 1:  money A ──── channels {1..7}    (disjoint anchors/templates)
   cell 2:  money B ──── channels {8..14}
   cell 3:  money C ──── channels {15..21}

   Detect axis in cell 1  →  only A connected  →  blast radius = 1 site
```

Phase 1 moves the operator from the top picture toward the bottom one. The
scorer (Phase 2) turns "blast radius = N money sites" into an auditable number
that confirms the move worked and guards future plans. Prose governs if the
diagram and text disagree.

## Requirements

> **Read order note:** requirements keep stable IDs R1–R9 regardless of phase.
> Delivery order is **Phase 1 (R9, R7-minimal) → Phase 2 (R1–R6) → Phase 3
> (R8, full R7)**. The scorer block (R1–R6) is written first below for
> readability, but ships *second*.

**Blast-radius scorer + gate — R1–R6 (Phase 2: validation & guardrail)**
- R1. Model the cross-channel connectivity graph from the **planned** publish
  set (the `plan-backlinks` / `validate-backlinks` JSONL payloads). Nodes are
  money sites and channels; an edge exists when they share a detectable
  correlation axis. **Node key = `main_domain`** (the registrable money-site
  domain — confirmed present in the payload alongside `target_url` and
  `platform`); multiple `target_url`s under one money site are sub-nodes /
  attributes, not separate money sites.
- R2. Compute a **blast-radius score** = for each detectable axis, the size of
  the worst-case connected set of money sites reachable through the channel set
  (i.e. "if this axis is detected on this channel, N money sites go down
  together").
- R3. Detectable axes to model. **Only one axis is reusable from `footprint`** —
  the link byte-signature (`attr_order`/`rel`/`target`/`preceding_char`), via
  `extract_link_signatures`. The other axes are **net-new extraction**:
  anchor-text class/template (lives in `anchor/metrics.py`, not footprint) and
  target-URL convergence / cluster-key (a documented concept with no extraction
  code today). Content-template similarity does not exist anywhere yet (see
  the deferred research question). **Publish-timing burst is NOT computable from
  the offline planned payloads** — timing is a publish-time random `time.sleep`
  from `MEDIUM_THROTTLE_MIN/MAX`, never written into the plan; drop it from the
  v1 offline scorer, or source it from `events.db` / `publish-history.json` at
  the cost of the pure pre-publish framing (deferred to planning).
- R4. Output the per-axis blast radius, the **worst-case connected-set size**
  (number of money sites in the largest connected component), and which
  channels/axes drive it. Human-readable markdown plus `--json` (mirror the
  `footprint` / `report-anchors` output contract).
- R5. Read-only, offline, no network — a pre-publish audit step over planned
  payloads (precedent: `preflight-targets`, `audit-state`, `footprint`).
- R6. The score must be **gate-able** (consumable as a pass/fail signal); the
  exact gate hardness and threshold are deferred to planning (see below).

**Structural risk reduction — R9 + R7 (Phase 1: first priority), R8 (Phase 3)**
- R9. **[Phase 1] Channel-quality cull**: surface channels that add footprint /
  detectability but little equity (nofollow + `referral_value="low"`) as
  retirement candidates — reduce the exposure surface, reusing the registry's
  existing `referral_value` grading. Cheapest lever: no graph, no similarity
  code; read-only surfacing, operator confirms the retirement set.
- R7. **[Phase 1, minimal] Containment**: partition the full mesh into isolated
  cells so each money-site group touches a **disjoint** channel subset. Phase 1
  delivers a *minimal* cell split (a config-level money-site→channel-subset
  assignment); a detection in one cell cannot connect to another cell's money
  sites. (The Phase 2 scorer later only *measures* the current plan and
  *re-validates* a proposed new plan — it does not generate cell assignments.
  Producing the cells is an operator/config input decision, consistent with the
  no-mutation scope boundary.)
- R8. **[Phase 3] De-correlation**: per-channel rotation of anchor pools,
  template variants, target-URL diversity (in the planned payloads) plus
  publish-schedule jitter (applied at execution time, not scoreable offline) so
  no single axis is uniform across channels; the scorer confirms the per-axis
  blast radius drops.

## Success Criteria

**Phase 1 (structural risk reduction — the safety win):**
- The operator has culled the negative-ROI channels (R9) and split money sites
  into disjoint channel cells (R7-minimal), so no single channel co-links every
  money site. Exposure is measurably smaller than full mesh.
- This is checkable without the scorer: count the channels each money site
  touches, confirm cells are disjoint.

**Phase 2 (scorer — validation & guardrail, self-contained, solo-checkable):**
- The scorer runs offline over a planned payload and emits, **per axis and
  channel**, the worst-case connected-set size, plus the single worst-case
  across all axes and the (channel, axis) drivers behind it.
- It confirms the Phase 1 cell split actually lowered the connected-set size,
  and the operator can audit the driver attribution against a hand-traced
  example. Caveat: a lower connected-set size follows arithmetically from
  partitioning — it is *not* by itself proof of reduced real penalty risk (see
  the unvalidated-propagation question under Outstanding Questions).

## Scope Boundaries
- **Not** an indexability, ranking, or "will Google actually penalize" oracle —
  it is a heuristic proxy for *correlated detectability* (same honesty caveat as
  `preflight-targets`).
- Phase 1 is **structural risk reduction only** (R9 cull + R7-minimal cells).
  The scorer (R1–R6) is Phase 2; full de-correlation (R8) is Phase 3.
- The scorer **surfaces** risk; it does not auto-rewrite anchors, reshuffle
  channels, or mutate the publish plan.
- Does not replace the existing single-corpus `footprint` audit — it sits above
  it, consuming the same dimensions across channels.

## Key Decisions
- **Blast-radius mode = SEO connected-penalty** (not account-ban or operational
  failure cascade). Centre of gravity is the footprint connectivity graph.
- **Topology today = full mesh** → worst-case blast radius; this is the thing to
  contain.
- **All four mechanisms wanted; structural fix first, scorer validates**
  (reversed from the initial draft after review). Under full mesh the scorer's
  worst-case answer is trivially "all money sites" and changes nothing until a
  lever exists, so the cheap structural levers lead. Phasing: **Phase 1** = R9
  cull + R7-minimal cells (immediate exposure cut) → **Phase 2** = R1–R6 scorer
  (validate + guardrail) → **Phase 3** = R8 de-correlation + full R7.
- **Also consider fixing the full-mesh default upstream.** Phase 1's cell split
  can be expressed as a `plan-backlinks` default that never builds a full mesh,
  rather than a post-hoc reshuffle — making the scorer a guardrail, not a
  centerpiece. Flagged for planning (see Outstanding Questions).
- **Build vs extend**: a net-new cross-channel scorer **plus a net-new
  connectivity-graph / connected-set layer** (`footprint`'s `analyze_corpus` is
  flat — it takes a list of HTML strings and discards every other payload field,
  so it cannot partition by money site or channel). The scorer *reuses* only
  `footprint`'s single-link byte-signature extractor (`extract_link_signatures`);
  the anchor-text axis is sourced from `anchor/metrics.py` and the remaining
  axes are built fresh. Budget the scorer phase (Phase 2) for the graph layer,
  not for reuse.

## Dependencies / Assumptions
- The planned publish set (which money site × which channel × which anchor ×
  which content) is reconstructable from `plan-backlinks` / `validate-backlinks`
  JSONL — **confirmed**: payloads carry `main_domain`, `target_url`, `platform`,
  and `links[].anchor`. (Publish *timing* is the exception — not in the plan;
  see R3.)
- Assumes registry `referral_value` / `dofollow` grading is the source of truth
  for the R9 cull.

## Outstanding Questions

### Resolve Before Planning
- (none — core product decisions resolved)

### Deferred to Planning
- [Affects R6][User decision + Needs research] Gate hardness for v1: advisory
  report (exit 0), soft gate (block + `--acknowledge`/OVERRIDE), or hard gate
  (exit non-zero). Needs real-corpus calibration to avoid false-positives
  halting all publishing; pick after seeing the scorer's actual score
  distribution.
- [Affects R3][Needs research] How to compute **content-template similarity**
  across channels (hashing / shingling / normalized structure) without flagging
  legitimately reused boilerplate.
- [Affects R1, R5][Technical] Wiring: new `blast-radius` CLI verb vs extending
  `footprint` with a cross-channel mode; how to thread money-site identity
  through the payload stream.
- [Affects R7][Technical] How to represent money-site → channel cell assignments
  (config schema vs derived) and where containment is enforced in the pipeline.
- [Affects R7, Problem Frame][Technical] **Fix the full-mesh default upstream?**
  Whether `plan-backlinks` should default to cell-scoped fan-out (never building
  a full mesh) instead of a post-hoc reshuffle — and why it fans to all channels
  today (deliberate equity-maximization vs unexamined default).
- [Affects R9, R7][User decision] Phase 1 sizing: how many money sites exist
  today and how many cells / how aggressive a cull — drives whether R7-minimal
  is a 2-cell split or finer.
- [Affects R2][Needs research] Whether blast radius is a single worst-case count
  or a weighted score (money-site equity × axis-detectability) — affects how the
  gate threshold is expressed.
- [Affects Problem Frame, R2][Needs research] **Penalty-propagation validity**:
  the model assumes a connected set on a modeled axis = sites that go down
  together. Confirm (from SEO literature / observed incidents) that connected
  penalties actually propagate along these axes, versus platform-level / shared-IP
  / disavow-linkage mechanisms the scorer does not model — otherwise a low score
  is false confidence.

## Next Steps
→ `/ce:plan` for structured implementation planning. **Start with Phase 1: R9
channel cull + R7-minimal cell split** (the immediate exposure cut). The scorer
(R1–R6) is planned as Phase 2 validation once the cells exist.
