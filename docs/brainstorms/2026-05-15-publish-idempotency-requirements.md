---
date: 2026-05-15
topic: publish-idempotency
---

# Publish 5xx Recovery — Verify-by-Listing for Blogger

## Problem Frame

`src/backlink_publisher/adapters/retry.py:26` 当前 `RETRYABLE_HTTP_STATUSES = frozenset({429})` —— 5xx 被显式排除（来源 lesson：`feedback_api-idempotency-lesson`，触发 `2026-05-12-adapter-retry-backoff` R4 的安全选择）。原因是若 5xx 后服务端实际已 commit，本地 retry 会双发。代价：今天 50 条 batch 中遇到一个 5xx 就丢掉那一条；cron 跑里很常见。

该 brainstorm 关 5xx 不敢 retry 这条硬约束，**但不通过引入 client-supplied idempotency key**（那个路径在第一轮 doc-review 中被 3 个 reviewer 一致拒绝：Blogger Posts.insert 几乎肯定不接受 `body.id`，整个对称设计建在未验证的前提上）。改走 **verify-by-listing**：每条 row 在 body 中嵌入 per-row sentinel HTML 注释；5xx 后用 `Posts.list` 拉近期 post 扫 sentinel；找到 → 恢复 URL 并 mark done；找不到 → safe to retry。

Medium API 没有 list-user-posts 端点（API 已弃用），无法走 verify-by-listing —— V1 接受不对称，Medium API 5xx 进 `uncertain` 状态由 operator 手动裁决。Medium browser adapters (brave/playwright) 走现有 `verify_publish` post-flight 检查不变。

来源：`docs/ideation/2026-05-15-open-ideation.md` idea #8(b)。Idea #8(a) crash-safe journal 与 #8(c) content escrow 已分别拆出。第一版 requirements 草稿（基于 client-supplied id 设计）被 ce:review/document-review 推翻；本版是第二稿。

## Architecture

```
plan-backlinks
  └─ (no schema change — current row format kept)

publish-backlinks
  └─ for each row:
       ├─ sentinel = "<!-- bp:tok:" + sha256(target_url + run_id + item_id)[:12] + " -->"
       ├─ adapter.publish(body=<rendered> + sentinel, …)
       │
       ├─ Blogger:
       │    ├─ 2xx → use returned URL → verify_publish (existing) → done
       │    └─ 5xx (after retry budget) →
       │         posts.list(blogId, maxResults=20, orderBy=published, sortDescending)
       │         scan each post.content for sentinel substring
       │         ├─ found → mark done with that post.url → verify_publish
       │         └─ not found → safe to mark failed (next --resume retries cleanly)
       │
       ├─ Medium API:
       │    ├─ 2xx → use returned URL → verify_publish → done
       │    └─ 5xx → mark uncertain + RECON log (no list endpoint = no recovery)
       │
       └─ Medium browser (brave/playwright):
            └─ unchanged — verify_publish post-flight already covers
```

## Requirements

**Sentinel embedding**

- R1. `publish-backlinks` derives `sentinel = "<!-- bp:tok:" + sha256(target_url + "\x00" + run_id + "\x00" + item_id).hexdigest()[:12] + " -->"` for each row, appended to the rendered body before adapter dispatch. Deterministic on `(target_url, run_id, item_id)`: a `--resume` invocation reproduces the same sentinel for the same item (per `2026-05-13-checkpoint-resume` R5 the run_id is preserved across resume).
- R2. Sentinel placement: concatenated to the end of the rendered HTML string for the body (do not assume the body ends with a specific tag — `markdown_utils.render_to_html` may emit `</ul>`, `</pre>`, `</blockquote>`, etc.). The HTML-comment shell `<!-- bp:tok:<digest> -->` is fixed; the hash input uses null-byte separators per R1; only the 12-hex digest output varies across rows.
- R3. Sentinel applies to **all** adapters that publish HTML bodies (Blogger API, Medium API, Medium browser). Medium API and browser don't currently use it for recovery — see R6, R8 — but emitting it consistently keeps the row content uniform and unlocks future cross-adapter recovery without schema migration.

**Blogger 5xx recovery**

- R4. After Blogger 5xx exhausts the existing retry budget (per `2026-05-12-adapter-retry-backoff` R1 = 3 attempts), `blogger_api.publish` enters a **recovery probe**: `service.posts().list(blogId=blog_id, maxResults=20, orderBy='published', sortDescending=True, fetchBodies=True)`. Iterate response items, substring-match sentinel against `item['content']`. On hit: return `published_url = item['url']`, status=`published_recovered`. On miss after iterating all 20: raise `ExternalServiceError` (current behavior — row marks failed; next `--resume` retries with the same sentinel and is safe).
- R5. HTTP 5xx becomes retryable **for Blogger only** (revises `2026-05-12-adapter-retry-backoff` R4 — 5xx joins 429 in the retryable set for Blogger). **Medium API 5xx behavior is unchanged — see R7.** Rationale: even if the recovery probe fails, a `--resume` retry is safe because the sentinel is deterministic — the next attempt re-checks the post-list before publishing (R6) and finds the prior attempt if it actually landed. The 5xx retry budget in R1 of the prior brainstorm stays at 3 attempts; the recovery probe runs after exhaustion only.
- R6. **Pre-dispatch dedup probe**: before each Blogger publish call (not on retry, only at the start of each item's attempt sequence), call the same `Posts.list` and scan for this row's sentinel. On hit: skip the publish entirely, mark done with the discovered URL (this catches the "prior run died after the actual publish landed" case). On miss: proceed to dispatch. To amortize cost across a batch, cache the Posts.list response per-run (per `--resume`, refetch since checkpoint reload signals time has passed).

**Medium API degraded path**

- R7. `medium_api.publish` does not change today's 5xx behavior: HTTP 5xx remains non-retryable per `2026-05-12-adapter-retry-backoff` R4 for Medium. After retry budget exhaustion on 5xx, the item resolves to `status=uncertain` (new value) instead of `failed`. RECON log emits (see R12). Sentinel is embedded in body (R3) so a future API listing capability — or operator-side manual check via Medium UI — has a stable anchor.
- R8. The two browser-based Medium adapters — `medium_brave` and `medium_browser` (collectively, "Medium browser adapters" elsewhere in this doc) — are out of scope for V1. `verify_publish` (existing, `src/backlink_publisher/verify_publish.py`) already covers their "did it commit?" question via post-flight URL GET. They emit the sentinel (R3) but do not consume it.

**Checkpoint integration**

- R9. The existing checkpoint per-item `status` field (per `2026-05-13-checkpoint-resume` R2/R3) gains one new value: `uncertain` (joins existing `pending|done|failed`). No schema rename, no `claim_state` parallel field, no migration of historical checkpoints. Old checkpoints continue to use only `pending|done|failed`.
- R10. `--resume` behavior for existing values is unchanged. For `uncertain`: `--resume` skips the row (does not re-attempt) — operator adjudication is required first.

**Operator surface**

- R11. New `publish-backlinks --list-uncertain [<run_id>]` flag: lists items currently in `status=uncertain` across all incomplete checkpoints (or filtered to one `run_id`). Output columns: `run_id`, `item_id`, `adapter`, `target_url`, `sent_at`, `sentinel` (display-prefix only).
- R12. New `publish-backlinks --adjudicate-uncertain <run_id> <item_id> --to (succeeded|failed) [--published-url <url>] --reason <text>`: terminal transition for a single uncertain item. **Guards**: rejects unless current status == `uncertain` (no override flag in V1 — explicit `--cleanup <run_id>` is the operator's manual escape). `--reason` mandatory. `--published-url` host validated against the row's adapter (medium.com / blogger.com). Every adjudication appends an entry to `~/.cache/backlink-publisher/adjudications.jsonl` with `{run_id, item_id, from_status, to_status, reason, published_url, ts, user=$USER}` — append-only, never rewritten.
- R13. RECON-level log on every transition to `status=uncertain`: `recon("publish.uncertain", item_id=I, run_id=R, adapter=A, sentinel_prefix=<first 6 hex>)`. Per `feedback_recon-level-for-always-on-signals`, RECON bypasses `--log-level` gate. **Payload deliberately minimal** (no `target_url`, no `sent_at`) — full record available via `--list-uncertain`. Avoids leaking campaign URLs into cron mail / Slack piping (per security-lens review).
- R14. WebUI resume banner (existing per `2026-05-13-checkpoint-resume` R12) gains an uncertain count line: _"N uncertain items awaiting adjudication — [Review]"_. Review link does NOT open a new WebUI page in V1 — instead, it shows a `<details>` block in the same banner with the same data as `--list-uncertain`. (Avoids retrofit of the 4904-line `webui.py` god-file per `feedback_standalone-page-vs-retrofit`.)

**Backward compat**

- R15. Pre-existing checkpoint items without sentinel are unaffected — they don't trigger the recovery probe (R4) because they predate the feature. Operator must `--resume` them as the legacy code did.
- R16. The publish stdout JSONL output format gains no new field. Sentinel lives only in the body sent to the adapter; checkpoint records the `status=uncertain` value; nothing else changes. Downstream consumers (operator-captured `published.jsonl`, WebUI, report-anchors) need no updates.

**Companion docs/solutions/ entry**

- R17. Ship a follow-up `docs/solutions/best-practices/http-5xx-blogger-recovery.md` **after PR #33 (lessons-kit-curation) merges** — not bundled in this feature's PR. Frontmatter `applies_when: implementing adapter retry policy or auditing api-idempotency-lesson`. Body documents: (a) Blogger 5xx is now recoverable via Posts.list + sentinel; (b) Medium API 5xx remains uncertain (no API listing); (c) verify-by-listing pattern is the project's preferred approach over client-supplied keys.

## Success Criteria

- A 50-row Blogger batch with 1–2 transient 5xx completes fully without losing rows and without producing duplicate Blogger posts. Verified: count Blogger posts before/after = +50; manual inspection finds no two posts with identical sentinel.
- A Medium API batch with a 5xx mid-flight yields an item with `status=uncertain` (not `failed`), emits a RECON log, surfaces in `--list-uncertain` + WebUI banner.
- Operator running `publish-backlinks --adjudicate-uncertain <run_id> <item_id> --to succeeded --published-url <url> --reason "manual check on medium.com confirmed"` transitions the item terminally; the `adjudications.jsonl` audit log gains one entry; subsequent `--resume` sees the item as `done`.
- A killed Blogger publish (SIGKILL between dispatch and HTTP response) followed by `--resume` finds the post via R6 pre-dispatch probe and skips re-publish — verified by no duplicate post on the blog.
- Two-week production cron soak: zero duplicate Blogger posts, zero Medium API double-publishes, uncertain rows visible to operator with stable run-over-run state.

## Scope Boundaries

- **Out of scope: client-supplied idempotency keys.** Original ideation #8(b) suggested `body.id` for Blogger; first-pass review found Blogger Posts.insert does not document `body.id` support and is likely server-assigned. Verify-by-listing is the chosen mechanism instead.
- **Out of scope: crash-safe journal (ideation #8a) and content escrow (ideation #8c).** Each gets its own brainstorm later.
- **Out of scope: cross-run dedup across totally separate plans.** The R6 pre-dispatch probe only catches "same row from same run died after publish" (sentinel is run-scoped). Operator running `plan-backlinks` twice with no `--resume` will produce duplicate sentinels-and-content — that is operator error, addressed by existing `--resume` flow.
- **Out of scope: Medium API recovery.** No public listing endpoint exists. Degraded `uncertain` + operator adjudication is V1's answer. Re-visit when/if Medium publishes a listing API or when V1 demonstrates Medium API usage is so low we deprecate the adapter entirely.
- **Out of scope: Medium browser cross-run dedup via UI navigation.** Existing `verify_publish` already covers post-flight. Adding pre-flight browser navigation is non-trivial (auth state, page latency) and not justified by observed pain.
- **Out of scope: sentinel-in-Markdown-body for any adapter rendering markdown.** Sentinel is HTML-comment only. Currently all 3 adapter paths render HTML (Blogger body, Medium HTML upload, Medium browser DOM). If a Markdown-only adapter is added, sentinel format must be revisited.
- **Out of scope: SQLite StateStore migration** — survivor #3 absorbs `adjudications.jsonl` and uncertain queue later if it ships.
- **Out of scope: `--dry-run` recovery path** — dry-run never reaches adapter, no sentinel, no probe.

## Key Decisions

- **Verify-by-listing instead of client-supplied keys** — reverses the first-pass design after document-review surfaced that Blogger Posts.insert `body.id` is server-assigned, not client-controlled. Reuses the project's existing `verify_publish.py` pattern (GET-by-URL) and extends it with Posts.list scanning when no URL is available (5xx case). One mechanism, two scenarios, leverages existing infra.
- **Sentinel embedded in body per-row** — deterministic on `(target_url, run_id, item_id)` so `--resume` re-derives the same sentinel and recovery is idempotent. HTML comment survives Blogger storage (verified pattern; planning must confirm against current Blogger HTML pipeline). 12-hex prefix of sha256 = 48 bits, sufficient because the search horizon is "last 20 posts in this blog" — collision probability is effectively zero within that scope.
- **Per-run dedup, not cross-run** — earlier draft tried to scan all checkpoints across all runs for cross-run dedup. Review found this created concurrency races, cleanup contradictions, persona leakage, and O(N×M) cost. V1 keeps dedup run-scoped (per `--resume`), accepts that "two unrelated plan runs of the same target" is operator error.
- **Asymmetric retry policy** — Blogger 5xx becomes retryable (recovery probe makes it safe); Medium API 5xx stays non-retryable with `uncertain` status. Reflects real adapter capability difference.
- **One status field, one new value** — added `uncertain` to existing checkpoint `status` enum; no parallel `claim_state` field, no schema migration, no dual-read paths. Earlier draft's `claim_state` parallel field was over-engineered.
- **Adjudication has strong guards** — state-machine guard (only `uncertain` → terminal), append-only audit log (`adjudications.jsonl`), mandatory `--reason`, validated `--published-url` host. The previous draft had none of these and security-lens reviewer correctly flagged adjudicate as the worst surface in the whole feature.
- **RECON payload minimized** — earlier draft logged `target_url + sent_at` unconditionally, leaking campaign URLs into cron mail. Now logs only item id + run id + adapter + 6-hex sentinel prefix. Full record retrievable via `--list-uncertain`.
- **No new `bp` umbrella CLI** — uses flat `publish-backlinks --<verb>` flags matching existing `pyproject.toml [project.scripts]` shape. Avoids inventing a new entrypoint pattern this feature is too narrow to justify.
- **Companion docs/solutions/ entry deferred** to a follow-up commit after PR #33 lessons-kit lands. Removes the cross-PR dependency the earlier draft created.
- **No WebUI standalone page** — the banner expansion is a `<details>` block, not a new template, respecting the 4904-line `webui.py` god-file constraint.

## Dependencies / Assumptions

- `2026-05-12-adapter-retry-backoff` is shipped (retry budget, error classes, dispatcher batch-continue). Feature revises R4 per-adapter.
- `2026-05-13-checkpoint-resume` is shipped (run_id, atomic per-item write, `--resume`, `--list-runs`, `--cleanup`, WebUI banner). Feature extends the `status` enum and the banner.
- `verify_publish.py` exists and works on Blogger + Medium URLs (verified — see source).
- Blogger Posts.list supports `fetchBodies=True` and returns the full HTML content (confirm during planning by inspecting `googleapiclient` Blogger v3 schema or a sandbox call).
- Blogger preserves HTML comments through its publish pipeline (assumption — confirm during planning by publishing a test post containing `<!-- bp:tok:test -->` to a sandbox blog and re-fetching).
- `recon()` callable exists in `src/backlink_publisher/logger.py` (per `feedback_recon-level-for-always-on-signals`). Confirm during planning.

## Outstanding Questions

### Resolve Before Planning

_(none — Blogger `Posts.list + fetchBodies` and HTML-comment preservation are framework features expected to work; verify in planning via sandbox calls — not blocking the brainstorm)_

### Deferred to Planning

- [Affects R4][Needs research] Confirm Blogger Posts.list with `fetchBodies=True` returns full HTML including comments. Run a sandbox call before R4 implementation.
- [Affects R1, R2][Needs research] Confirm Blogger preserves `<!-- bp:tok:* -->` HTML comments through publish (i.e., the comment appears verbatim in the post's saved content). Sanity-check Medium API too even though we don't consume it there.
- [Affects R4, R6][Technical] Define the cache invalidation policy for the per-run Posts.list response in R6. Refetch on every `--resume`? On a timer? Spec a default.
- [Affects R7][Technical] How does Medium browser adapter set `status=uncertain`? Browser path has no analogue of the API 5xx classification — does it need a new error class, or does R8 fully out-of-scope it (current reading)?
- [Affects R11, R12][Technical] Subcommand placement: `publish-backlinks --list-uncertain` flag works for single-purpose listing; `--adjudicate-uncertain` with 3 args is a long flag. Decide if a small subcommand layer is warranted (e.g., `publish-backlinks uncertain list` / `publish-backlinks uncertain adjudicate ...`) or if flat flags suffice.
- [Affects R13][Needs research] Verify `logger.py` exports a `recon()` callable, not just a level constant. Filing gap in planning if needed.
- [Affects R14][Technical] WebUI banner already collects per-run state; verify the `<details>` block can render a table of uncertain rows without retrofitting the page's CSS/JS. Sibling-not-child principle still applies — if `<details>` is hostile to the current layout, drop R14 to banner-count-only.

### Out of scope but worth flagging (for future brainstorms)

- **Operator inbox DoS surface**: a sustained Medium API 5xx incident could accumulate hundreds of uncertain rows. V1 has no bulk-adjudicate. Watch the soak test — if uncertain accumulates >50 entries in 2 weeks, open a follow-up for `--adjudicate-uncertain --bulk --older-than 7d`.
- **Ledger directory permissions**: `adjudications.jsonl` lives under `~/.cache/backlink-publisher/`. XDG convention says cache is ephemeral; this is operator state. Re-evaluate path + permissions when survivor #3 (SQLite StateStore) is brainstormed.

## Next Steps

→ `/ce:plan` for structured implementation planning.
