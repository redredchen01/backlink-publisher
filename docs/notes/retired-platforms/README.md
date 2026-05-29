# Retired Platforms

Platforms explicitly evaluated and rejected for integration. Each file records the evidence
and decision so the question "why isn't X supported?" has a permanent, dated answer.

These platforms are **not** registered in the adapter registry. Do not add them to
`publishing/adapters/__init__.py` without first reopening the investigation with new evidence.

## Index

| Platform | Decision date | Reason |
|---|---|---|
| [Bloglovin](bloglovin.md) | 2026-05-25 | Platform rebranded (→ Activate), blog service discontinued 2021, Cloudflare 403 |

## How to Add an Entry

1. Create `<platform-slug>.md` following the `bloglovin.md` template.
2. Add a row to the table above.
3. Link the spike-notes or findings doc that contains the raw probe evidence.
