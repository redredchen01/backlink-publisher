"""Cell-assignment config parser — Blast-radius Phase 1 (R7-minimal).

Parses ``[cells."<main_domain>"]`` blocks into a mapping of
``main_domain → list[channel_name]``.  Example config fragment::

    [cells."example.com"]
    channels = ["telegraph", "rentry"]

    [cells."another-site.org"]
    channels = ["blogger", "medium"]

**Fail-loud contract** (mirrors ``config/parsers/alarm.py`` — NOT the
tolerant skip-with-warning posture of ``target.py``):

- Unknown channel name (not in ``registered_platforms()``) →
  ``InputValidationError`` at parse time.  A typo (``"telegrph"``)
  would otherwise silently drop a channel the operator believes is
  enrolled — a footgun in a safety feature.
- The same channel in two or more sites' cells →
  ``InputValidationError`` at parse time.  Disjointness is the point
  of containment; overlap defeats it, and the error fires at config-load
  time (before any publishing), so the operator sees a hard failure
  before a batch starts.

``[cells.*]`` is an **unmanaged root** — deliberately NOT added to
``_SAVE_CONFIG_KNOWN_ROOTS``, so ``_preserve_unknown_sections``
passes the section through verbatim on every ``save_config`` call.
"""

from __future__ import annotations

from typing import Any

from backlink_publisher._util.errors import InputValidationError


def _registered_platforms() -> list[str]:
    """Lazy wrapper for ``registered_platforms()``.

    The import is deferred to call time to break the config ↔ registry
    circular dependency: ``config.loader`` imports ``cells`` at module
    level, and ``registry`` imports ``backlink_publisher.config.Config``
    at module level.  A module-level wrapper (rather than an inline
    ``from ... import ...`` inside the function body) keeps the symbol
    patchable by ``monkeypatch.setattr`` in tests.
    """
    from backlink_publisher.publishing.registry import (  # noqa: PLC0415
        registered_platforms,
    )
    return list(registered_platforms())


def _parse_cell_assignments(cells_section: Any) -> dict[str, list[str]]:
    """Parse ``[cells.*]`` into ``{main_domain: [channel, ...]}``.

    ``cells_section`` is the dict produced by ``tomllib`` for the ``cells``
    top-level key.  ``None`` or empty dict → return ``{}``, no error.

    Raises ``InputValidationError`` on:
    - an entry that is not a table
    - ``channels`` value that is not a list of strings
    - a channel name not in ``registered_platforms()``
    - the same channel appearing in more than one site's cell (overlap)
    """
    if not cells_section:
        return {}

    if not isinstance(cells_section, dict):
        raise InputValidationError(
            f"[cells] must be a table of tables, got {type(cells_section).__name__}"
        )

    known: set[str] = set(_registered_platforms())
    result: dict[str, list[str]] = {}
    # Track channel → first-claiming domain for overlap detection.
    claimed: dict[str, str] = {}

    for raw_domain, entry in cells_section.items():
        domain = raw_domain.rstrip("/")  # normalise, consistent with target.py

        if not isinstance(entry, dict):
            raise InputValidationError(
                f'[cells."{raw_domain}"] must be a table, '
                f"got {type(entry).__name__}"
            )

        channels_raw = entry.get("channels")
        if channels_raw is None:
            # [cells."x"] present but no channels key → empty cell (valid).
            result[domain] = []
            continue

        if not isinstance(channels_raw, list) or not all(
            isinstance(c, str) for c in channels_raw
        ):
            raise InputValidationError(
                f'[cells."{raw_domain}"].channels must be a list of strings'
            )

        channels: list[str] = []
        for ch in channels_raw:
            # --- Unknown channel check ---
            if ch not in known:
                raise InputValidationError(
                    f'[cells."{raw_domain}"].channels: unknown channel {ch!r} '
                    f"(not in registered_platforms()). "
                    f"Check for a typo; known channels: {sorted(known)}"
                )
            # --- Disjointness check (cross-cell overlap) ---
            if ch in claimed:
                raise InputValidationError(
                    f"[cells] overlap: channel {ch!r} is assigned to both "
                    f'"{claimed[ch]}" and "{raw_domain}". '
                    f"Each channel must belong to at most one cell — "
                    f"a shared channel defeats containment."
                )
            claimed[ch] = raw_domain
            channels.append(ch)

        result[domain] = channels

    return result
