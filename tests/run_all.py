#!/usr/bin/env python3
"""Run every test in this directory and report which failed.

The tests are plain scripts rather than a pytest suite - each exits 0 or 1 and
prints what it checked - so this is the runner. It keeps going after a failure
instead of stopping at the first: when a change breaks three things, seeing all
three is worth more than seeing the earliest.

Run: python tests/run_all.py
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    paths = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    if not paths:
        print("[FAIL] No tests found. Is this the tests/ directory?")
        return 1

    print(f"--- {len(paths)} test file(s) on Python {sys.version.split()[0]} ---\n")

    failed = []
    started = time.time()
    for path in paths:
        name = os.path.basename(path)
        result = subprocess.run([sys.executable, path], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  [PASS] {name}")
        else:
            failed.append(name)
            print(f"  [FAIL] {name}  (exit {result.returncode})")
            # Only failures get their output, so a green run stays readable.
            for line in (result.stdout + result.stderr).rstrip().splitlines():
                print(f"         {line}")

    elapsed = time.time() - started
    print(f"\n--- {len(paths) - len(failed)}/{len(paths)} passed in {elapsed:.1f}s ---")
    if failed:
        print(f"[FAIL] {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
