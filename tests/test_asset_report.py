"""Attributing static/ files to modules, so their text can reach the PDFs.

static/ is flat - nothing in it says which module a handbook belongs to. The
only non-arbitrary answer is which module's HTML links to it, so that mapping
is what this covers.

Run: python tests/test_asset_report.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

from _fixtures import open_archive  # noqa: E402

import asset_report as ar  # noqa: E402
import extract_edx  # noqa: E402
from build_module_pdf import group_modules  # noqa: E402

FILES = {
    "course.xml": '<course url_name="r"/>',
    "course/r.xml": '<course><chapter url_name="c1"/><chapter url_name="c2"/></course>',
    "chapter/c1.xml": '<chapter display_name="1. One"><sequential url_name="s1"/>'
                      '<sequential url_name="s3"/></chapter>',
    "chapter/c2.xml": '<chapter display_name="2. Two"><sequential url_name="s2"/></chapter>',
    "sequential/s1.xml": '<sequential display_name="U1"><vertical url_name="v1"/></sequential>',
    "sequential/s2.xml": '<sequential display_name="U2"><vertical url_name="v2"/></sequential>',
    # staff-only: its links must not pull an asset into the PDF
    "sequential/s3.xml": '<sequential display_name="Retired" visible_to_staff_only="true">'
                         '<vertical url_name="v3"/></sequential>',
    "vertical/v1.xml": '<vertical display_name="S1"><html url_name="h1"/></vertical>',
    "vertical/v2.xml": '<vertical display_name="S2"><html url_name="h2"/></vertical>',
    "vertical/v3.xml": '<vertical display_name="S3"><html url_name="h3"/></vertical>',
    "html/h1.html": '<a href="/static/handbook.pdf">h</a>'
                    '<img src="/static/pic.png"/>'
                    '<a href="/static/handbook.pdf?v=2#page3">again, with query</a>'
                    # edX links with underscores what it stores with spaces
                    '<a href="/static/AI_Shifts.png">shifts</a>'
                    '<a href="/static/Section_5_summary.m4a">audio</a>',
    "html/h2.html": "<a href='/static/brief.docx'>b</a>"
                    '<a href="/static/gone.pdf">dead</a>',
    "html/h3.html": '<a href="/static/staff_only.pdf">hidden</a>',
    "static/handbook.pdf": "%PDF",
    "static/brief.docx": "PK",
    "static/pic.png": "PNG",
    "static/AI Shifts.png": "PNG",
    "static/Section 5 summary.m4a": "audio bytes",
    "static/orphan.pdf": "%PDF",
    "static/subs_a.srt.sjson": '{"text":[]}',
}


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


def main():
    failures = []
    with tempfile.TemporaryDirectory() as base:
        archive = open_archive(os.path.join(base, "course.a.tar.gz"), FILES)
        modules = group_modules(extract_edx.parse_course(archive)["chapters"])
        by_number = {m["number"]: m for m in modules}

        one = ar.module_assets(by_number["1"], archive)
        two = ar.module_assets(by_number["2"], archive)

        check(failures, set(one) == {"handbook.pdf", "pic.png",
                                     "AI_Shifts.png", "Section_5_summary.m4a"},
              f"module 1's links, as written in the HTML: {sorted(one)}")
        check(failures, set(two) == {"brief.docx", "gone.pdf"},
              f"module 2 should link the brief and the dead link, got {sorted(two)}")
        check(failures, "handbook.pdf" not in two and "brief.docx" not in one,
              "an asset must not leak into a module that does not link it")

        # ?query and #fragment are not part of the filename.
        check(failures, len(one["handbook.pdf"]) == 2,
              "both links to the same asset should be recorded, query string and all")
        check(failures, not any("?" in n or "#" in n for n in one),
              f"query/fragment must be stripped from the name: {sorted(one)}")

        # A staff-only unit is not on the page, so its links are not the course's.
        check(failures, "staff_only.pdf" not in one,
              "an asset linked only from a hidden unit must not be attributed")

        # Classification drives whether text can be got out at all.
        pdf_lib = ar.pdf_reader_available()
        check(failures, ar.classify("pic.png", archive, pdf_lib)[0] == "image",
              "an image has no text")
        check(failures, ar.classify("brief.docx", archive, pdf_lib)[0] == "document",
              "a docx is a document")
        check(failures, ar.classify("subs_a.srt.sjson", archive, pdf_lib)[0] == "transcript",
              "transcript sidecars are already handled elsewhere")
        kind, how = ar.classify("handbook.pdf", archive, "")
        check(failures, kind == "document" and "pdfminer" in how,
              f"with no pdf library, a pdf must say what is needed: {how}")

        # edX stores "AI Shifts.png" and links /static/AI_Shifts.png. Matching
        # those literally reported every such file as missing AND unreferenced
        # at once - 26 of each on the real course.
        lookup, collisions = ar.build_lookup(archive.listdir("static"))
        check(failures, lookup.get("AI_Shifts.png") == "AI Shifts.png",
              "a space-named file must resolve from its underscore link")
        check(failures, not collisions, f"no collisions expected here: {collisions}")
        check(failures, ar.url_name("a b c.png") == "a_b_c.png",
              "every space becomes an underscore")

        # Course audio is speech, so the Whisper path applies, not a doc reader.
        kind, how = ar.classify("Section 5 summary.m4a", archive, pdf_lib)
        check(failures, kind == "audio" and "Whisper" in how,
              f"an .m4a is transcribable audio, got {kind}/{how}")

        # Two stored names collapsing to one link is ambiguous and must be said.
        _, clash = ar.build_lookup(["a b.png", "a_b.png"])
        check(failures, "a_b.png" in clash,
              "two files sharing one /static/ spelling should be flagged")

        # An orphan is real: it exists but no module has a claim on it.
        present = archive.listdir("static")
        referenced = {lookup[n] for n in (set(one) | set(two)) if n in lookup}
        orphans = [n for n in present if n not in referenced
                   and not n.lower().endswith(ar.TRANSCRIPT_SUFFIXES)]
        check(failures, orphans == ["orphan.pdf"],
              f"exactly one unreferenced document expected, got {orphans}")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] assets attributed to the module whose HTML links them")
        print("  [PASS] query strings stripped, hidden units excluded, orphans found")
        print("  [PASS] type classification says whether text is reachable")
        print("  [PASS] edX's space-to-underscore link spelling resolved; audio")
        print("         recognised as transcribable; ambiguous spellings flagged")
    return not failures


if __name__ == "__main__":
    print("static asset attribution")
    raise SystemExit(0 if main() else 1)
