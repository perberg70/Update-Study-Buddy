"""Content in the export that the course page does not show.

An export carries what the course holds, not what a student sees. Two ways
text reaches a generated document without ever being on the page:

  * a unit marked visible_to_staff_only, or otherwise hidden in the OLX;
  * text hidden by CSS - the screen-reader pattern around an image.

Both are real, correct course markup. Neither belongs in a study document by
default, and neither may be dropped silently.

Run: python tests/test_hidden_content.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fixtures import open_archive  # noqa: E402

import extract_edx  # noqa: E402
from build_module_pdf import (group_modules, hides_content,  # noqa: E402
                              html_blocks, module_story)

FILES = {
    "course.xml": '<course url_name="HT26"/>',
    "course/HT26.xml": '<course><chapter url_name="c1"/></course>',
    "chapter/c1.xml": '<chapter display_name="1. Welcome">'
                      '<sequential url_name="s1"/><sequential url_name="s2"/></chapter>',
    "sequential/s1.xml": '<sequential display_name="Unit A">'
                         '<vertical url_name="v1"/></sequential>',
    "vertical/v1.xml": '<vertical display_name="Overview"><html url_name="visible"/></vertical>',
    "html/visible.html":
        '<p>On the page.</p>'
        '<img src="/static/s.png" alt="Schedule image."/>'
        '<div class="sr-only"><h3>Historical reference</h3>'
        '<p>Spring 2026, not autumn.</p></div>'
        '<p>Also on the page.</p>',
    "sequential/s2.xml": '<sequential display_name="Retired" visible_to_staff_only="true">'
                         '<vertical url_name="v2"/></sequential>',
    "vertical/v2.xml": '<vertical display_name="Old"><html url_name="staff"/></vertical>',
    "html/staff.html": '<p>Staff-only prose.</p>',
}


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


def texts(blocks):
    return " ".join(text for _kind, text in blocks)


def main():
    failures = []

    # The attribute test itself, including what must NOT count as hidden.
    for attrs, want, why in [
        ([("class", "sr-only")], True, "sr-only"),
        ([("class", "visually-hidden extra")], True, "visually-hidden among others"),
        ([("style", "display:none")], True, "display:none"),
        ([("style", "DISPLAY : NONE")], True, "case and spacing"),
        ([("aria-hidden", "true")], True, "aria-hidden"),
        ([("hidden", "")], True, "hidden attribute"),
        ([("class", "highlight")], False, "an ordinary class"),
        ([("style", "display:block")], False, "display:block"),
        ([("aria-hidden", "false")], False, "aria-hidden=false"),
        ([], False, "no attributes"),
    ]:
        check(failures, hides_content(attrs) == want, f"hides_content: {why}")

    with tempfile.TemporaryDirectory() as base:
        archive = open_archive(os.path.join(base, "course.h.tar.gz"), FILES)

        # 1. CSS-hidden text is skipped, and what surrounds it survives.
        stats = {}
        blocks = texts(html_blocks(archive, "html/visible.html", stats=stats))
        check(failures, "On the page." in blocks and "Also on the page." in blocks,
              "visible prose either side of a hidden block must survive")
        check(failures, "Historical reference" not in blocks,
              "a hidden heading must not reach the document")
        check(failures, "Spring 2026, not autumn." not in blocks,
              "hidden body text must not reach the document")
        check(failures, "[Image: Schedule image.]" in blocks,
              "alt text is the only trace of an image in a text document")
        check(failures, stats.get("hidden_chars", 0) > 0,
              "skipped text must be counted, not dropped silently")

        # 2. --include-hidden restores it.
        kept = texts(html_blocks(archive, "html/visible.html", include_hidden=True))
        check(failures, "Spring 2026, not autumn." in kept,
              "--include-hidden must restore hidden text")

        # 3. A staff-only unit is recorded by the parser and skipped by the build.
        structure = extract_edx.parse_course(archive)
        seqs = {s["title"]: s for s in structure["chapters"][0]["sequentials"]}
        check(failures, seqs["Retired"].get("hidden") == "visible_to_staff_only=true",
              f"staff-only unit should be flagged, got {seqs['Retired'].get('hidden')!r}")
        check(failures, not seqs["Unit A"].get("hidden"),
              "a visible unit must not be flagged hidden")

        module = group_modules(structure["chapters"])[0]
        styles = {k: None for k in
                  ("title", "chapter", "unit", "subunit", "inner", "item", "body", "video")}
        stats = {"units": 0, "subunits": 0, "html": 0, "videos": 0, "transcripts": 0,
                 "from_olx": 0, "from_store": 0, "video_missing": 0, "skipped": {},
                 "hidden_nodes": [], "hidden_text_components": 0, "hidden_chars": 0}

        # Paragraph construction needs reportlab; the skipping decision does not.
        try:
            from build_module_pdf import build_styles, register_unicode_font
            styles = build_styles(register_unicode_font())
        except ImportError:
            print("  [SKIP] reportlab absent - unit skipping not exercised end to end")
        else:
            module_story(module, archive, styles, stats)
            check(failures, stats["units"] == 1,
                  f"only the visible unit should be built, got {stats['units']}")
            check(failures, [k for k, _, _ in stats["hidden_nodes"]] == ["unit"],
                  f"the skipped unit should be reported, got {stats['hidden_nodes']}")

            stats["hidden_nodes"] = []
            stats["units"] = 0
            module_story(module, archive, styles, stats, include_hidden=True)
            check(failures, stats["units"] == 2 and not stats["hidden_nodes"],
                  "--include-hidden must build the staff-only unit too")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] sr-only / display:none / aria-hidden skipped, counted, restorable")
        print("  [PASS] staff-only units flagged on parse and skipped on build")
    return not failures


if __name__ == "__main__":
    print("content hidden from students")
    raise SystemExit(0 if main() else 1)
