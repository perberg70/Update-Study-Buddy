"""Reading the course from its archive: provenance, and no blending.

The defect this replaces: extract_edx.py unpacked into edx_export/ without
clearing it, so a second export left every file of the first in place and a
course could carry its structure from one export and its content from another.
Reading from the archive makes that structurally impossible - these tests hold
that line rather than checking a cleanup step.

Run: python tests/test_provenance.py
"""

import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fixtures import make_archive, open_archive  # noqa: E402

import extract_edx  # noqa: E402
from olx_archive import CourseArchive, CourseArchiveError, open_course_archive  # noqa: E402


def course(name, chapters, extra=None):
    """A minimal but complete OLX tree as {relative path: text}."""
    files = {
        "course.xml": '<course url_name="run"/>',
        "course/run.xml": "<course>" + "".join(
            f'<chapter url_name="ch{i}"/>' for i in range(1, chapters + 1)) + "</course>",
    }
    for i in range(1, chapters + 1):
        files[f"chapter/ch{i}.xml"] = (f'<chapter display_name="{i}. Ch {i}">'
                                       f'<sequential url_name="s{i}"/></chapter>')
        files[f"sequential/s{i}.xml"] = (f'<sequential display_name="U{i}">'
                                         f'<vertical url_name="v{i}"/></sequential>')
        files[f"vertical/v{i}.xml"] = (f'<vertical display_name="S{i}">'
                                       f'<html url_name="h{i}"/></vertical>')
        files[f"html/h{i}.html"] = f"<p>{name} chapter {i}</p>"
    files.update(extra or {})
    return files


def main():
    failures = []
    with tempfile.TemporaryDirectory() as base:
        old_files = course("OLD", 3, {"html/orphan.html": "<p>stale</p>"})
        old = make_archive(os.path.join(base, "course.OLD.tar.gz"), old_files)
        new = make_archive(os.path.join(base, "course.NEW.tar.gz"), course("NEW", 2))

        structure_path = os.path.join(base, "structure.json")

        def run(tar):
            sys.argv = ["extract_edx.py", "--tar", tar, "--out", structure_path]
            code = extract_edx.main()
            with io.open(structure_path, encoding="utf-8") as fh:
                return code, json.load(fh)

        code, data = run(old)
        if code != 0 or len(data.get("chapters", [])) != 3:
            failures.append(f"OLD should parse to 3 chapters, got {len(data.get('chapters', []))}")

        # The point of the change: parsing NEW cannot see anything of OLD.
        code, data = run(new)
        if len(data.get("chapters", [])) != 2:
            failures.append(f"NEW should parse to 2 chapters, got {len(data.get('chapters', []))}")

        archive = CourseArchive(new)
        if archive.exists("html/orphan.html"):
            failures.append("a file only OLD contains must not be reachable from NEW")
        if archive.read_text("html/h1.html") != "<p>NEW chapter 1</p>":
            failures.append("content must come from the archive being read")

        source = data.get("_source") or {}
        if os.path.basename(source.get("tar", "")) != "course.NEW.tar.gz":
            failures.append(f"provenance should name the archive, got {source.get('tar')!r}")
        for field in ("sha256_head", "size_bytes", "read_at", "tar_modified",
                      "files_in_archive", "course_root"):
            if not source.get(field):
                failures.append(f"provenance missing {field}")

        if (CourseArchive(old).fingerprint()["sha256_head"]
                == archive.fingerprint()["sha256_head"]):
            failures.append("distinct archives produced the same fingerprint")

        # The recorded archive is what later steps open, not the newest file.
        os.utime(old, None)  # OLD is now newest by mtime - as OneDrive makes it
        cwd = os.getcwd()
        try:
            os.chdir(base)
            chosen = open_course_archive(structure_path=structure_path)
        finally:
            os.chdir(cwd)
        if os.path.basename(chosen.tar_path) != "course.NEW.tar.gz":
            failures.append(f"later steps should reopen the recorded archive, "
                            f"got {os.path.basename(chosen.tar_path)}")

        # Layout independence: root, course/-wrapped, and run-named all agree.
        parsed = []
        for prefix in ("", "course", "HV+GenAI+HT26"):
            arc = open_archive(os.path.join(base, f"layout_{prefix or 'root'}.tar.gz"),
                               course("L", 2), prefix=prefix)
            parsed.append(json.dumps(extract_edx.parse_course(arc), sort_keys=True))
        if len(set(parsed)) != 1:
            failures.append("the three archive layouts parsed differently")

        # An archive with no course.xml must say so, and say what it did find.
        junk = make_archive(os.path.join(base, "junk.tar.gz"), {"notes/readme.txt": "hi"})
        try:
            CourseArchive(junk)
            failures.append("an archive without course.xml should be rejected")
        except CourseArchiveError as exc:
            if "notes" not in str(exc):
                failures.append(f"the rejection should list what was found: {exc}")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] a second export cannot see the first one's files")
        print("  [PASS] source archive recorded, reopened by later steps, "
              "fingerprints differ")
        print("  [PASS] root / course/ / run-named layouts parse identically")
    return not failures


if __name__ == "__main__":
    print("archive provenance")
    raise SystemExit(0 if main() else 1)
