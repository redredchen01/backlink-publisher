"""Single source of truth for the events.db kind vocabulary and the
source-record status -> event-kind classification map.

Dependency-free by design: this module imports nothing from ``store`` /
sqlite, so writers that must stay I/O-free (e.g. ``publishing.banner_dispatcher``)
can import a kind constant without dragging in ``EventStore``.

Two seams (see docs/brainstorms/2026-05-25-events-db-kind-contract-requirements.md):

* Seam A — the ``kind`` strings written to events.db. Enumerated in ``KINDS``.
  Do NOT rename these: historical rows depend on the exact strings.
* Seam B — how the projector classifies an upstream source record's ``status``
  into an event kind. Encoded in ``STATUS_MAP`` + ``SOURCE_DEFAULT``.

The just-fixed P0 (projector silently dropped CLI successes when production
wrote checkpoint status ``done`` but the classifier only knew ``succeeded``)
lived at Seam B. The classifier here distinguishes three outcomes so the next
drift is quarantined visibly instead of dropped:

* a concrete event kind            -> emit that kind
* ``CONFIRMED_FAMILY``             -> success; caller resolves confirmed vs
                                      unverified from the record's ``verified``
                                      flag (preserves the PR #222 D5 split)
* ``NO_EMIT``                      -> a *declared intentional* no-op (the source
                                      is not the system of record for this
                                      status); skip silently, never quarantine
* ``QUARANTINE`` (the per-source default for authoritative sources) -> an
                                      unrecognized status the source *should*
                                      have classified; quarantine + continue
"""

from __future__ import annotations

from typing import Final

# --- Seam A: the event-kind vocabulary (14 kinds; do NOT rename) ---------

PUBLISH_INTENT: Final = "publish.intent"
PUBLISH_CONFIRMED: Final = "publish.confirmed"
PUBLISH_UNVERIFIED: Final = "publish.unverified"
PUBLISH_FAILED: Final = "publish.failed"
DRAFT_CREATED: Final = "draft.created"
DRAFT_SCHEDULED: Final = "draft.scheduled"
BANNER_SOURCE_URL_FALLBACK: Final = "banner.source_url_fallback"
BANNER_SKIPPED_NO_METHOD: Final = "banner.skipped_no_method"
BANNER_FAILED: Final = "banner.failed"
BANNER_EMBEDDED: Final = "banner.embedded"
BANNER_SKIPPED_NO_ARTIFACT: Final = "banner.skipped_no_artifact"
IMAGE_GEN_INVOKED: Final = "image_gen_invoked"
IMAGE_GEN_CAPPED: Final = "image_gen_capped"
IMAGE_GEN_DISABLED_AUTO: Final = "image_gen_disabled_auto"

#: Every kind ever written to events.db. The R8a CI gate asserts no writer
#: emits a kind outside this set.
KINDS: Final[frozenset[str]] = frozenset(
    {
        PUBLISH_INTENT,
        PUBLISH_CONFIRMED,
        PUBLISH_UNVERIFIED,
        PUBLISH_FAILED,
        DRAFT_CREATED,
        DRAFT_SCHEDULED,
        BANNER_SOURCE_URL_FALLBACK,
        BANNER_SKIPPED_NO_METHOD,
        BANNER_FAILED,
        BANNER_EMBEDDED,
        BANNER_SKIPPED_NO_ARTIFACT,
        IMAGE_GEN_INVOKED,
        IMAGE_GEN_CAPPED,
        IMAGE_GEN_DISABLED_AUTO,
    }
)


# --- Seam B: classification outcome sentinels ----------------------------


class _Outcome:
    """A non-kind classification outcome. Distinct identity, repr for logs."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<{self.name}>"


#: Success status whose concrete kind (confirmed vs unverified) is resolved by
#: the record's ``verified`` flag, not by the status alone.
CONFIRMED_FAMILY: Final = _Outcome("CONFIRMED_FAMILY")

#: A declared intentional no-op: the source is deliberately not the system of
#: record for this status (e.g. history does not emit for ``drafted`` — drafts
#: owns it; drafts does not emit for ``failed`` — history owns it). Skip
#: silently; do NOT quarantine.
NO_EMIT: Final = _Outcome("NO_EMIT")

#: An unrecognized status from an authoritative source. The projector
#: quarantines the record (never drops it) and continues. This is the
#: anti-P0 defense.
QUARANTINE: Final = _Outcome("QUARANTINE")


#: An outcome is either a concrete event-kind string or one of the sentinels.
Outcome = "str | _Outcome"

#: Known ``(source_record_type, status) -> outcome`` classifications.
#: A status absent from a source's map resolves to ``SOURCE_DEFAULT[source]``.
STATUS_MAP: Final[dict[str, dict[str, "str | _Outcome"]]] = {
    # Checkpoint is the authoritative publish-outcome source: an unrecognized
    # status is genuine drift (the P0 class) -> default QUARANTINE.
    "checkpoint": {
        "pending": PUBLISH_INTENT,
        "done": CONFIRMED_FAMILY,
        "succeeded": CONFIRMED_FAMILY,
        "failed": PUBLISH_FAILED,
    },
    # History emits only for published/failed; every other status it sees is a
    # transient state owned by another source (e.g. ``drafted`` is owned by the
    # drafts queue) -> default NO_EMIT (intentional suppression, verified in
    # projector reducer comments).
    "history": {
        "published": PUBLISH_CONFIRMED,
        "failed": PUBLISH_FAILED,
        "drafted": NO_EMIT,
    },
    # Drafts owns scheduled/drafted; ``failed`` is owned by history -> default
    # NO_EMIT (intentional suppression).
    "drafts": {
        "published": PUBLISH_CONFIRMED,
        "scheduled": DRAFT_SCHEDULED,
        "drafted": DRAFT_CREATED,
        "failed": NO_EMIT,
    },
}

#: Per-source outcome for a status absent from ``STATUS_MAP[source]``.
#: Checkpoint defaults to QUARANTINE (drift is real); history/drafts default to
#: NO_EMIT (they are catch-all suppressors for non-owned statuses).
SOURCE_DEFAULT: Final[dict[str, "str | _Outcome"]] = {
    "checkpoint": QUARANTINE,
    "history": NO_EMIT,
    "drafts": NO_EMIT,
}


def classify(source_type: str, status: str) -> "str | _Outcome":
    """Return the classification outcome for ``(source_type, status)``.

    Returns either a kind string (one of ``KINDS``) or one of the sentinels
    ``CONFIRMED_FAMILY`` / ``NO_EMIT`` / ``QUARANTINE``. Never raises — an
    unknown source_type with no default also resolves to ``QUARANTINE`` so a
    new source can't silently drop records.
    """
    per_source = STATUS_MAP.get(source_type, {})
    if status in per_source:
        return per_source[status]
    return SOURCE_DEFAULT.get(source_type, QUARANTINE)
