"""Reading course documents out of static/ and into the module PDF.

The course carries handbooks, briefs and a spreadsheet in static/, linked from
the HTML. Their words are course material. Images are deliberately excluded:
on this course they are generated from the unit text they illustrate, so OCR
would put the same sentences in twice.

Run: python tests/test_asset_text.py
"""

import io
import os
import sys
import tarfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asset_text as at  # noqa: E402
from olx_archive import CourseArchive  # noqa: E402


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


def real_docx(paragraphs):
    buf = io.BytesIO()
    body = "".join(
        "<w:p>" + "".join(f"<w:r><w:t>{run}</w:t></w:r>" for run in runs) + "</w:p>"
        for runs in paragraphs)
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                   f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def real_xlsx(cells):
    buf = io.BytesIO()
    items = "".join(f"<si><t>{c}</t></si>" for c in cells)
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml",
                   '<sst xmlns="http://schemas.openxmlformats.org/'
                   f'spreadsheetml/2006/main">{items}</sst>')
    return buf.getvalue()


def real_pdf(lines):
    """A genuine PDF, so extraction is tested rather than simulated."""
    try:
        from reportlab.platypus import SimpleDocTemplate, Paragraph
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        return b""
    buf, style = io.BytesIO(), getSampleStyleSheet()["Normal"]
    SimpleDocTemplate(buf).build([Paragraph(line, style) for line in lines])
    return buf.getvalue()


def archive_with(members, path):
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return CourseArchive(path)


def built_story(base):
    """Build one module from a fixture course and report what went wrong."""
    try:
        from build_module_pdf import build_styles, group_modules, module_story
        import extract_edx
    except ImportError as exc:
        print(f"  [SKIP] {exc} - the module story is not exercised end to end")
        return []

    files = {
        "course.xml": '<course url_name="HT26"/>',
        "course/HT26.xml": '<course><chapter url_name="c1"/></course>',
        "chapter/c1.xml": '<chapter display_name="1. Welcome">'
                          '<sequential url_name="s1"/></chapter>',
        "sequential/s1.xml": '<sequential display_name="Unit A">'
                             '<vertical url_name="v1"/></sequential>',
        "vertical/v1.xml": '<vertical display_name="Overview">'
                           '<html url_name="a"/><html url_name="b"/></vertical>',
        # The handbook is linked from both components; it belongs in once.
        "html/a.html": '<p>Read the handbook.</p>'
                       '<a href="/static/Integrating_AI.docx">handbook</a>'
                       '<img src="/static/picture.png" alt="A diagram."/>',
        "html/b.html": '<p>As above.</p>'
                       '<a href="/static/Integrating_AI.docx">handbook again</a>',
    }
    path = os.path.join(base, "course.story.tar.gz")
    from _fixtures import make_archive
    make_archive(path, files)

    # The documents go in afterwards: make_archive writes text files, and these
    # are zip containers.
    extra = {
        "static/Integrating AI.docx": real_docx([["Integrating AI into teaching"]]),
        "static/picture.png": b"PNG bytes",
    }
    archive = repack(path, extra)

    styles = build_styles()
    module = group_modules(extract_edx.parse_course(archive)["chapters"])[0]
    stats = {}
    story = module_story(module, archive, styles, stats)
    text = " ".join(getattr(flowable, "text", "") for flowable in story)

    out = []
    check(out, "Integrating AI into teaching" in text,
          "the handbook's text should be in the module story")
    check(out, stats.get("documents") == 1,
          f"the handbook counts once, linked twice: {stats.get('documents')!r}")
    check(out, "picture.png" not in text,
          "an image is not a document - its text is already in the unit")
    check(out, not stats.get("documents_unread"),
          f"nothing should fail to read here: {stats.get('documents_unread')!r}")

    stats_off = {}
    off = module_story(module, archive, styles, stats_off, include_documents=False)
    check(out, "Integrating AI into teaching" not in
          " ".join(getattr(f, "text", "") for f in off),
          "--no-documents must leave the handbook out")
    return out


def repack(path, extra):
    """Add binary members to an existing fixture archive."""
    members = {}
    with tarfile.open(path, "r:gz") as tar:
        for member in tar:
            if member.isfile():
                handle = tar.extractfile(member)
                members[member.name] = handle.read() if handle else b""
    members.update(extra)
    os.remove(path)
    return archive_with(members, path)


def main():
    import tempfile
    failures = []

    # The link spelling, derived from the real course.
    for stored, linked, why in [
        ("AI Shifts.png", "AI_Shifts.png", "spaces"),
        ("Intro Learning with AI (Mod 2).png", "Intro_Learning_with_AI__Mod_2_.png",
         "brackets"),
        ("students' learning.pdf", "students__learning.pdf", "apostrophe"),
        ("HI - Autumn 2026.png", "HI_-_Autumn_2026.png", "hyphen survives"),
    ]:
        check(failures, at.url_name(stored) == linked,
              f"{why}: got {at.url_name(stored)!r}, expected {linked!r}")

    with tempfile.TemporaryDirectory() as base:
        pdf_bytes = real_pdf(["Prompts demo app builder",
                              "Scaffold a timer app, then refine the prompt."])
        members = {
            "course.xml": b'<course url_name="r"/>',
            "course/r.xml": b"<course/>",
            "static/Integrating AI.docx": real_docx(
                [["Integrating AI into teaching"], ["Chapter one: ", "before class."]]),
            "static/Template timer.xlsx": real_xlsx(["Task", "Timer app demo"]),
            "static/notes.txt": "plain  text\n\n\nwith gaps".encode(),
            "static/broken.pdf": b"not a pdf at all",
            "static/empty.pdf": b"",
            "static/picture.png": b"PNG bytes",
        }
        if pdf_bytes:
            members["static/Prompts demo.pdf"] = pdf_bytes
        archive = archive_with(members, os.path.join(base, "c.tar.gz"))

        # docx: runs inside one paragraph join without a space being invented.
        text, how = at.asset_text(archive, "Integrating AI.docx")
        check(failures, "Integrating AI into teaching" in text, f"docx heading: {text!r}")
        check(failures, "Chapter one: before class." in text,
              f"runs in a paragraph should join exactly: {text!r}")

        text, _ = at.asset_text(archive, "Template timer.xlsx")
        check(failures, "Timer app demo" in text, f"xlsx cell text: {text!r}")

        text, _ = at.asset_text(archive, "notes.txt")
        check(failures, text == "plain text\n\nwith gaps",
              f"whitespace should be normalised, got {text!r}")

        # PDFs need a third-party reader, and both "installed" and "absent" are
        # states this runs in - CI installs pypdf, a fresh checkout has neither.
        # So assert the behaviour of whichever state this is, rather than
        # skipping the file type wherever it is not installed.
        backend = at.pdf_backend()[0]
        if backend and pdf_bytes:
            text, how = at.asset_text(archive, "Prompts demo.pdf")
            check(failures, "Scaffold a timer app" in text,
                  f"a real PDF should round-trip, got {text[:80]!r} ({how})")
            broken_reason = "could not read"
        elif backend:
            print("  [SKIP] reportlab absent - no PDF to extract from")
            broken_reason = "could not read"
        else:
            # The reason must name the install, not merely report no text: a
            # missing package looked like an empty document once already.
            text, how = at.asset_text(archive, "broken.pdf")
            check(failures, text == "" and "pip install" in how,
                  f"with no reader, a PDF should say how to get one: {how!r}")
            broken_reason = "no PDF reader installed"

        # Failures carry a reason rather than looking like an empty document.
        for name, expect in [("broken.pdf", broken_reason),
                             ("empty.pdf", "empty"),
                             ("picture.png", "not a document"),
                             ("absent.pdf", "not in the archive")]:
            text, how = at.asset_text(archive, name)
            check(failures, text == "" and expect in how,
                  f"{name}: expected a reason containing {expect!r}, got {how!r}")

        # Only documents are pulled from a component's links, and once each.
        lookup = at.static_lookup(archive)
        body = ('<a href="/static/Integrating_AI.docx">a</a>'
                '<img src="/static/picture.png"/>'
                '<a href="/static/Integrating_AI.docx?v=2">again</a>'
                '<a href="/static/Template_timer.xlsx">x</a>')
        found = at.linked_documents(body, lookup)
        check(failures, found == ["Integrating AI.docx", "Template timer.xlsx"],
              f"documents only, in order, deduplicated: {found}")
        check(failures, "picture.png" not in found,
              "images are excluded - they are generated from the unit text")

        # End to end: a document linked from a unit reaches the module story,
        # an image does not, and an empty stats dict is enough - module_story
        # seeds what it counts rather than trusting its caller, so a counter
        # added later cannot KeyError a build that was going fine.
        story_failures = built_story(base)
        failures.extend(story_failures)

        # A runaway document must not swamp the module it sits beside.
        long_text = at.normalise("word " * (at.MAX_CHARS // 2))
        check(failures, len(long_text) <= at.MAX_CHARS + 60 and "truncated" in long_text,
              "an oversized document should be truncated and say so")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        pdf = f"pdf (via {backend}), " if backend else "pdf absence reported, "
        print(f"  [PASS] {pdf}docx, xlsx and txt read; failures explain themselves")
        print("  [PASS] only linked documents pulled in, once each, images excluded")
        print("  [PASS] edX link spelling resolved; oversized text truncated")
        print("  [PASS] a linked document reaches the module story, once, "
              "and --no-documents leaves it out")
    return not failures


if __name__ == "__main__":
    print("course documents from static/")
    raise SystemExit(0 if main() else 1)
