#!/usr/bin/env python3
"""Find where a phrase in a generated document came from, and why the course
page might not show it.

Text can be in the OLX and still be absent from the rendered page, in two quite
different ways:

  * the component is there but hidden - `visible_to_staff_only`, an unreleased
    `start` date, or an unpublished draft that the export still carries;
  * the component is visible but the text inside it is hidden by CSS, which is
    how a long description for screen readers is normally attached to an image.

The fix differs per case, so this reports both: where the component sits in the
course, every visibility attribute on its chapter/unit/subunit chain, and the
raw HTML around the match with its classes and styles intact.

Read-only.

Usage:
    python tools/find_text.py "Schedule: historical reference"
    python tools/find_text.py "spring 2026" --context 600
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import COURSE_STRUCTURE_PATH  # noqa: E402
from olx_archive import (CourseArchiveError, describe_source,  # noqa: E402
                         open_course_archive)

# Attributes that keep a component out of a student's view.
VISIBILITY_ATTRS = ("visible_to_staff_only", "hide_from_toc", "start",
                    "visible_to_staff", "is_practice_exam", "entrance_exam_id")

# Class and style patterns that hide text visually while leaving it for screen
# readers. Bootstrap, edX's own theme and hand-rolled markup all appear here.
HIDDEN_CLASS_RE = re.compile(
    r"\b(sr-only|sr_only|screen-?reader(-only|-text)?|visually-?hidden|"
    r"hidden|hide|a11y-?only|accessible-?text|invisible)\b", re.I)
HIDDEN_STYLE_RE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|"
    r"(?:clip|position)\s*:\s*(?:rect\([^)]*\)|absolute)[^;]*|"
    r"(?:left|top|text-indent)\s*:\s*-\d{4,}", re.I)

TAG_RE = re.compile(r"<[^>]+>")


def normalise(text):
    """Collapse whitespace so a PDF's reflowed text matches the source HTML."""
    return " ".join(text.split())


def visible_text(html):
    """Text as the parser sees it, with an index back into the raw HTML."""
    out, spans, pos = [], [], 0
    for match in TAG_RE.finditer(html):
        if match.start() > pos:
            out.append(html[pos:match.start()])
            spans.append((len("".join(out)) - (match.start() - pos), pos))
        pos = match.end()
    if pos < len(html):
        out.append(html[pos:])
        spans.append((len("".join(out)) - (len(html) - pos), pos))
    return "".join(out), spans


def raw_offset(spans, text_index):
    """Map an offset in the stripped text back to an offset in the raw HTML."""
    best = 0
    for text_start, raw_start in spans:
        if text_start <= text_index:
            best = raw_start + (text_index - text_start)
        else:
            break
    return best


def enclosing_tags(html, offset):
    """Open tags still unclosed at *offset*, outermost first."""
    stack = []
    for match in TAG_RE.finditer(html):
        if match.start() >= offset:
            break
        tag = match.group(0)
        if tag.startswith("</"):
            name = tag[2:].rstrip(">").strip().lower()
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == name:
                    del stack[i:]
                    break
        elif not tag.endswith("/>"):
            name = re.match(r"<\s*([A-Za-z0-9]+)", tag)
            if name and name.group(1).lower() not in (
                    "br", "img", "hr", "input", "meta", "link"):
                stack.append((name.group(1).lower(), tag))
    return stack


def hidden_reason(tag_text):
    """Why this tag would hide its contents visually, or ''."""
    classes = re.search(r'class\s*=\s*["\']([^"\']*)["\']', tag_text, re.I)
    if classes and HIDDEN_CLASS_RE.search(classes.group(1)):
        return f'class="{classes.group(1).strip()}"'
    style = re.search(r'style\s*=\s*["\']([^"\']*)["\']', tag_text, re.I)
    if style and HIDDEN_STYLE_RE.search(style.group(1)):
        return f'style="{style.group(1).strip()}"'
    if re.search(r'\shidden(\s|=|>)', tag_text, re.I):
        return "hidden attribute"
    if re.search(r'aria-hidden\s*=\s*["\']true', tag_text, re.I):
        return "aria-hidden=true"
    return ""


def walk_components(archive):
    """{component url_name: [(kind, url_name, element), ...] ancestor chain}.

    The OLX is walked directly rather than through course_structure.json,
    because the chain has to be keyed by url_name: two units can share a
    display name, and a match on titles would then report the wrong one.
    """
    def parse(relpath):
        xml = archive.read_text(relpath)
        if xml is None:
            return None
        try:
            return ET.fromstring(xml)
        except ET.ParseError:
            return None

    index = {}
    root = parse("course.xml")
    if root is None:
        return index
    course = parse(f"course/{root.get('url_name')}.xml")
    if course is None:
        return index

    for chapter_ref in course.findall("chapter"):
        ch_name = chapter_ref.get("url_name")
        chapter = parse(f"chapter/{ch_name}.xml")
        if chapter is None:
            continue
        for seq_ref in chapter.findall("sequential"):
            seq_name = seq_ref.get("url_name")
            seq = parse(f"sequential/{seq_name}.xml")
            if seq is None:
                continue
            for vert_ref in seq.findall("vertical"):
                vert_name = vert_ref.get("url_name")
                vert = parse(f"vertical/{vert_name}.xml")
                if vert is None:
                    continue
                chain = [("chapter", ch_name, chapter),
                         ("sequential", seq_name, seq),
                         ("vertical", vert_name, vert)]
                for comp in vert:
                    if comp.get("url_name"):
                        index[comp.get("url_name")] = chain
    return index


def visibility_flags(chain):
    """Attributes on the ancestor chain that keep a component off the page."""
    flags = []
    for kind, url_name, element in chain:
        title = element.get("display_name", url_name)
        for attr in VISIBILITY_ATTRS:
            if element.get(attr):
                flags.append(f"{kind} {title!r}: {attr}={element.get(attr)!r}")
    return flags


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phrase", help="text to find (whitespace is ignored)")
    parser.add_argument("--context", type=int, default=400,
                        help="characters of raw HTML to show around the match")
    parser.add_argument("--tar", dest="tar_path",
                        help="course .tar.gz to search (default: the one "
                             "course_structure.json was built from)")
    args = parser.parse_args()

    try:
        archive = open_course_archive(args.tar_path)
    except (CourseArchiveError, FileNotFoundError) as exc:
        print(f"[FAIL] {exc}")
        return 1

    structure = {}
    if os.path.exists(COURSE_STRUCTURE_PATH):
        with open(COURSE_STRUCTURE_PATH, encoding="utf-8") as fh:
            structure = json.load(fh)
        describe_source(structure)

    needle = normalise(args.phrase).lower()
    if not needle:
        print("[FAIL] Nothing to search for.")
        return 1

    components = walk_components(archive)

    hits = 0
    for name in archive.listdir("html"):
        if not name.lower().endswith(".html"):
            continue
        html = archive.read_text(f"html/{name}")
        if html is None:
            continue

        text, spans = visible_text(html)
        flat = normalise(text).lower()
        if needle not in flat:
            continue
        hits += 1

        url_name = name[:-5]
        chain = components.get(url_name)
        print("\n" + "=" * 70)
        print(f"  html/{name}")
        print("=" * 70)
        if chain:
            for kind, label in zip(("chapter:", "unit:   ", "subunit:"), chain):
                print(f"  {kind}  {label[2].get('display_name', label[1])}")
        else:
            print("  [!] This component is NOT referenced by any vertical in the")
            print("      course. It is an orphan the export still carries, which is")
            print("      one way text reaches the PDF but not the page.")

        if chain:
            flags = visibility_flags(chain)
            if flags:
                print("\n  Hidden in the OLX:")
                for flag in flags:
                    print(f"    {flag}")
                print("  The export carries this, but students do not see it.")
            else:
                print("\n  OLX visibility: nothing marks this chapter/unit/subunit hidden.")

        # Where the phrase sits in the raw markup, and what encloses it.
        index = flat.find(needle)
        approx = 0
        seen = 0
        for i, ch in enumerate(text):
            if not ch.isspace() or (seen and not text[i - 1].isspace()):
                if seen == index:
                    approx = i
                    break
                seen += 1
        offset = raw_offset(spans, approx)

        stack = enclosing_tags(html, offset)
        reasons = [(tag, hidden_reason(raw)) for tag, raw in stack if hidden_reason(raw)]
        if reasons:
            print("\n  Hidden by CSS - enclosed in:")
            for tag, why in reasons:
                print(f"    <{tag}> {why}")
            print("  This is the screen-reader pattern: real content, deliberately")
            print("  not shown to sighted users. The PDF keeps it because the parser")
            print("  reads text, not stylesheets.")
        else:
            print("\n  CSS: nothing enclosing the match hides it.")
            print(f"  Enclosing tags: {' > '.join(t for t, _ in stack) or '(none)'}")

        start = max(0, offset - args.context // 2)
        print("\n  Raw HTML around the match:")
        print("  " + "-" * 66)
        for line in html[start:offset + args.context].splitlines():
            print(f"  | {line}")
        print("  " + "-" * 66)

    if not hits:
        print(f"\n[FAIL] {args.phrase!r} is in no html component of this archive.")
        print("       If it is in the PDF, the PDF was built from a different export.")
        return 1

    print(f"\n[OK] {hits} component(s) contain that text.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
