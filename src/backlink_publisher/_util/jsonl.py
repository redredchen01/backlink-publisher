"""JSONL read/write utilities for the backlink pipeline."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, Iterator

from backlink_publisher._util.errors import emit_error
from backlink_publisher.persistence.safe_write import atomic_write

MAX_LINE_LENGTH = 65536  # 64 KB per line


def read_jsonl(
    source: Iterable[str] | None = None,
    strict: bool = True,
) -> Iterator[dict[str, Any]]:
    """Read JSONL from an iterable of lines (default: stdin).

    Each non-empty line is parsed as JSON.

    When *strict* is ``True`` (default), malformed JSON or empty input
    produces a diagnostic on stderr and exits with code 2.  When
    ``False``, malformed lines are skipped with a warning and empty
    input yields nothing.
    """
    if source is None:
        source = sys.stdin

    line_num = 0
    has_data = False

    for raw_line in source:
        line = raw_line.rstrip("\n\r")
        if not line:
            continue
        has_data = True
        line_num += 1

        if len(line) > MAX_LINE_LENGTH:
            diagnostic = f"line {line_num}: exceeds maximum line length ({MAX_LINE_LENGTH})"
            if strict:
                emit_error(diagnostic, exit_code=2)
            else:
                print(f"WARN: {diagnostic}", file=sys.stderr)
                continue

        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            diagnostic = f"line {line_num}: malformed JSON: {exc}"
            if strict:
                emit_error(diagnostic, exit_code=2)
            else:
                print(f"WARN: {diagnostic}", file=sys.stderr)
                continue

        if not isinstance(obj, dict):
            diagnostic = f"line {line_num}: expected a JSON object, got {type(obj).__name__}"
            if strict:
                emit_error(diagnostic, exit_code=2)
            else:
                print(f"WARN: {diagnostic}", file=sys.stderr)
                continue

        yield obj

    if not has_data:
        if strict:
            emit_error("empty input: no JSONL rows provided", exit_code=2)


def write_jsonl(rows: Iterable[dict[str, Any]], dest: Any = None) -> None:
    """Write JSONL to an iterable (default: stdout).

    Each row is serialized as a single JSON line.
    """
    if dest is None:
        dest = sys.stdout

    for row in rows:
        dest.write(json.dumps(row, ensure_ascii=False) + "\n")
    dest.flush()


def atomic_write_jsonl(rows: Iterable[dict[str, Any]], path: Path, mode: int = 0o600) -> None:
    """Write JSONL to path atomically via a sibling .tmp/.new file and replace.

    Ensures readers see either the old file or the fully written new one,
    never a partially written or torn file.
    """
    buffer = StringIO()
    write_jsonl(rows, buffer)
    atomic_write(path, buffer.getvalue(), mode)