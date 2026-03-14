#!/usr/bin/env python3
"""Fail if any tracked Python source contains non-ASCII characters."""

from __future__ import annotations

import subprocess
from pathlib import Path


def tracked_python_files() -> list[Path]:
    out = subprocess.check_output(["git", "ls-files", "*.py"], text=True)
    return [Path(line.strip()) for line in out.splitlines() if line.strip()]


def main() -> int:
    issues: list[tuple[Path, int, str]] = []

    for path in tracked_python_files():
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if any(ord(ch) > 127 for ch in line):
                snippet = line.encode("ascii", errors="backslashreplace").decode("ascii")
                issues.append((path, line_no, snippet[:140]))

    if issues:
        print("[FAIL] Non-ASCII characters found in Python source:")
        for path, line_no, snippet in issues:
            print(f"  - {path}:{line_no}: {snippet}")
        return 1

    print("[OK] Python source is ASCII-only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
