"""BIND_ERROR_MESSAGES coverage — Plan 2026-05-19-001 Unit 5.

Every error_code the bind-channel CLI (Unit 2) can emit on a
``channel.bind.failed`` event must map to a Chinese operator message in
``BIND_ERROR_MESSAGES``. This test pins the contract so a future code
addition in the driver without a matching message addition fails loudly
here, not silently in production.
"""

from __future__ import annotations


KNOWN_DRIVER_ERROR_CODES = frozenset({
    "bound_predicate_timeout",
    "playwright_launch_failed",
    "storage_path_traversal",
    "persist_io_error",
    "stream_closed_no_terminal_event",
})


def test_every_known_error_code_has_chinese_message():
    from webui_app.services.bind_job import BIND_ERROR_MESSAGES
    missing = KNOWN_DRIVER_ERROR_CODES - set(BIND_ERROR_MESSAGES.keys())
    assert not missing, (
        f"BIND_ERROR_MESSAGES missing Chinese mappings for: {sorted(missing)}. "
        f"Plan 2026-05-19-001 Unit 5 requires every known driver error_code "
        f"to surface a localized operator message."
    )


def test_every_message_contains_chinese_characters():
    from webui_app.services.bind_job import BIND_ERROR_MESSAGES
    for code, msg in BIND_ERROR_MESSAGES.items():
        assert isinstance(msg, str) and msg, (
            f"BIND_ERROR_MESSAGES[{code!r}] must be a non-empty str"
        )
        assert any("一" <= ch <= "鿿" for ch in msg), (
            f"BIND_ERROR_MESSAGES[{code!r}] must contain CJK chars (got: {msg!r})"
        )


def test_no_english_only_fallbacks_for_known_codes():
    """Defensive: a message that is ASCII-only is almost certainly the
    English fallback we explicitly do not want for shipped codes."""
    from webui_app.services.bind_job import BIND_ERROR_MESSAGES
    for code in KNOWN_DRIVER_ERROR_CODES:
        msg = BIND_ERROR_MESSAGES[code]
        assert not msg.isascii(), (
            f"BIND_ERROR_MESSAGES[{code!r}] is ASCII-only — looks like an "
            f"English fallback escaped the localization pass: {msg!r}"
        )
