"""Remove all writeas platform references from a worktree checkout.

Idempotent — safe to run on any branch. Skips files that don't exist
or already have no writeas references. Must be run from the worktree root.
"""

import os
import re
import sys
from pathlib import Path


def _log(msg: str) -> None:
    print(f"  {msg}")


def _replace(path: str, old: str, new: str) -> bool:
    """Replace old with new in file. Returns True if changed."""
    p = Path(path)
    if not p.exists():
        return False
    content = p.read_text(encoding="utf-8")
    if old not in content:
        return False
    content = content.replace(old, new, 1)
    p.write_text(content, encoding="utf-8")
    return True


def _replace_all(path: str, old: str, new: str) -> int:
    """Replace all occurrences of old with new. Returns count."""
    p = Path(path)
    if not p.exists():
        return 0
    content = p.read_text(encoding="utf-8")
    if old not in content:
        return 0
    count = content.count(old)
    content = content.replace(old, new)
    p.write_text(content, encoding="utf-8")
    return count


def _remove_lines(path: str, predicate):
    """Remove lines matching predicate. Returns count removed."""
    p = Path(path)
    if not p.exists():
        return 0
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = [l for l in lines if not predicate(l)]
    if len(new_lines) == len(lines):
        return 0
    p.writelines(new_lines)
    return len(lines) - len(new_lines)


def _delete_file(path: str) -> bool:
    p = Path(path)
    if p.exists():
        p.unlink()
        return True
    return False


def _grep_files(root: str, pattern: str, include: tuple = ()) -> list[str]:
    """Find files containing pattern under root."""
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip common dirs
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", ".pytest_cache")]
        for fn in filenames:
            if include and not fn.endswith(include):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, "rb") as f:
                    if pattern.encode() in f.read():
                        result.append(fp)
            except Exception:
                pass
    return result


def main(root: str) -> None:
    root = os.path.abspath(root)
    print(f"\n=== Cleaning writeas from {root} ===")

    # ── Phase 1: Delete adapter source files ──
    for f in [
        os.path.join(root, "src/backlink_publisher/publishing/adapters/writeas.py"),
    ]:
        if _delete_file(f):
            _log(f"DELETED {f}")

    # ── Phase 2: Fix instant_web.py (if WriteAsCdpAdapter exists) ──
    iw_path = os.path.join(root, "src/backlink_publisher/publishing/adapters/instant_web.py")
    if os.path.exists(iw_path) and "WriteAsCdpAdapter" in Path(iw_path).read_text(encoding="utf-8"):
        with open(iw_path) as f:
            lines = f.readlines()
        new_lines = []
        in_class = False
        for line in lines:
            if line.strip() == "class WriteAsCdpAdapter(Publisher):":
                in_class = True
                continue
            if in_class:
                if line.strip() and not line.startswith((" ", "\t")) and not line.strip().startswith("#"):
                    if line.strip().startswith(("class ", "__all__", "@")):
                        in_class = False
                        new_lines.append(line)
                    else:
                        in_class = False
                        new_lines.append(line)
                continue
            new_lines.append(line)
        # Fix __all__
        result = []
        for line in new_lines:
            if '"WriteAsCdpAdapter"' in line or "'WriteAsCdpAdapter'" in line:
                line = line.replace(', "WriteAsCdpAdapter"', "").replace(', \'WriteAsCdpAdapter\'', "")
                line = line.replace('"WriteAsCdpAdapter", ', "").replace("'WriteAsCdpAdapter', ", "")
            result.append(line)
        Path(iw_path).write_text("".join(result), encoding="utf-8")
        _log(f"FIXED {iw_path}")

    # ── Phase 3: Fix adapters/__init__.py ──
    ai_path = os.path.join(root, "src/backlink_publisher/publishing/adapters/__init__.py")
    if os.path.exists(ai_path):
        with open(ai_path) as f:
            content = f.read()

        orig = content

        # Remove imports (both formats)
        patterns = [
            "from .writeas import WriteAsAPIAdapter\n",
            "from .instant_web import TelegraphCdpAdapter, WriteAsCdpAdapter\n",
            "from .writeas import (\n    WriteAsAPIAdapter,\n)\n",
        ]
        for p in patterns:
            content = content.replace(p, "")

        # Fix the instant_web import line if WriteAsCdpAdapter still there
        content = re.sub(
            r"from \.instant_web import (?:TelegraphCdpAdapter, )?WriteAsCdpAdapter\n",
            "from .instant_web import TelegraphCdpAdapter\n",
            content,
        )

        # Remove writeas references in __all__ or ALL_CAPS lists
        content = re.sub(r'("writeas",\s*\n|"writeas"\n)', "", content)
        content = re.sub(r"('writeas',\s*\n|'writeas'\n)", "", content)

        # Remove register("writeas", ...) line
        content = re.sub(
            r'register\("writeas",\s*\w+.*?\n',
            "",
            content,
        )
        content = re.sub(
            r"register\('writeas',\s*\w+.*?\n",
            "",
            content,
        )

        # Remove verify_adapter_setup branches for writeas
        content = re.sub(
            r"    if platform == \"writeas\":\n.*?(?=\n    (?:if |elif |else:|#|$))",
            "",
            content,
        )
        # Remove the dispatch dict entry lines
        content = re.sub(
            r'\s*"writeas":\s*_offline_verify_writeas,\n',
            "",
            content,
        )
        content = re.sub(
            r'\s*"writeas":\s*_verify_writeas_live,\n',
            "",
            content,
        )

        # Remove _verify_writeas_live function and _offline_verify_writeas
        content = re.sub(
            r"\ndef _verify_writeas_live.*?\n\n(?=def |class )",
            "",
            content,
            flags=re.DOTALL,
        )
        content = re.sub(
            r"\ndef _offline_verify_writeas.*?\n\n(?=def |class )",
            "",
            content,
            flags=re.DOTALL,
        )

        # Remove any remaining writeas comment lines
        content = re.sub(r"^.*?(?:writeas|Write\.as|WriteAs).*?\n", "", content, flags=re.MULTILINE)

        if content != orig:
            Path(ai_path).write_text(content, encoding="utf-8")
            _log(f"FIXED {ai_path}")

    # ── Phase 4: Fix config/types.py ──
    ct_path = os.path.join(root, "src/backlink_publisher/config/types.py")
    if os.path.exists(ct_path):
        with open(ct_path) as f:
            content = f.read()
        orig = content

        # Remove WriteAsConfig class
        content = re.sub(
            r"@dataclass(?:\(frozen=True\))?\nclass WriteAsConfig:.*?\n\n",
            "",
            content,
            flags=re.DOTALL,
        )

        # Remove writeas field + docstring from Config class
        content = re.sub(
            r"""    writeas: WriteAsConfig \| None = None\n.*?\.\)\)""\n""",
            "",
            content,
            flags=re.DOTALL,
        )

        # Also try alternate pattern without the triple-quote ending
        content = re.sub(
            r"""    writeas: WriteAsConfig \| None = None\n.*?\(per SEC-3\)\.\)""\"\n""",
            "",
            content,
            flags=re.DOTALL,
        )

        # Remove writeas_token_path property
        content = re.sub(
            r"\n    @property\n    def writeas_token_path.*?\n",
            "",
            content,
            flags=re.DOTALL,
        )

        # Remove orphaned docstrings mentioning writeas
        content = re.sub(r'""".*?writeas.*?"""\n', "", content, flags=re.DOTALL)
        content = re.sub(r'""".*?Write\.as.*?"""\n', "", content, flags=re.DOTALL)

        # Remove any remaining writeas comment/docstring lines (non-aggressive)
        content = re.sub(r"^.*writeas.*$\n", "", content, flags=re.MULTILINE)

        if content != orig:
            Path(ct_path).write_text(content, encoding="utf-8")
            _log(f"FIXED {ct_path}")

    # ── Phase 5: Fix config/__init__.py ──
    ci_path = os.path.join(root, "src/backlink_publisher/config/__init__.py")
    if os.path.exists(ci_path):
        with open(ci_path) as f:
            content = f.read()
        orig = content
        for item in ["WriteAsConfig", "load_writeas_token", "save_writeas_token"]:
            content = re.sub(
                rf'    "{item}",\n',
                "",
                content,
            )
            content = re.sub(
                rf"    '{item}',\n",
                "",
                content,
            )
            content = re.sub(
                rf"    {item},\n",
                "",
                content,
            )
        if content != orig:
            Path(ci_path).write_text(content, encoding="utf-8")
            _log(f"FIXED {ci_path}")

    # ── Phase 6: Fix config/tokens.py ──
    t_path = os.path.join(root, "src/backlink_publisher/config/tokens.py")
    if os.path.exists(t_path):
        with open(t_path) as f:
            content = f.read()
        orig = content
        content = re.sub(
            r'        \("writeas", "writeas-token\.json"\),\n',
            "",
            content,
        )
        content = re.sub(
            r"\ndef load_writeas_token.*?\n\n",
            "",
            content,
            flags=re.DOTALL,
        )
        content = re.sub(
            r"\ndef save_writeas_token.*?\n\n",
            "",
            content,
            flags=re.DOTALL,
        )
        if content != orig:
            Path(t_path).write_text(content, encoding="utf-8")
            _log(f"FIXED {t_path}")

    # ── Phase 7: Fix config/loader.py ──
    l_path = os.path.join(root, "src/backlink_publisher/config/loader.py")
    if os.path.exists(l_path):
        with open(l_path) as f:
            content = f.read()
        orig = content
        content = re.sub(r"\n    WriteAsConfig,\n", "\n", content)
        content = re.sub(
            r"\n    writeas_section = .*?\n.*?writeas.*?\n(?:.*?writeas.*?\n)*",
            "",
            content,
        )
        # Also remove writeas=writeas from Config() call
        content = content.replace(",\n        writeas=writeas,", "")
        content = content.replace("        writeas=writeas,\n", "")
        if content != orig:
            Path(l_path).write_text(content, encoding="utf-8")
            _log(f"FIXED {l_path}")

    # ── Phase 8: Fix config/writer.py ──
    w_path = os.path.join(root, "src/backlink_publisher/config/writer.py")
    if os.path.exists(w_path):
        with open(w_path) as f:
            content = f.read()
        orig = content
        content = re.sub(r"\n    WriteAsConfig,\n", "\n", content)
        content = re.sub(
            r"\n    writeas_config: WriteAsConfig \| None = None,\n",
            "\n",
            content,
        )
        content = re.sub(
            r"writeas_cfg = .*?\n",
            "",
            content,
        )
        content = re.sub(
            r"    if writeas_cfg is not None:\n.*?\n.*?\n.*?\n",
            "",
            content,
        )
        if content != orig:
            Path(w_path).write_text(content, encoding="utf-8")
            _log(f"FIXED {w_path}")

    # ── Phase 9: Fix config/_toml_utils.py ──
    tu_path = os.path.join(root, "src/backlink_publisher/config/_toml_utils.py")
    if os.path.exists(tu_path):
        with open(tu_path) as f:
            content = f.read()
        orig = content
        content = content.replace('"writeas", ', "")
        content = content.replace('"writeas"\n', "\n")
        content = content.replace('"writeas"}', "}")
        if content != orig:
            Path(tu_path).write_text(content, encoding="utf-8")
            _log(f"FIXED {tu_path}")

    # ── Phase 10: Fix webui_app/binding_status.py ──
    bs_path = os.path.join(root, "webui_app/binding_status.py")
    if os.path.exists(bs_path):
        with open(bs_path) as f:
            content = f.read()
        orig = content
        content = content.replace('frozenset({"writeas"})', "frozenset()")
        if content != orig:
            Path(bs_path).write_text(content, encoding="utf-8")
            _log(f"FIXED {bs_path}")

    # ── Phase 11: Fix docstring-only refs ──
    replacements = [
        ("image_gen/types.py", "``writeas``) can fall back", "``telegraph``) can fall back"),
        ("image_gen/types.py", "Write.as-style", "Telegraph-style"),
        ("velog_graphql.py", "writeas-style", "explicit"),
        ("hashnode.py", "writeas-style", "explicit"),
        ("hashnode.py", "fallback the dispatcher already wires for writeas.", "fallback the dispatcher already wires for Telegraph."),
        ("token_paste.py", "or browser-mediated. Currently allowlists ghpages only — writeas was", "or browser-mediated. Currently allowlists ghpages only — legacy was"),
    ]
    for file_path, old, new in replacements:
        fp = os.path.join(root, "src/backlink_publisher", file_path)
        if not os.path.exists(fp):
            fp = os.path.join(root, "webui_app", file_path)
        if _replace(fp, old, new):
            _log(f"FIXED {fp}")

    # Also fix _velog_graphql_impl.py if it exists
    for fp in [
        os.path.join(root, "src/backlink_publisher/publishing/adapters/_velog_graphql_impl.py"),
    ]:
        if _replace(fp, "writeas-style", "explicit"):
            _log(f"FIXED {fp}")

    # Fix template files
    for fp in [os.path.join(root, "webui_app/templates/_settings_channel_token_paste.html")]:
        if _replace(fp, "writeas", "telegraph"):
            _log(f"FIXED {fp}")

    # ── Phase 12: Delete writeas test files ──
    for f in [
        "tests/test_adapter_writeas.py",
        "tests/test_writeas_banner.py",
    ]:
        fp = os.path.join(root, f)
        if _delete_file(fp):
            _log(f"DELETED {fp}")

    # ── Phase 13: Fix test files ──
    # Go through all test files and remove writeas references
    for fp in _grep_files(os.path.join(root, "tests"), "writeas", include=(".py",)):
        with open(fp) as f:
            content = f.read()
        orig = content

        # Remove writeas import lines
        content = re.sub(r"^.*?from.*?writeas.*?import.*?\n", "", content, flags=re.MULTILINE)
        content = re.sub(r"^.*?import.*?writeas.*?\n", "", content, flags=re.MULTILINE)
        content = re.sub(r"^.*?WriteAsConfig.*?\n", "", content, flags=re.MULTILINE)
        content = re.sub(r"^.*?load_writeas_token.*?\n", "", content, flags=re.MULTILINE)
        content = re.sub(r"^.*?save_writeas_token.*?\n", "", content, flags=re.MULTILINE)

        # Remove writeas test functions/classes
        content = re.sub(
            r"    def test_writeas_.*?\n.*?(?=\n    def |\nclass |$)",
            "",
            content,
            flags=re.DOTALL,
        )
        content = re.sub(
            r"class TestWriteAs.*?\n.*?(?=\nclass |\Z)",
            "",
            content,
            flags=re.DOTALL,
        )
        content = re.sub(
            r"class TestWriteas.*?\n.*?(?=\nclass |\Z)",
            "",
            content,
            flags=re.DOTALL,
        )

        # Replace writeas strings with telegraph in parametrize/test data
        content = content.replace('"writeas"', '"telegraph"')
        content = content.replace("'writeas'", "'telegraph'")
        content = content.replace("writeas", "telegraph")

        if content != orig:
            Path(fp).write_text(content, encoding="utf-8")
            _log(f"FIXED {fp}")

    # ── Phase 14: Fix AGENTS.md ──
    amd = os.path.join(root, "AGENTS.md")
    if os.path.exists(amd):
        with open(amd) as f:
            content = f.read()
        orig = content
        content = content.replace(
            "hashnode, writeas, …",
            "hashnode, …",
        )
        content = re.sub(
            r"- \*\*writeas\*\*:.*?\n",
            "",
            content,
        )
        # Clean up extra blank lines
        content = re.sub(r"\n{3,}", "\n\n", content)
        if content != orig:
            Path(amd).write_text(content, encoding="utf-8")
            _log(f"FIXED {amd}")

    # ── Phase 15: Fix monolith_budget.toml ──
    mb = os.path.join(root, "monolith_budget.toml")
    if os.path.exists(mb):
        with open(mb) as f:
            content = f.read()
        orig = content
        # Lower ceiling if adapters/__init__.py is tracked and has writeas
        content = re.sub(
            r'rationale = "Registry.*?writeas.*?"\n',
            'rationale = "Registry + re-export hub. Writeas platform fully removed."\n',
            content,
        )
        # Remove any line with writeas
        content = re.sub(r"^.*?writeas.*?$\n", "", content, flags=re.MULTILINE)
        if content != orig:
            Path(mb).write_text(content, encoding="utf-8")
            _log(f"FIXED {mb}")

    print(f"Done cleaning {root}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/remove-writeas.py <worktree-root>")
        sys.exit(1)
    main(sys.argv[1])
