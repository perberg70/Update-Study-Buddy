import argparse
import json
import os
import sys
import tarfile
import xml.etree.ElementTree as ET

from config import COURSE_STRUCTURE_PATH, EXTRACT_DIR, resolve_tar_path


def safe_extract(tar: tarfile.TarFile, path: str) -> None:
    """Extract *tar* into *path*, refusing anything that escapes it."""
    base = os.path.abspath(path)
    for member in tar.getmembers():
        target = os.path.abspath(os.path.join(base, member.name))
        if not target.startswith(base + os.sep) and target != base:
            raise RuntimeError(f"Unsafe archive entry blocked: {member.name}")

    # filter="data" also rejects absolute paths, links pointing outside the
    # destination, and device/FIFO members, which the name check above cannot
    # see. It is the default from Python 3.14; passing it explicitly silences
    # the deprecation warning and gets the protection now.
    try:
        try:
            tar.extractall(path=path, filter="data")
            return
        except tarfile.FilterError as exc:
            raise RuntimeError(f"Unsafe archive entry blocked: {exc}") from None
    except TypeError:
        # Python < 3.12: no filter argument. Reject links by hand instead.
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                link_target = os.path.abspath(
                    os.path.join(base, os.path.dirname(member.name), member.linkname)
                )
                if not link_target.startswith(base + os.sep):
                    raise RuntimeError(f"Unsafe link entry blocked: {member.name}")
            elif not (member.isfile() or member.isdir()):
                raise RuntimeError(f"Unsafe special entry blocked: {member.name}")
        tar.extractall(path=path)


def find_course_root(extract_dir: str) -> str:
    """Return the directory directly containing course.xml.

    Open edX archives come in more than one shape:
    - an import archive built for upload has course.xml at its root;
    - a Studio export wraps everything in a single directory, named after the
      course run, which is not always literally "course".
    """
    direct = os.path.join(extract_dir, "course.xml")
    if os.path.exists(direct):
        return extract_dir

    conventional = os.path.join(extract_dir, "course")
    if os.path.exists(os.path.join(conventional, "course.xml")):
        return conventional

    candidates = [
        os.path.join(extract_dir, entry)
        for entry in sorted(os.listdir(extract_dir))
        if os.path.isdir(os.path.join(extract_dir, entry))
        and os.path.exists(os.path.join(extract_dir, entry, "course.xml"))
    ]
    if len(candidates) == 1:
        return candidates[0]

    found = sorted(os.listdir(extract_dir))[:15] or ["(empty)"]
    raise FileNotFoundError(
        "course.xml not found in the archive.\n"
        f"  Looked in: {extract_dir}, {conventional}, and one level down.\n"
        f"  Top level actually contains: {', '.join(found)}\n"
        "  Expected an Open edX archive with course.xml at its root or inside a "
        "single top-level directory."
    )


def extract_and_parse(tar_path: str, extract_dir: str):
    if not os.path.exists(tar_path):
        print(f"Error: {tar_path} not found. Place the edX course export .tar.gz in this folder.")
        sys.exit(1)
    if not os.path.exists(extract_dir):
        os.makedirs(extract_dir)

    print(f"Extracting {tar_path} to {extract_dir}...")
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            safe_extract(tar, extract_dir)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        print("The archive contains an entry that would write outside the extract "
              "directory. Extraction aborted; nothing was unpacked.")
        sys.exit(1)

    course_data = {"chapters": []}

    try:
        course_root = find_course_root(extract_dir)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if course_root != os.path.join(extract_dir, "course"):
        print(f"[INFO] Course root: {course_root}")

    tree = ET.parse(os.path.join(course_root, "course.xml"))
    course_url_name = tree.getroot().get("url_name")
    course_file = os.path.join(course_root, "course", f"{course_url_name}.xml")

    if not os.path.exists(course_file):
        print(f"Error: Course file {course_file} not found.")
        sys.exit(1)

    course_tree = ET.parse(course_file)
    for chapter in course_tree.getroot().findall("chapter"):
        ch_url_name = chapter.get("url_name")
        ch_file = os.path.join(course_root, "chapter", f"{ch_url_name}.xml")

        chapter_obj = {"title": ch_url_name, "sequentials": []}

        if os.path.exists(ch_file):
            ch_tree = ET.parse(ch_file)
            chapter_obj["title"] = ch_tree.getroot().get("display_name", ch_url_name)

            for seq in ch_tree.getroot().findall("sequential"):
                seq_url_name = seq.get("url_name")
                seq_file = os.path.join(course_root, "sequential", f"{seq_url_name}.xml")

                seq_obj = {"title": seq_url_name, "verticals": []}
                if os.path.exists(seq_file):
                    seq_tree = ET.parse(seq_file)
                    seq_obj["title"] = seq_tree.getroot().get("display_name", seq_url_name)

                    for vert in seq_tree.getroot().findall("vertical"):
                        vert_url_name = vert.get("url_name")
                        vert_file = os.path.join(course_root, "vertical", f"{vert_url_name}.xml")

                        vert_obj = {"components": [], "title": vert.get("url_name", "")}
                        if os.path.exists(vert_file):
                            vert_tree = ET.parse(vert_file)
                            vroot = vert_tree.getroot()
                            vert_obj["title"] = vroot.get("display_name", vert.get("url_name", ""))
                            for component in vroot:
                                comp_type = component.tag
                                comp_url_name = component.get("url_name")
                                vert_obj["components"].append({"type": comp_type, "url_name": comp_url_name})

                        seq_obj["verticals"].append(vert_obj)
                chapter_obj["sequentials"].append(seq_obj)
        course_data["chapters"].append(chapter_obj)

    with open(COURSE_STRUCTURE_PATH, "w", encoding="utf-8") as f:
        json.dump(course_data, f, indent=4)
    print(f"Structure saved to {COURSE_STRUCTURE_PATH}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract and parse an edX course export.")
    parser.add_argument("--tar", dest="tar_path", help="Path to course .tar.gz export")
    parser.add_argument("--out", dest="extract_dir", default=EXTRACT_DIR, help="Extraction output directory")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        tar_path = resolve_tar_path(args.tar_path)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    extract_and_parse(tar_path, args.extract_dir)
