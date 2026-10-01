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

from build_module_pdf import (claims_file, extra_material_story,  # noqa: E402
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
        def put(name, text="x"):
            with open(os.path.join(base, name), "w", encoding="utf-8") as fh:
                fh.write(text)

        put("Module_1_Webinar_1.txt", "Per: Hej och välkomna.\n\nAnna: Tack, Åsa.\n")
        put("Module_1.txt", "Overview line")
        make_docx(os.path.join(base, "module_1_Webinar_2.docx"), ["First line", "Second line"])
        put("Module_1_empty.txt", "   \n")
        put("Module_10_Webinar.txt", "belongs to module 10")
        put("Module_1x.txt", "not a separator")
        put("Module_2_Webinar.txt", "belongs to module 2")
        put("abc123_video.txt", "a url_name transcript")
        put("~$Module_1_lock.docx", "junk")
        put("Module_1_notes.pdf", "junk")
        put(".sources.json", "{}")

        stats = {}
        flow = extra_material_story(MODULE, base, STYLES, stats, para)
        texts = [f.text for f in flow]
        if texts[0] != "Additional material":
            failures.append(f"should open with the heading, got {texts[:1]}")
        for want in ("Webinar 1", "Per: Hej och välkomna.", "Anna: Tack, Åsa.",
                     "Webinar 2", "Second line", "Overview line", "Module 1"):
            if want not in texts:
                failures.append(f"missing {want!r} in {texts}")
        added = [n for n, _ in stats["extra"]]
        if added != ["Module_1.txt", "Module_1_Webinar_1.txt", "module_1_Webinar_2.docx"]:
            failures.append(f"wrong files added: {added}")
        if [n for n, _ in stats["extra_unread"]] != ["Module_1_empty.txt"]:
            failures.append(f"empty file should be reported: {stats['extra_unread']}")

        for other in ({"number": "3", "title": "t", "chapters": []},):
            if extra_material_story(other, base, STYLES, {}, para):
                failures.append("a module with no matching file must add nothing")
        if extra_material_story(MODULE, None, STYLES, {}, para):
            failures.append("no folder must add nothing")
        if extra_material_story(MODULE, os.path.join(base, "nope"), STYLES, {}, para):
            failures.append("a missing folder must add nothing")

    unnumbered = {"number": "", "title": "Final seminar - April 1st", "chapters": []}
    if not claims_file(unnumbered, "Module_Final_seminar_April_1st_Recording.txt"):
        failures.append("an unnumbered module should be claimed by its PDF name")
    if extra_title(MODULE, "Module_1_Webinar_1.txt") != "Webinar 1":
        failures.append("extra_title should drop the module prefix")
    if read_extra_file(os.path.join(ROOT, "no_such_file.txt"))[0]:
        failures.append("a missing file must read as empty, not raise")

    for f in failures:
        print(f"[FAIL] {f}")
    if not failures:
        print("[OK] extra material: added in order, empty/lock/other types skipped")
    return not failures


if __name__ == "__main__":
    raise SystemExit(0 if test_extra_material() else 1)
