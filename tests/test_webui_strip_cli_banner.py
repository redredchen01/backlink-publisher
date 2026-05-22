"""Regression: WebUI must strip the config_echo banner before displaying
errors. Without this, the 5-line banner + run_id line eats the 200-char
truncation budget in publish-history templates and hides the real error
(see memory: feedback_webui_stderr_preview_truncated).
"""

from __future__ import annotations

from webui_app.helpers.cli_runner import strip_cli_diagnostic_banner


_BANNER_AND_RUN_ID = (
    "[publish-backlinks] effective config:\n"
    "  config:    /Users/dex/.config/backlink-publisher/config.toml\n"
    "  env:       (none)\n"
    "  platforms: medium\n"
    "  sha:       08050522eeff97a7\n"
    "publish-backlinks: run_id=202\n"
)


def test_strip_banner_only_returns_sentinel():
    result = strip_cli_diagnostic_banner(_BANNER_AND_RUN_ID)
    assert "effective config" not in result
    assert "run_id" not in result
    assert "CLI exited without an error message" in result


def test_strip_banner_preserves_real_error():
    real_err = (
        "AuthExpiredError: medium session expired — please rebind\n"
        "publish failed: medium credentials invalid\n"
    )
    result = strip_cli_diagnostic_banner(_BANNER_AND_RUN_ID + real_err)
    assert result.startswith("AuthExpiredError")
    assert "publish failed: medium credentials invalid" in result
    assert "effective config" not in result
    assert "run_id" not in result


def test_strip_banner_without_run_id_line():
    banner_only_no_run_id = (
        "[plan-backlinks] effective config:\n"
        "  config:    /tmp/cfg.toml\n"
        "  env:       (none)\n"
        "  platforms: (none)\n"
        "  sha:       abc\n"
        "ImportError: backlink_publisher.foo\n"
    )
    result = strip_cli_diagnostic_banner(banner_only_no_run_id)
    assert result == "ImportError: backlink_publisher.foo"


def test_strip_banner_preserves_input_without_banner():
    no_banner = "row 1: missing field 'language'\nrow 1: bad value\n"
    result = strip_cli_diagnostic_banner(no_banner)
    assert "row 1: missing field 'language'" in result
    assert "row 1: bad value" in result


def test_strip_banner_handles_empty():
    assert strip_cli_diagnostic_banner("") == ""


def test_strip_banner_matches_all_cli_names():
    for cli in ("plan-backlinks", "validate-backlinks", "publish-backlinks",
                "report-anchors", "footprint"):
        s = (
            f"[{cli}] effective config:\n"
            "  config:    /tmp/c.toml\n"
            "  env:       (none)\n"
            "  platforms: medium\n"
            "  sha:       deadbeef\n"
            "Some real diagnostic\n"
        )
        result = strip_cli_diagnostic_banner(s)
        assert result == "Some real diagnostic", f"failed for {cli}: {result!r}"
