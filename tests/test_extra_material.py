"""Extra files outside the export are appended to the right module's PDF.

Run: python tests/test_extra_material.py
"""

import os
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from build_module_pdf import (extra_material_dir, extra_material_story,  # noqa: E402
                              extra_title, read_extra_file)


class FakePara:
    def __init__(self, text, style):
        self.text, self.style = text, style


def para(text, style, bullet=None):
    return FakePara(text, style)


STYLES = {k: k for k in ("chapter", "unit", "body", "video")}
MODULE = {"number": "1", "title": "Welcome", "chapters": []}


def make_docx(path, lines):
    body = "".join(f"<w:p><w:r><w:t>{ln}</w:t></w:r></w:p>" for ln in lines)
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)


def test_extra_material():
    failures = []
    with tempfile.TemporaryDirectory() as base:
        folder = extra_material_dir(base, MODULE)
        if os.path.basename(folder) != "Module_1":
            failures.append(f"module 1 folder should be Module_1, got {folder}")
        os.makedirs(folder)
        os.makedirs(extra_material_dir(base, {"number": "2", "title": "x", "chapters": []}))

        with open(os.path.join(folder, "Webinar_1_2026.txt"), "w", encoding="utf-8") as fh:
            fh.write("Per: Hej och välkomna.\n\nAnna: Tack, Åsa.\n")
        make_docx(os.path.join(folder, "Webinar_2.docx"), ["First line", "Second line"])
        with open(os.path.join(folder, "empty.txt"), "w") as fh:
            fh.write("   \n")
        with open(os.path.join(folder, "~$lock.docx"), "w") as fh:
            fh.write("junk")
        with open(os.path.join(folder, "ignored.pdf"), "w") as fh:
            fh.write("junk")

        stats = {}
        flow = extra_material_story(MODULE, base, STYLES, stats, para)
        texts = [f.text for f in flow]
        if texts[0] != "Additional material":
            failures.append(f"should open with the heading, got {texts[:1]}")
        for want in ("Webinar 1 2026", "Per: Hej och välkomna.", "Tack, Åsa".join(["Anna: ", "."]),
                     "Webinar 2", "Second line"):
            if want not in texts:
                failures.append(f"missing {want!r} in {texts}")
        if [n for n, _ in stats["extra"]] != ["Webinar_1_2026.txt", "Webinar_2.docx"]:
            failures.append(f"wrong files added: {stats['extra']}")
        if [n for n, _ in stats["extra_unread"]] != ["empty.txt"]:
            failures.append(f"empty file should be reported: {stats['extra_unread']}")

        other = extra_material_story({"number": "3", "title": "t", "chapters": []},
                                     base, STYLES, {}, para)
        if other:
            failures.append("a module with no folder must add nothing")
        if extra_material_story(MODULE, None, STYLES, {}, para):
            failures.append("no base dir must add nothing")

    if extra_title("Webinar_1__2026_Transcript.txt") != "Webinar 1 2026 Transcript":
        failures.append("extra_title should tidy underscores")
    if read_extra_file(os.path.join(ROOT, "no_such_file.txt"))[0]:
        failures.append("a missing file must read as empty, not raise")

    for f in failures:
        print(f"[FAIL] {f}")
    if not failures:
        print("[OK] extra material: added in order, empty/lock/other types skipped")
    return not failures


if __name__ == "__main__":
    raise SystemExit(0 if test_extra_material() else 1)
