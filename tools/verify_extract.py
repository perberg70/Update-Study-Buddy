#!/usr/bin/env python3
"""Work out which archive edx_export/ actually came from, and whether it is clean.

Two defects made this ambiguous: extract_edx.py recorded nothing about its
input, and extraction never cleared the destination, so files from an earlier
archive survive alongside a newer one. A mixed directory yields documents whose
structure comes from one export and whose content comes from another.

Read-only. Compares every course*.tar.gz it can find against what is on disk.

Usage:
    python tools/verify_extract.py
    python tools/verify_extract.py --tar path\\to\\course.xyz.tar.gz
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import COURSE_STRUCTURE_PATH, EXTRACT_DIR  # noqa: E402


def stamp(path):
    try:
        return dt.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "?"


def sha256_head(path, limit=8 * 1024 * 1024):
    """Hash of the first few MB - enough to tell two archives apart cheaply."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            digest.update(fh.read(limit))
    except OSError:
        return "?"
    return digest.hexdigest()[:12]


def archive_members(tar_path):
    """{relative path: size} for regular files in the archive."""
    out = {}
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    out[member.name.replace("\\", "/").lstrip("./")] = member.size
    except Exception as exc:
        print(f"  [FAIL] cannot read {tar_path}: {exc}")
    return out


def disk_files(extract_dir):
    out = {}
    for root, _dirs, files in os.walk(extract_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, extract_dir).replace("\\", "/")
            try:
                out[rel] = os.path.getsize(full)
            except OSError:
                out[rel] = -1
    return out


def compare(members, on_disk):
    """How well an archive explains what is on disk."""
    # Archive paths may or may not carry a leading directory; try both.
    def variants(path):
        yield path
        if "/" in path:
            yield path.split("/", 1)[1]

    matched = missing = 0
    explained = set()
    for path, size in members.items():
        hit = None
        for variant in variants(path):
            if variant in on_disk:
                hit = variant
                break
        if hit is None:
            missing += 1
            continue
        explained.add(hit)
        if on_disk[hit] == size:
            matched += 1
    return matched, missing, explained


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tar", action="append", default=[],
                        help="archive to test (repeatable); default: course*.tar.gz here")
    parser.add_argument("--extract-dir", default=EXTRACT_DIR)
    args = parser.parse_args()

    print("=" * 70)
    print("  Candidate archives")
    print("=" * 70)
    candidates = args.tar or sorted(glob.glob("course*.tar.gz"))
    if not candidates:
        print("  none found in this folder (pass --tar to point at one elsewhere)")
    for path in sorted(candidates, key=lambda p: os.path.getmtime(p), reverse=True):
        size = os.path.getsize(path) / (1024 * 1024)
        print(f"  {os.path.basename(path):34} {size:8.1f} MB  {stamp(path)}  "
              f"sha:{sha256_head(path)}")
    if candidates and not args.tar:
        newest = max(candidates, key=lambda p: os.path.getmtime(p))
        print(f"\n  extract_edx.py with no --tar would pick: {os.path.basename(newest)}")
        if len(candidates) > 1:
            print("  [!] More than one archive present. Selection is by modification")
            print("      time, which OneDrive changes on sync - it does not mean newest")
            print("      export. Always pass --tar to be certain.")

    print()
    print("=" * 70)
    print(f"  What is in {args.extract_dir}")
    print("=" * 70)
    if not os.path.isdir(args.extract_dir):
        print(f"  {args.extract_dir} does not exist - nothing extracted yet.")
        return 0

    on_disk = disk_files(args.extract_dir)
    print(f"  {len(on_disk)} file(s) on disk")
    if os.path.exists(COURSE_STRUCTURE_PATH):
        print(f"  {COURSE_STRUCTURE_PATH} last written {stamp(COURSE_STRUCTURE_PATH)}")
        try:
            with open(COURSE_STRUCTURE_PATH, encoding="utf-8") as fh:
                source = json.load(fh).get("_source")
            if source:
                print(f"  recorded source: {source.get('tar')}")
                print(f"                   sha:{source.get('sha256_head')} "
                      f"extracted {source.get('extracted_at')}")
            else:
                print("  no provenance recorded (written before extract_edx.py tracked it)")
        except Exception:
            pass

    print()
    print("=" * 70)
    print("  Which archive explains the extracted files")
    print("=" * 70)
    best, best_score = None, -1
    for path in candidates:
        members = archive_members(path)
        if not members:
            continue
        matched, missing, explained = compare(members, on_disk)
        stale = len(on_disk) - len(explained)
        pct = 100.0 * matched / len(members) if members else 0
        print(f"\n  {os.path.basename(path)}")
        print(f"    {len(members)} file(s) in archive")
        print(f"    {matched} present on disk with identical size ({pct:.0f}%)")
        print(f"    {missing} in archive but absent from disk")
        print(f"    {stale} on disk that this archive does NOT contain")
        if pct > best_score:
            best, best_score = path, pct

        if stale and pct > 90:
            print("    [!] Nearly all of this archive is present, but extra files remain.")
            print("        Those are left over from an earlier extraction - the directory")
            print("        was never cleared. Content can be a mix of two course versions.")

    if best:
        print()
        print(f"  Best match: {os.path.basename(best)} ({best_score:.0f}% of its files "
              "present with matching size)")
        print("  Re-extract to remove any doubt (the directory is cleared by default):")
        print(f"    python extract_edx.py --tar {os.path.basename(best)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
