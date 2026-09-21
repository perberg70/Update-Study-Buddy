#!/usr/bin/env python3
"""Start a course update: find the export, parse it, and say what to do next.

One command to run at the beginning of every update, so the two questions that
have caused the most trouble - *where does the export go* and *which one is
being read* - are answered before any work happens rather than after it.

It creates course_exports/ if missing, finds the archive, refuses to guess
between two, records which one it read into course_structure.json, and prints
the commands to run next. Nothing is downloaded, uploaded or deleted.

Usage:
    python start_run.py
    python start_run.py --tar course_exports/course.hp_m6v88.tar.gz
    python start_run.py --module 1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from config import (COURSE_STRUCTURE_PATH, EXPORTS_DIR, describe_export,
                    find_exports, looks_like_import_archive)
from olx_archive import CourseArchive, CourseArchiveError

RULE = "=" * 70


def heading(text):
    print(f"\n{RULE}\n  {text}\n{RULE}")


def ensure_exports_dir():
    """Create course_exports/ so there is somewhere obvious to put the file."""
    try:
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[WARN] Could not create {EXPORTS_DIR}: {exc}")
        return False
    return True


def locate_export(explicit_tar):
    """The archive to read, or None plus instructions printed."""
    if explicit_tar:
        if not os.path.exists(explicit_tar):
            print(f"[FAIL] {explicit_tar} not found.")
            return None
        return explicit_tar

    from_env = os.getenv("EDX_TAR_PATH")
    if from_env:
        print(f"[OK] EDX_TAR_PATH is set: {from_env}")
        if not os.path.exists(from_env):
            print("[FAIL] ...but no file is there. Unset it, or point it at the export.")
            return None
        return from_env

    chosen, here, elsewhere = find_exports()

    if not here:
        print("[FAIL] Nothing there yet.\n")
        print("  Export the course from edX Studio (Tools -> Export), put the")
        print("  course.<something>.tar.gz in the folder above, and run this again.")
        print("  It stays out of git: *.tar.gz is ignored.")
        return None

    if len(here) > 1:
        print(f"[FAIL] {len(here)} exports in {chosen}, and none was named:\n")
        for path in here:
            print(f"      {describe_export(path)}")
        print("\n  Keep the one you want and move the others out, or name one:")
        print(f"      python start_run.py --tar \"{here[0]}\"")
        print("\n  Choosing by date is not safe: OneDrive rewrites modification")
        print("  times on sync, so the newest file is not the newest export.")
        return None

    path = here[0]
    print(f"[OK] Found one export in {chosen}")
    print(f"     {describe_export(path)}")

    reason = looks_like_import_archive(path)
    if reason:
        print("\n[WARN] This looks like an edXUpdater import archive, not a course")
        print(f"       export - {reason}.")
        print("       That archive is built to upload TO edX (it updates the course")
        print("       home page). This tool reads an export pulled FROM edX Studio:")
        print("       Tools -> Export. They are different trees, and building study")
        print("       material from the wrong one is hard to spot afterwards.")
        print("       If that is what you meant, carry on; otherwise re-export.")

    # An archive somewhere else is not used, and must not be silently ignored.
    if elsewhere:
        print(f"\n[WARN] {len(elsewhere)} other archive(s) are being ignored, because")
        print(f"       {chosen} takes precedence:")
        for other in elsewhere:
            print(f"         {other}")
        print("       Move them into course_exports/ or delete them to avoid doubt.")

    return str(path)


def parse_export(tar_path, structure_path):
    """Run extract_edx.py so provenance is recorded the same way every time."""
    result = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "extract_edx.py"),
         "--tar", tar_path, "--out", structure_path],
        check=False)
    return result.returncode == 0


def summarise(structure_path, wanted_module=None):
    """Modules in the parsed course, so the next command can be copied."""
    try:
        with open(structure_path, encoding="utf-8") as fh:
            structure = json.load(fh)
    except Exception as exc:
        print(f"[WARN] Could not read {structure_path}: {exc}")
        return None

    # Imported here so start_run works even without reportlab installed.
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
        from build_module_pdf import group_modules, module_label
    except Exception:
        return None

    modules = group_modules(structure.get("chapters", []))
    print(f"\n  {len(modules)} module(s):")
    for module in modules:
        marker = "  <-- " if wanted_module and (module["number"] or "") == wanted_module else "      "
        print(f"    {(module['number'] or '-'):>3}{marker}{module_label(module)}")
    return modules


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tar", dest="tar_path",
                        help="name the export explicitly instead of searching")
    parser.add_argument("--module", help="the module you intend to build next")
    parser.add_argument("--out", dest="structure_path", default=COURSE_STRUCTURE_PATH)
    args = parser.parse_args()

    heading("1. Where the course export goes")
    ensure_exports_dir()
    print(f"  {EXPORTS_DIR}\n")
    tar_path = locate_export(args.tar_path)
    if tar_path is None:
        return 1

    heading("2. Reading it")
    try:
        archive = CourseArchive(tar_path)
    except CourseArchiveError as exc:
        print(f"[FAIL] That file is not a readable course archive:\n  {exc}")
        print("\n  An export from edX Studio has course.xml at its root or one")
        print("  directory down. Re-export if this looks wrong.")
        return 1

    fingerprint = archive.fingerprint()
    print(f"[OK] {os.path.basename(tar_path)}")
    print(f"     sha:{fingerprint['sha256_head']}  "
          f"course root: {fingerprint['course_root']}  "
          f"{fingerprint['files_in_archive']} file(s)")

    heading("3. Parsing the structure")
    if not parse_export(tar_path, args.structure_path):
        print("[FAIL] Parsing failed - see the error above.")
        return 1

    modules = summarise(args.structure_path, (args.module or "").strip() or None)

    heading("4. What to run next")
    module = (args.module or "").strip() or (
        modules[0]["number"] if modules and modules[0].get("number") else "1")
    print(f"""  Build a module PDF, with the videos' speech in it:

      python tools/fetch_youtube_transcripts.py --module {module}
      python tools/transcribe_videos.py --module {module} --dry-run
      python tools/transcribe_videos.py --module {module}
      python tools/build_module_pdf.py --module {module}

  Every step reads the export recorded just now, and refuses if that stops
  being true. To check the environment first:

      python preflight.py
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
