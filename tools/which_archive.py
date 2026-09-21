#!/usr/bin/env python3
"""Say which course archive is on hand, and which one this run will read.

Nothing is unpacked any more, so "what is in edx_export/, and where did it come
from?" no longer has to be reconstructed - course_structure.json names its
source archive outright. What is still worth checking is the choice itself:
with no --tar, extract_edx.py takes the newest course*.tar.gz by modification
time, and OneDrive updates mtimes on sync, so "newest" does not mean "newest
export".

Read-only.

Usage:
    python tools/which_archive.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import COURSE_STRUCTURE_PATH  # noqa: E402
from olx_archive import CourseArchive, CourseArchiveError  # noqa: E402


def stamp(path):
    try:
        return dt.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "?"


def describe(path):
    """One line per archive: size, mtime, hash, course root, file count."""
    size = os.path.getsize(path) / (1024 * 1024)
    try:
        archive = CourseArchive(path)
    except CourseArchiveError as exc:
        return f"  {os.path.basename(path):34} {size:8.1f} MB  {stamp(path)}  [{exc}]"
    fingerprint = archive.fingerprint()
    return (f"  {os.path.basename(path):34} {size:8.1f} MB  {stamp(path)}  "
            f"sha:{fingerprint['sha256_head']}\n"
            f"  {'':34} root: {fingerprint['course_root']}, "
            f"{fingerprint['files_in_archive']} file(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tar", action="append", default=[],
                        help="archive to inspect (repeatable); "
                             "default: course*.tar.gz in this folder")
    args = parser.parse_args()

    print("=" * 70)
    print("  Archives found")
    print("=" * 70)
    candidates = args.tar or sorted(glob.glob("course*.tar.gz"))
    if not candidates:
        print("  none in this folder (pass --tar to point at one elsewhere)")
    for path in sorted(candidates, key=lambda p: os.path.getmtime(p), reverse=True):
        print(describe(path))

    if candidates and not args.tar and len(candidates) > 1:
        newest = max(candidates, key=lambda p: os.path.getmtime(p))
        print(f"\n  With no --tar, extract_edx.py would pick: {os.path.basename(newest)}")
        print("  [!] More than one archive present. The pick is by modification")
        print("      time, which OneDrive changes on sync - it does not mean newest")
        print("      export. Pass --tar to be certain.")

    print()
    print("=" * 70)
    print(f"  What {COURSE_STRUCTURE_PATH} records")
    print("=" * 70)
    if not os.path.exists(COURSE_STRUCTURE_PATH):
        print(f"  {COURSE_STRUCTURE_PATH} does not exist - run extract_edx.py.")
        return 0

    print(f"  last written {stamp(COURSE_STRUCTURE_PATH)}")
    try:
        with open(COURSE_STRUCTURE_PATH, encoding="utf-8") as fh:
            structure = json.load(fh)
    except Exception as exc:
        print(f"  [FAIL] cannot read it: {exc}")
        return 1

    source = structure.get("_source") or {}
    if not source.get("tar"):
        print("  no source recorded (written before provenance was tracked).")
        print("  Re-run: python extract_edx.py --tar <archive>")
        return 0

    print(f"  source:  {source['tar']}")
    print(f"  sha:     {source.get('sha256_head', '?')}")
    print(f"  read at: {source.get('read_at', '?')}")
    print(f"  root:    {source.get('course_root', '?')}, "
          f"{source.get('files_in_archive', '?')} file(s)")
    print(f"  parsed:  {len(structure.get('chapters', []))} chapter(s)")

    # Every later step reads this same archive, so its absence or replacement is
    # the one thing that can still put a run on different content than it parsed.
    if not os.path.exists(source["tar"]):
        print("\n  [!] That archive is no longer at the recorded path. The later")
        print("      steps would fall back to the newest match. Re-run")
        print("      extract_edx.py --tar <archive> before building anything.")
        return 1

    try:
        now = CourseArchive(source["tar"]).fingerprint()
    except CourseArchiveError as exc:
        print(f"\n  [!] That archive is unreadable now: {exc}")
        return 1

    if now["sha256_head"] != source.get("sha256_head"):
        print("\n  [!] The file at that path has changed since it was parsed")
        print(f"      (recorded sha:{source.get('sha256_head')}, now sha:{now['sha256_head']}).")
        print("      Re-run extract_edx.py before building anything from it.")
        return 1

    print("\n  [OK] The recorded archive is present and unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
