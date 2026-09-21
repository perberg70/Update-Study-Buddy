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
from asset_text import (STATIC_REF_RE, build_lookup,  # noqa: E402
                        pdf_backend, url_name)

# Transcript sidecars are already handled by the transcript resolver;
# counting them here would bury the documents this is looking for.
TRANSCRIPT_SUFFIXES = (".srt", ".sjson", ".vtt")

TEXT_READY = {".txt": "stdlib", ".md": "stdlib", ".csv": "stdlib",
              ".docx": "stdlib (zip + xml)", ".pdf": "needs pypdf or pdfminer.six",
              ".xlsx": "stdlib (zip + xml), cell text only"}

# Audio uploaded as course material - an AI summary of a section, say. Speech,
# so the existing local Whisper path applies rather than a document reader.
AUDIO_SUFFIXES = (".m4a", ".mp3", ".wav", ".ogg", ".aac", ".flac")

def pdf_reader_available():
    """Name of a PDF reader that actually works, or ''.

    Imports the entry point, not the package: `import pypdf` succeeds even
    where `from pypdf import PdfReader` dies inside cryptography, so a shallow
    check reports a reader that cannot read. This environment does exactly
    that, which is how the bug was found.
    """
    return pdf_backend()[0]


IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
ATTR_RE = re.compile(r"""(\w[\w-]*)\s*=\s*["']([^"']*)["']""")


def image_alts(body):
    """{static name: alt text} for every <img> in a component."""
    alts = {}
    for tag in IMG_RE.findall(body):
        attrs = dict(ATTR_RE.findall(tag))
        src = attrs.get("src", "")
        match = STATIC_REF_RE.search(src)
        if match:
            alts[match.group(1)] = (attrs.get("alt") or "").strip()
    return alts


def prose_words(body):
    """Visible words in a component, reusing the PDF's own HTML reader."""
    try:
        from build_module_pdf import HtmlToBlocks
        parser = HtmlToBlocks()
        parser.feed(body)
        parser.close()
        return sum(len(text.split()) for _kind, text in parser.blocks)
    except Exception:
        return -1


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
                    alts = image_alts(body)
                    words = prose_words(body)
                    for name in STATIC_REF_RE.findall(body):
                        found.setdefault(name, []).append({
                            "where": f"{seq.get('title', '?')} / "
                                     f"{vert.get('title', '?')}",
                            "alt": alts.get(name, ""),
                            "words": words,
                        })
    return found


def classify(name, archive, pdf_lib):
    """(kind, how the text could be extracted) for one static file."""
    ext = os.path.splitext(name)[1].lower()
    if name.lower().endswith(TRANSCRIPT_SUFFIXES):
        return "transcript", "already used by the transcript resolver"
    if ext in AUDIO_SUFFIXES:
        return "audio", "speech - transcribable with the local Whisper path"
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
    lookup, collisions = build_lookup(present)
    pdf_lib = pdf_reader_available()

    print(f"\n{len(present)} file(s) in the archive's static/\n")
    if collisions:
        print("[WARN] Different files share one /static/ spelling, so a link to")
        print("       them is ambiguous:")
        for key, names in list(collisions.items())[:5]:
            print(f"         {key}  <-  {', '.join(names)}")
        print()

    referenced, missing = {}, {}
    for module in modules:
        assets = module_assets(module, archive)
        if not assets:
            continue
        print(module_label(module))
        for name, where in sorted(assets.items()):
            stored = lookup.get(name)
            if stored is None:
                missing.setdefault(name, []).extend(where)
                print(f"   [MISSING]  {name}  - linked but not in the archive")
                continue
            referenced.setdefault(stored, []).extend(where)
            kind, how = classify(stored, archive, pdf_lib)
            size = human(archive.size(f"static/{stored}"))
            shown = stored if stored == name else f"{stored}  (linked as {name})"
            print(f"   [{kind:10}] {shown}  ({size})")
            print(f"   {'':12} {how}")
            first = where[0]
            print(f"   {'':12} linked from: {first['where']}"
                  + (f" (+{len(where) - 1} more)" if len(where) > 1 else ""))
            if kind == "image":
                # Whether OCR is worth it turns on this: an image beside a page
                # of prose is illustration, one on a near-empty page is the
                # lesson.
                alt = first["alt"] or "(no alt text)"
                print(f"   {'':12} alt: {alt[:58]}")
                print(f"   {'':12} prose in that component: {first['words']} word(s)")
        print()

    unreferenced = [n for n in present
                    if n not in referenced
                    and not n.lower().endswith(TRANSCRIPT_SUFFIXES)]

    print("=" * 68)
    kinds = {}
    for n in referenced:
        kinds[classify(n, archive, pdf_lib)[0]] = kinds.get(
            classify(n, archive, pdf_lib)[0], 0) + 1
    detail = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
    print(f"  linked from a module's HTML   {len(referenced)}  ({detail})")
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
