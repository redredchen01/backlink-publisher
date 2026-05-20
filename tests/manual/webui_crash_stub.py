#!/usr/bin/env python3
"""Crash-simulating stub for the launcher restart-loop smoke tests.

NOT collected by pytest (path tests/manual/, name doesn't start with test_).
Pure stdlib — no Flask, no backlink_publisher imports — so failures here are
unambiguously the stub's STUB_MODE behavior, not import errors.

Usage from the launcher:
    WEBUI_SCRIPT=tests/manual/webui_crash_stub.py STUB_MODE=fail-fast \\
        bash 启动WebUI.command

STUB_MODE values:
    fail-fast        Print one line and raise RuntimeError immediately (exit 1).
    fail-after-N     Sleep STUB_FAIL_DELAY seconds, then raise (exit 1).
    ignore-sigterm   Trap SIGTERM as no-op and sleep 3600s; only SIGKILL ends it.
"""

import os
import signal
import sys
import time

MODE = os.environ.get("STUB_MODE", "fail-fast")
PORT = os.environ.get("PORT", "?")
print(f"[stub] mode={MODE} port={PORT}", flush=True)

if MODE == "fail-fast":
    print("[stub] crashing immediately", flush=True)
    raise RuntimeError("stub crash")

if MODE == "fail-after-N":
    delay = float(os.environ.get("STUB_FAIL_DELAY", "5"))
    print(f"[stub] will crash after {delay}s", flush=True)
    time.sleep(delay)
    raise RuntimeError(f"stub crash after {delay}s")

if MODE == "ignore-sigterm":
    signal.signal(signal.SIGTERM, lambda s, f: None)
    print("[stub] ignoring SIGTERM, sleeping 3600s (use SIGKILL to end)", flush=True)
    time.sleep(3600)
    sys.exit(0)

print(f"[stub] unknown STUB_MODE={MODE}", file=sys.stderr)
sys.exit(2)
