"""Extraction provenance and stale-file cleanup.

Run: python tests/test_provenance.py
"""

import io
import json
import os
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import extract_edx  # noqa: E402


def build_archive(base, name, chapters, orphans=()):
    src = os.path.join(base, f"src_{name}")
    for sub in ("course", "chapter", "sequential", "vertical", "html"):
        os.makedirs(os.path.join(src, sub), exist_ok=True)
    w = lambda p, t: io.open(os.path.join(src, p), "w", encoding="utf-8").write(t)
    w("course.xml", '<course url_name="run"/>')
    w("course/run.xml", "<course>" + "".join(
        f'<chapter url_name="ch{i}"/>' for i in range(1, chapters + 1)) + "</course>")
    for i in range(1, chapters + 1):
        w(f"chapter/ch{i}.xml", f'<chapter display_name="{i}. Ch {i}">'
          f'<sequential url_name="s{i}"/></chapter>')
        w(f"sequential/s{i}.xml", f'<sequential display_name="U{i}">'
          f'<vertical url_name="v{i}"/></sequential>')
        w(f"vertical/v{i}.xml", f'<vertical display_name="S{i}"><html url_name="h{i}"/></vertical>')
        w(f"html/h{i}.html", f"<p>{name} chapter {i}</p>")
    for orphan in orphans:
        w(f"html/{orphan}.html", f"<p>stale {orphan}</p>")

    path = os.path.join(base, f"course.{name}.tar.gz")
    with tarfile.open(path, "w:gz") as tar:
        for dirpath, _dirs, files in os.walk(src):
            for f in files:
                full = os.path.join(dirpath, f)
                tar.add(full, arcname=os.path.relpath(full, src))
    return path


def main():
    failures = []
    with tempfile.TemporaryDirectory() as base:
        old = build_archive(base, "OLD", 3, orphans=["orphan1", "orphan2"])
        new = build_archive(base, "NEW", 2)
        out = os.path.join(base, "edx_export")
        structure = os.path.join(base, "structure.json")
        extract_edx.COURSE_STRUCTURE_PATH = structure

        extract_edx.extract_and_parse(old, out)
        html_dir = os.path.join(out, "html")
        if not os.path.exists(os.path.join(html_dir, "orphan1.html")):
            failures.append("setup: OLD should have produced orphan files")

        # --keep reproduces the original defect.
        extract_edx.extract_and_parse(new, out, clean=False)
        if not os.path.exists(os.path.join(html_dir, "orphan1.html")):
            failures.append("--keep should leave stale files (that is its purpose)")

        # The default must clear them.
        extract_edx.extract_and_parse(new, out)
        left = sorted(os.listdir(html_dir))
        if left != ["h1.html", "h2.html"]:
            failures.append(f"clean extract should leave only NEW's files, got {left}")

        with io.open(structure, encoding="utf-8") as fh:
            data = json.load(fh)
        source = data.get("_source") or {}
        if os.path.basename(source.get("tar", "")) != "course.NEW.tar.gz":
            failures.append(f"provenance should name the archive, got {source.get('tar')!r}")
        for field in ("sha256_head", "size_bytes", "extracted_at", "tar_modified"):
            if not source.get(field):
                failures.append(f"provenance missing {field}")
        if len(data.get("chapters", [])) != 2:
            failures.append("structure should hold NEW's 2 chapters")

        # Two different archives must fingerprint differently.
        old_fp = extract_edx.archive_fingerprint(old)
        new_fp = extract_edx.archive_fingerprint(new)
        if old_fp["sha256_head"] == new_fp["sha256_head"]:
            failures.append("distinct archives produced the same fingerprint")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] stale files cleared by default, kept with --keep")
        print("  [PASS] source archive recorded and fingerprints differ")
    return not failures


if __name__ == "__main__":
    print("extraction provenance")
    raise SystemExit(0 if main() else 1)
