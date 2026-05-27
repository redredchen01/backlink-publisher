"""3-way drift guard for the browser-bind channel set (Plan 2026-05-27-008, U2).

`CHANNELS` (the low-level traversal-defense frozenset) must stay identical to
`set(RECIPES)` (the recipe registry) AND to the set of recipe *files on disk*.

`CHANNELS == set(RECIPES)` is already locked by
``tests/test_bind_channel_recipes.py::test_recipes_dict_covers_exactly_channels``.
The leg that was *unguarded* — and that this module adds — is the filesystem
one: dropping a ``recipes/<name>.py`` file without a ``RECIPES`` entry (or
leaving a stale entry after deleting the file) previously went undetected.

Why a guard and not derivation: ``CHANNELS`` cannot simply be
``frozenset(RECIPES)`` because ``channels/__init__`` is a lightweight primitive
imported by the bind driver, ``AuthExpiredError``, and CLI argparse, while a
recipe module (``medium.py``) imports the driver and config loader — deriving
would invert that layering and risk a ``channels -> recipes -> medium ->
driver -> channels`` import cycle. So the three representations stay separate
and this guard keeps them honest. Test-time only (never an import-time assert),
per ``invert-drift-check-when-invariant-becomes-dynamic``.
"""

from pathlib import Path

from backlink_publisher.cli._bind import recipes as recipes_pkg
from backlink_publisher.cli._bind.channels import CHANNELS
from backlink_publisher.cli._bind.recipes import RECIPES


def _recipe_file_stems(directory: Path) -> set[str]:
    """Channel recipe files in ``directory``: ``<name>.py`` excluding
    ``__init__.py`` and underscore-prefixed helpers/selectors."""
    return {
        p.stem
        for p in directory.glob("*.py")
        if not p.name.startswith("_") and p.stem != "__init__"
    }


def test_channels_recipes_and_files_all_agree():
    """The 3-way invariant: CHANNELS == RECIPES keys == recipe files on disk."""
    recipes_dir = Path(recipes_pkg.__file__).parent
    on_disk = _recipe_file_stems(recipes_dir)
    assert on_disk == set(RECIPES) == set(CHANNELS), (
        f"channel drift: files={sorted(on_disk)} "
        f"RECIPES={sorted(RECIPES)} CHANNELS={sorted(CHANNELS)} — add the new "
        "recipe to RECIPES (and CHANNELS), or remove the stale file/entry"
    )


def test_stem_extraction_excludes_helpers_and_flags_extras(tmp_path):
    """R7 red->green honesty: prove the file-scan would FAIL on a planted
    extra recipe and correctly excludes ``__init__``/underscore helpers, so the
    guard above is not tautological."""
    (tmp_path / "velog.py").write_text("RECIPE = None\n")
    (tmp_path / "medium.py").write_text("RECIPE = None\n")
    (tmp_path / "__init__.py").write_text("")
    (tmp_path / "_mastodon_selectors.py").write_text("")  # helper — excluded
    (tmp_path / "newchannel.py").write_text("RECIPE = None\n")  # planted drift

    stems = _recipe_file_stems(tmp_path)

    assert stems == {"velog", "medium", "newchannel"}  # helpers/init excluded
    # The planted file is present but absent from a {velog, medium} registry —
    # the real assertion's `==` would go red, which is the drift we want caught.
    assert stems != {"velog", "medium"}
