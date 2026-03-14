#!/usr/bin/env python3
"""Fail if Git conflict markers are present in tracked text files."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def tracked_files() -> list[Path]:
    out = subprocess.check_output(["git", "ls-files"], text=True)
    return [Path(line.strip()) for line in out.splitlines() if line.strip()]


def is_probably_text(path: Path) -> bool:
    binary_exts = {
        ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".mp3", ".mp4", ".zip", ".gz", ".tar", ".xlsx", ".docx"
    }
    return path.suffix.lower() not in binary_exts


def main() -> int:
    issues: list[tuple[Path, int, str]] = []
    for path in tracked_files():
        if not path.exists() or not is_probably_text(path):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for idx, line in enumerate(lines, start=1):
            if line.startswith(MARKERS):
                issues.append((path, idx, line[:20]))

    if issues:
        print("[FAIL] Conflict markers detected:")
        for p, ln, txt in issues:
            print(f"  - {p}:{ln}: {txt}")
        return 1

    print("[OK] No conflict markers found in tracked text files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
