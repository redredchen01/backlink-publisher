# AGENTS.md — backlink-publisher

See `README.md` for project overview and `docs/` for plans, brainstorms, ideation, and solutions.

## Lessons capture (dual-track)

The project keeps lessons in two places:

- **Private auto-memory** — Claude Code automatically writes `feedback_*.md` files at `~/.claude/projects/<project-memory-slug>/memory/` during sessions. These are fast-capture, operator-private, and never committed.
- **Public `docs/solutions/`** — High-value or recurring lessons get *promoted* into committed markdown entries under `docs/solutions/<category>/` (categories: `best-practices/`, `logic-errors/`, `test-failures/`, `ui-bugs/`). The promotion tool is `/ce:compound` (a Claude Code skill from the `compound-engineering` plugin — see plugin docs); it generates the frontmatter schema each existing entry uses.

**Promotion is rewriting, not copy-paste. Strip session UUIDs, real domains, absolute paths, and user-identifying quotes; teach the pattern, not the incident.** The grep gates in `docs/plans/2026-05-15-001-refactor-lessons-kit-curation-plan.md` (Unit 5) are the safety net; the gitignored token file at `~/.local/share/backlink-publisher/private-tokens.txt` enumerates what to scrub.

Next curation review: **2026-08-15** — *aspirational quarterly cadence; not enforced by CI or any tool*. This file is static markdown; the actual trigger is "next time `/ce:compound` or `/ce:plan` runs in this repo, scan recent `feedback_*.md` and decide what's worth promoting." Update this date when the review completes; treat skipping a quarter as a soft signal, not a failure.

Soft observation (2026-05-15): historical `docs/brainstorms/` and `docs/plans/` files contain real operator domain references (e.g. target hostnames). The sanitization rule above applies to `docs/solutions/` entries; if the project ever needs to extend it to historical decision artifacts, scope a separate pass — do not retrofit silently.
