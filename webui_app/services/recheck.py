"""History entry re-verification service.

Plan 2026-05-19-006 Unit 5. The publish pipeline already records
``*_unverified`` status when post-publish verify fails (CLI flow), and
older history entries may have been recorded under the old "hard-write
status='published'" code path even though the article never appeared.

This service re-fetches each entry's ``article_urls`` and updates the
real status via :func:`backlink_publisher.linkcheck.verify.verify_published`.
``verify_fn`` is parameterised so tests can inject a fake without going
out to the real network (which the autouse conftest fixtures block).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence

from backlink_publisher.linkcheck.verify import VerificationResult


# Type alias for the injection point: same signature as `verify_published`.
VerifyFn = Callable[..., VerificationResult]


def _default_verify(*args, **kwargs) -> VerificationResult:
    """Lazy indirection so ``unittest.mock.patch`` against
    ``backlink_publisher.linkcheck.verify.verify_published`` takes effect
    even after this module has been imported."""
    from backlink_publisher.linkcheck.verify import verify_published as _vp
    return _vp(*args, **kwargs)


@dataclass
class RecheckSummary:
    checked: int = 0
    confirmed: int = 0          # was unverified/failed → became published/drafted
    downgraded_to_failed: int = 0  # was published/_unverified → now failed
    skipped: int = 0            # no article_urls / unrecheckable

    def as_flash(self) -> str:
        return (
            f"已核实 {self.checked} 条："
            f"{self.confirmed} 升为已发布，"
            f"{self.downgraded_to_failed} 标为失败，"
            f"{self.skipped} 跳过"
        )


def _resolve_required_link(item: dict) -> list[str]:
    target = (item.get("target_url") or "").strip()
    return [target] if target else []


def _final_status_for(original: str, ok: bool) -> str:
    """Map (original status, verify ok?) to the new status to persist."""
    if ok:
        # Strip an ``_unverified`` suffix when verify confirms the post.
        if original.endswith("_unverified"):
            return original[: -len("_unverified")]
        if original == "failed":
            # Manually-marked failed but URL actually resolves → upgrade.
            return "published"
        return original
    # ok=False — verify could not find title + anchor on the live page
    return "failed"


def recheck_one(
    item: dict,
    *,
    verify_fn: VerifyFn = _default_verify,
    max_wait_per_url: int = 10,
) -> dict:
    """Re-verify a single history item.

    Returns a dict of mutations to merge into the item:
    ``status``, ``verify_error`` (only on failure), ``verified_at``,
    plus an ``_outcome`` key consumed by :func:`recheck_many` for the
    summary count.
    """
    article_urls: Sequence[str] = item.get("article_urls") or []
    title = item.get("title", "")
    required_links = _resolve_required_link(item)
    original_status = item.get("status", "")

    if not article_urls:
        return {
            "status": "failed",
            "verify_error": "no article URL to verify",
            "verified_at": datetime.now().isoformat(timespec="seconds"),
            "_outcome": "skipped",
        }

    last_reason = "no verifiable URL"
    for url in article_urls:
        try:
            result = verify_fn(
                url,
                title=title,
                required_link_urls=required_links,
                max_wait=max_wait_per_url,
            )
        except Exception as exc:
            last_reason = f"verify error: {exc}"
            continue
        if result.ok:
            new_status = _final_status_for(original_status, ok=True)
            return {
                "status": new_status,
                "verified_at": datetime.now().isoformat(timespec="seconds"),
                "_outcome": "confirmed",
                # clear stale verify_error if any
                "verify_error": None,
            }
        last_reason = result.reason or last_reason

    new_status = _final_status_for(original_status, ok=False)
    return {
        "status": new_status,
        "verify_error": last_reason,
        "verified_at": datetime.now().isoformat(timespec="seconds"),
        "_outcome": "downgraded",
    }


def recheck_many(
    items: list[dict],
    *,
    verify_fn: VerifyFn = _default_verify,
    max_wait_per_url: int = 10,
) -> tuple[dict[str, dict], RecheckSummary]:
    """Verify a batch of items. Returns ``(id -> mutation_dict, summary)``.

    Caller is responsible for applying the mutations via
    ``history_store.bulk_update`` / ``update_item``.
    """
    by_id: dict[str, dict] = {}
    summary = RecheckSummary()
    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue
        mutation = recheck_one(
            item, verify_fn=verify_fn, max_wait_per_url=max_wait_per_url,
        )
        outcome = mutation.pop("_outcome", None)
        # Strip None values so bulk_update doesn't overwrite with literal nulls
        # unless intended (verify_error=None is intentional to clear stale errors).
        by_id[item_id] = mutation
        summary.checked += 1
        if outcome == "confirmed":
            summary.confirmed += 1
        elif outcome == "downgraded":
            summary.downgraded_to_failed += 1
        elif outcome == "skipped":
            summary.skipped += 1
    return by_id, summary
