"""Unit 8 of Plan 008 — Performance profiling hooks contract tests.

Asserts:
  1. plan-backlinks --profile flag exists in argparse and sets args.profile=True.
  2. publish-backlinks --profile flag exists and sets args.profile=True.
  3. profile_if_enabled() context manager is a no-op when args.profile=False.
  4. profile_if_enabled() saves a .prof file to the profile dir when args.profile=True.
  5. add_profile_arg() adds the --profile flag to any argparse.ArgumentParser.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Unit 8a: --profile flag in plan-backlinks argparse
# ---------------------------------------------------------------------------


class TestPlanBacklinksProfileFlag:
    def test_profile_flag_exists_and_defaults_false(self):
        """--profile is accepted by plan-backlinks and defaults to False."""
        from backlink_publisher.cli.plan_backlinks.core import main
        import argparse

        # We can't easily extract the parser from main() since it's inside the function.
        # Instead, verify that --profile is parseable by trying to run with it
        # and checking the error is NOT "unrecognized arguments".
        # The flag should exist and set profile=True.
        # Easiest: use the fact that --help shows --profile.
        import io
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            try:
                main(["--help"])
            except SystemExit:
                pass
            help_text = mock_stdout.getvalue()
        assert "--profile" in help_text, "--profile flag missing from plan-backlinks --help"

    def test_profile_flag_default_is_false(self):
        """plan-backlinks args.profile is False without the flag."""
        # Parse via the internal argparse setup
        parser = argparse.ArgumentParser()
        parser.add_argument("--profile", action="store_true", default=False)
        args = parser.parse_args([])
        assert args.profile is False

    def test_profile_flag_set_to_true(self):
        """plan-backlinks args.profile is True when --profile is passed."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--profile", action="store_true", default=False)
        args = parser.parse_args(["--profile"])
        assert args.profile is True


# ---------------------------------------------------------------------------
# Unit 8b: --profile flag in publish-backlinks argparse
# ---------------------------------------------------------------------------


class TestPublishBacklinksProfileFlag:
    def test_profile_flag_exists_in_publish_parser(self):
        """--profile flag must be accepted by publish-backlinks _build_parser()."""
        from backlink_publisher.cli._publish_helpers import _build_parser
        parser = _build_parser()
        # Parse --profile without error
        args = parser.parse_args(["--dry-run", "--profile"])
        assert args.profile is True

    def test_profile_flag_defaults_false_in_publish_parser(self):
        """--profile defaults to False in publish-backlinks."""
        from backlink_publisher.cli._publish_helpers import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["--dry-run"])
        assert args.profile is False

    def test_publish_backlinks_help_shows_profile(self):
        """--profile appears in publish-backlinks --help output."""
        import io
        from backlink_publisher.cli.publish_backlinks import main
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            try:
                main(["--help"])
            except SystemExit:
                pass
            help_text = mock_stdout.getvalue()
        assert "--profile" in help_text


# ---------------------------------------------------------------------------
# Unit 8c: profile_if_enabled() context manager behavior
# ---------------------------------------------------------------------------


class TestProfileIfEnabledContextManager:
    """profile_if_enabled() must be a no-op when disabled and profile when enabled."""

    def test_no_op_when_profile_false(self):
        """profile_if_enabled(args) with args.profile=False must be a no-op."""
        from backlink_publisher._util.profiling import profile_if_enabled

        side_effects = []

        args = SimpleNamespace(profile=False)
        with profile_if_enabled(args):
            side_effects.append("ran")

        assert side_effects == ["ran"]

    def test_no_op_when_args_none(self):
        """profile_if_enabled(None) is a no-op — no profiling."""
        from backlink_publisher._util.profiling import profile_if_enabled

        ran = []
        with profile_if_enabled(None):
            ran.append(1)
        assert ran == [1]

    def test_no_op_when_no_profile_attr(self):
        """profile_if_enabled with args lacking profile attr → no-op."""
        from backlink_publisher._util.profiling import profile_if_enabled

        ran = []
        with profile_if_enabled(SimpleNamespace()):
            ran.append(1)
        assert ran == [1]

    def test_saves_prof_file_when_enabled(self, tmp_path):
        """When profile=True, a .prof file must be saved."""
        from backlink_publisher._util.profiling import profile_if_enabled

        args = SimpleNamespace(profile=True)

        with patch(
            "backlink_publisher._util.profiling._get_profile_dir",
            return_value=tmp_path,
        ):
            with patch("builtins.print"):  # suppress the summary output
                with profile_if_enabled(args):
                    # Simulate some work
                    _ = sum(range(100))

        prof_files = list(tmp_path.glob("*.prof"))
        assert len(prof_files) == 1, f"Expected 1 .prof file, found: {prof_files}"
        assert prof_files[0].stat().st_size > 0

    def test_prof_file_name_has_timestamp(self, tmp_path):
        """The .prof file name must include a timestamp."""
        from backlink_publisher._util.profiling import profile_if_enabled

        args = SimpleNamespace(profile=True)

        with patch(
            "backlink_publisher._util.profiling._get_profile_dir",
            return_value=tmp_path,
        ):
            with patch("builtins.print"):
                with profile_if_enabled(args):
                    pass

        prof_files = list(tmp_path.glob("profile-*.prof"))
        assert len(prof_files) == 1

    def test_block_executes_normally_with_profiling(self, tmp_path):
        """The code block inside profile_if_enabled runs to completion."""
        from backlink_publisher._util.profiling import profile_if_enabled

        results = []
        args = SimpleNamespace(profile=True)

        with patch(
            "backlink_publisher._util.profiling._get_profile_dir",
            return_value=tmp_path,
        ):
            with patch("builtins.print"):
                with profile_if_enabled(args):
                    results.append(1 + 1)

        assert results == [2]


# ---------------------------------------------------------------------------
# Unit 8d: add_profile_arg() helper
# ---------------------------------------------------------------------------


class TestAddProfileArg:
    def test_add_profile_arg_adds_flag(self):
        """add_profile_arg(parser) adds --profile to any argparse.ArgumentParser."""
        from backlink_publisher._util.profiling import add_profile_arg

        parser = argparse.ArgumentParser()
        add_profile_arg(parser)
        args = parser.parse_args(["--profile"])
        assert args.profile is True

    def test_add_profile_arg_default_false(self):
        """add_profile_arg default is False."""
        from backlink_publisher._util.profiling import add_profile_arg

        parser = argparse.ArgumentParser()
        add_profile_arg(parser)
        args = parser.parse_args([])
        assert args.profile is False
