#!/usr/bin/env python3
"""What files the course carries in static/, and which module each belongs to.

Course authors upload handbooks, briefs and reading lists in Studio under
Files & Uploads; they travel inside the export in static/ and are linked from
the HTML with /static/<name>. organize_content.py copies some of them to
Global_Assets/ and nothing reads that folder afterwards, so today they reach
neither the notebook nor the module PDFs.

Putting their text into the module PDFs needs two things this answers:

  * which module an asset belongs to - static/ is flat, so the only honest
    answer is which module's HTML links to it;
  * whether the text can be got out at all, which depends on the file type
    and, for PDFs, on pdfminer.six being installed.

Read-only. Nothing is written, downloaded or uploaded.

Usage:
    python tools/asset_report.py
    python tools/asset_report.py --module 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import COURSE_STRUCTURE_PATH  # noqa: E402
from olx_archive import (CourseArchiveError, describe_source,  # noqa: E402
                         open_course_archive)
from build_module_pdf import group_modules, module_label  # noqa: E402

# /static/name.ext as OLX writes it in href/src, ignoring any ?query or #frag.
STATIC_REF_RE = re.compile(r"/static/([^\"'\s>?#\\]+)")

# Transcript sidecars are already handled by the transcript resolver; counting
# them here would bury the documents this is looking for.
TRANSCRIPT_SUFFIXES = (".srt", ".sjson", ".vtt")

TEXT_READY = {".txt": "stdlib", ".md": "stdlib", ".html": "stdlib",
              ".docx": "stdlib (zip + xml)", ".pdf": "needs pdfminer.six",
              ".xlsx": "stdlib (zip + xml), but a sheet reads poorly as prose"}


def pdf_reader_available():
    for module in ("pdfminer", "pypdf"):
        try:
            __import__(module)
            return module
        except Exception:
            continue
    return ""


def module_assets(module, archive):
    """{asset name: [components that link to it]} for one module's HTML."""
    found = {}
    for chapter in module["chapters"]:
        if chapter.get("hidden"):
            continue
        for seq in chapter.get("sequentials", []):
            if seq.get("hidden"):
                continue
            for vert in seq.get("verticals", []):
                if vert.get("hidden"):
                    continue
                for comp in vert.get("components", []):
                    if comp.get("type") != "html":
                        continue
                    body = archive.read_text(f"html/{comp['url_name']}.html")
                    if not body:
                        continue
                    for name in STATIC_REF_RE.findall(body):
                        found.setdefault(name, []).append(
                            f"{seq.get('title', '?')} / {vert.get('title', '?')}")
    return found


def classify(name, archive, pdf_lib):
    """(kind, how the text could be extracted) for one static file."""
    ext = os.path.splitext(name)[1].lower()
    if name.lower().endswith(TRANSCRIPT_SUFFIXES):
        return "transcript", "already used by the transcript resolver"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        return "image", "no text - would need OCR"
    how = TEXT_READY.get(ext)
    if not how:
        return "other", "unknown type"
    if ext == ".pdf" and not pdf_lib:
        return "document", "needs pdfminer.six (pip install pdfminer.six)"
    if ext == ".pdf":
        return "document", f"{pdf_lib} is installed"
    return "document", how


def human(size):
    return f"{size / 1024:.0f} KB" if size < 1048576 else f"{size / 1048576:.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--module", help="limit to one module number")
    parser.add_argument("--tar", dest="tar_path", help="course .tar.gz to read")
    args = parser.parse_args()

    if not os.path.exists(COURSE_STRUCTURE_PATH):
        print(f"[FAIL] {COURSE_STRUCTURE_PATH} not found. Run start_run.py first.")
        return 1
    with open(COURSE_STRUCTURE_PATH, encoding="utf-8") as fh:
        structure = json.load(fh)
    describe_source(structure)

    try:
        archive = open_course_archive(args.tar_path)
    except (CourseArchiveError, FileNotFoundError) as exc:
        print(f"[FAIL] {exc}")
        return 1

    modules = group_modules(structure.get("chapters", []))
    if args.module:
        modules = [m for m in modules if (m["number"] or "") == args.module.strip()]
        if not modules:
            print(f"[FAIL] No module {args.module!r}.")
            return 1

    present = archive.listdir("static")
    pdf_lib = pdf_reader_available()

    print(f"\n{len(present)} file(s) in the archive's static/\n")

    referenced, missing = {}, {}
    for module in modules:
        assets = module_assets(module, archive)
        if not assets:
            continue
        print(module_label(module))
        for name, where in sorted(assets.items()):
            if name not in present:
                missing.setdefault(name, []).extend(where)
                print(f"   [MISSING]  {name}  - linked but not in the archive")
                continue
            referenced.setdefault(name, []).extend(where)
            kind, how = classify(name, archive, pdf_lib)
            size = human(archive.size(f"static/{name}"))
            print(f"   [{kind:10}] {name}  ({size})")
            print(f"   {'':12} {how}")
            print(f"   {'':12} linked from: {where[0]}"
                  + (f" (+{len(where) - 1} more)" if len(where) > 1 else ""))
        print()

    unreferenced = [n for n in present
                    if n not in referenced
                    and not n.lower().endswith(TRANSCRIPT_SUFFIXES)]

    print("=" * 68)
    docs = sum(1 for n in referenced if classify(n, archive, pdf_lib)[0] == "document")
    print(f"  linked from a module's HTML   {len(referenced)}  ({docs} document(s))")
    print(f"  in static/ but linked nowhere {len(unreferenced)}")
    print(f"  linked but absent             {len(missing)}")
    if not pdf_lib:
        print("\n  [!] No PDF text library. Any .pdf above cannot be read until:")
        print("      pip install pdfminer.six")

    if unreferenced:
        print("\n  Not linked from any module's HTML, so there is no non-arbitrary")
        print("  module to put them in:")
        for name in unreferenced[:15]:
            print(f"    {name}  ({human(archive.size('static/' + name))})")
        if len(unreferenced) > 15:
            print(f"    ... and {len(unreferenced) - 15} more")

    if not referenced and not unreferenced:
        print("\n  Nothing but transcripts in static/. Including course documents in")
        print("  the module PDFs would have nothing to include, and the unused")
        print("  Global_Assets/ copy step can simply go.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
