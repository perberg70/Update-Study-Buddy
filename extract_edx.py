"""Parse an edX/Open edX course archive into course_structure.json.

Despite the name this no longer unpacks anything. It used to extract into
edx_export/ and read from there, but that directory was shared between runs:
a second archive extracted over it left behind every file the first contained,
so a course could carry its structure from one export and its content from
another. Everything now reads the archive directly, so there is exactly one
input and it is recorded.

Usage:
    python extract_edx.py
    python extract_edx.py --tar course.hp_m6v88.tar.gz
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET

from config import COURSE_STRUCTURE_PATH, resolve_tar_path
from olx_archive import CourseArchive, CourseArchiveError

# An export carries what the course holds, not what a student sees. A unit
# marked staff-only, or one whose release date has not passed, is in the
# archive but not on the page - so it is recorded here and skipped downstream
# rather than silently folded into a study document.
HIDDEN_ATTRS = ("visible_to_staff_only", "visible_to_staff", "hide_from_toc")


def hidden_reason(element):
    """Why this node is not on a student's page, or '' if it is."""
    for attr in HIDDEN_ATTRS:
        value = (element.get(attr) or "").strip().lower()
        if value in ("true", "1", "yes"):
            return f"{attr}={element.get(attr)}"
    return ""


def _parse(archive, relpath):
    """Root element of an OLX file, or None when absent or malformed."""
    text = archive.read_text(relpath)
    if text is None:
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        print(f"[WARN] {relpath} is not valid XML: {exc}")
        return None


def parse_course(archive):
    """Chapter -> sequential -> vertical -> component tree."""
    root = _parse(archive, "course.xml")
    if root is None:
        raise CourseArchiveError("course.xml is missing or unreadable")

    course_url_name = root.get("url_name")
    course_root = _parse(archive, f"course/{course_url_name}.xml")
    if course_root is None:
        raise CourseArchiveError(
            f"course/{course_url_name}.xml not found in the archive")

    chapters = []
    for chapter in course_root.findall("chapter"):
        ch_url_name = chapter.get("url_name")
        chapter_obj = {"title": ch_url_name, "sequentials": []}

        ch_root = _parse(archive, f"chapter/{ch_url_name}.xml")
        if ch_root is None:
            chapters.append(chapter_obj)
            continue
        chapter_obj["title"] = ch_root.get("display_name", ch_url_name)
        chapter_obj["url_name"] = ch_url_name
        if hidden_reason(ch_root):
            chapter_obj["hidden"] = hidden_reason(ch_root)

        for seq in ch_root.findall("sequential"):
            seq_url_name = seq.get("url_name")
            seq_obj = {"title": seq_url_name, "verticals": []}

            seq_root = _parse(archive, f"sequential/{seq_url_name}.xml")
            if seq_root is None:
                chapter_obj["sequentials"].append(seq_obj)
                continue
            seq_obj["title"] = seq_root.get("display_name", seq_url_name)
            seq_obj["url_name"] = seq_url_name
            if hidden_reason(seq_root):
                seq_obj["hidden"] = hidden_reason(seq_root)

            for vert in seq_root.findall("vertical"):
                vert_url_name = vert.get("url_name")
                vert_obj = {"title": vert_url_name, "components": []}

                vert_root = _parse(archive, f"vertical/{vert_url_name}.xml")
                if vert_root is not None:
                    vert_obj["title"] = vert_root.get("display_name", vert_url_name)
                    vert_obj["url_name"] = vert_url_name
                    if hidden_reason(vert_root):
                        vert_obj["hidden"] = hidden_reason(vert_root)
                    vert_obj["components"] = [
                        {"type": component.tag, "url_name": component.get("url_name")}
                        for component in vert_root
                    ]
                seq_obj["verticals"].append(vert_obj)

            chapter_obj["sequentials"].append(seq_obj)
        chapters.append(chapter_obj)

    return {"chapters": chapters}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tar", dest="tar_path", help="Path to the course .tar.gz")
    parser.add_argument("--out", dest="structure_path", default=COURSE_STRUCTURE_PATH,
                        help="Where to write the parsed structure")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        tar_path = resolve_tar_path(args.tar_path)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        return 1

    try:
        archive = CourseArchive(tar_path)
        course_data = parse_course(archive)
    except CourseArchiveError as exc:
        print(f"Error: {exc}")
        return 1

    course_data["_source"] = archive.fingerprint()

    with open(args.structure_path, "w", encoding="utf-8") as fh:
        json.dump(course_data, fh, indent=4, ensure_ascii=False)

    source = course_data["_source"]
    print(f"[OK] {args.structure_path}")
    print(f"     source: {source['tar']}")
    print(f"     {source['size_bytes'] / 1048576:.1f} MB, sha:{source['sha256']}, "
          f"course root: {source['course_root']}")
    print(f"     {len(course_data['chapters'])} chapter(s), "
          f"{source['files_in_archive']} file(s) in the archive")

    hidden = [(ch.get("title"), node.get("title"), node["hidden"])
              for ch in course_data["chapters"]
              for node in [ch] + ch.get("sequentials", [])
              + [v for s in ch.get("sequentials", []) for v in s.get("verticals", [])]
              if node.get("hidden")]
    if hidden:
        print(f"     {len(hidden)} node(s) hidden from students "
              "(skipped unless --include-hidden):")
        for chapter, title, why in hidden[:8]:
            print(f"       {str(title)[:44]:44} {why}")
        if len(hidden) > 8:
            print(f"       ... and {len(hidden) - 8} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
