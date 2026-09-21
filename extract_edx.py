import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
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


def archive_fingerprint(tar_path):
    """Enough to identify the archive an extraction came from."""
    digest = hashlib.sha256()
    try:
        with open(tar_path, "rb") as fh:
            digest.update(fh.read(8 * 1024 * 1024))
        size = os.path.getsize(tar_path)
        mtime = dt.datetime.fromtimestamp(os.path.getmtime(tar_path)).isoformat(" ", "seconds")
    except OSError:
        return {}
    return {
        "tar": os.path.abspath(tar_path),
        "size_bytes": size,
        "sha256_head": digest.hexdigest()[:12],
        "tar_modified": mtime,
        "extracted_at": dt.datetime.now().isoformat(" ", "seconds"),
    }


def extract_and_parse(tar_path: str, extract_dir: str, clean: bool = True):
    if not os.path.exists(tar_path):
        print(f"Error: {tar_path} not found. Place the edX course export .tar.gz in this folder.")
        sys.exit(1)

    # Extracting over an existing directory leaves behind any file the new
    # archive does not contain, so the result can blend two course versions -
    # a correct structure pointing at stale content. Clearing is the default.
    if clean and os.path.isdir(extract_dir) and os.listdir(extract_dir):
        print(f"Clearing {extract_dir} (pass --keep to extract over it instead)...")
        shutil.rmtree(extract_dir)
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

    # Record where this came from. Without it there is no way to tell which
    # archive produced a given course_structure.json, or the PDFs built from it.
    course_data["_source"] = archive_fingerprint(tar_path)

    with open(COURSE_STRUCTURE_PATH, "w", encoding="utf-8") as f:
        json.dump(course_data, f, indent=4)
    print(f"Structure saved to {COURSE_STRUCTURE_PATH}")
    source = course_data["_source"]
    if source:
        print(f"   source: {os.path.basename(source['tar'])} "
              f"({source['size_bytes'] / 1048576:.1f} MB, sha:{source['sha256_head']})")
    print(f"   {len(course_data['chapters'])} chapter(s)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract and parse an edX course export.")
    parser.add_argument("--tar", dest="tar_path", help="Path to course .tar.gz export")
    parser.add_argument("--out", dest="extract_dir", default=EXTRACT_DIR, help="Extraction output directory")
    parser.add_argument("--keep", action="store_true",
                        help="extract over the existing directory instead of clearing it")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        tar_path = resolve_tar_path(args.tar_path)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    extract_and_parse(tar_path, args.extract_dir, clean=not args.keep)
