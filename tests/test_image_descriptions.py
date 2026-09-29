"""Image descriptions never reach a generated document, in English or Swedish.

The course's images are drawn from the unit text beside them, so a description
repeats that text. It must not appear in the module PDFs or the notebook text -
not as alt text, not as a hidden long description, not as a labelled paragraph
or disclosure - and not with --include-hidden either.

The negative cases matter as much: this is a course about generative AI, and
"describe the image" is ordinary course prose there.

Run: python tests/test_image_descriptions.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from course_html import (is_description_label,  # noqa: E402
                         opens_with_description_label, parse_html, visible_text)

IMG = '<img src="/static/chart.png" alt="{}"/>'


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


def seen(html, include_hidden=False):
    parser = parse_html(html, include_hidden=include_hidden)
    return " ".join(text for _kind, text in parser.blocks), parser


# (case, html, text that must be gone, text that must stay)
GONE = [
    ("alt text", IMG.format("A bar chart of adoption by year") + "<p>Adoption grew.</p>",
     "A bar chart of adoption", "Adoption grew."),
    ("alt text, Swedish", IMG.format("Stapeldiagram över användning per år")
     + "<p>Användningen ökade.</p>", "Stapeldiagram", "Användningen ökade."),
    ("sr-only after an image - the course's own pattern",
     '<p>Before.</p>' + IMG.format("Schedule") +
     '<div class="sr-only"><h3>Historical reference</h3><p>Spring 2026.</p></div>'
     '<p>After.</p>', "Spring 2026", "After."),
    ("sr-only after an image, Swedish",
     IMG.format("Schema") + '<span class="visually-hidden">Schemat visar veckorna.</span>'
     '<p>Nästa steg.</p>', "Schemat visar", "Nästa steg."),
    ("hidden inside a figure",
     '<figure><img src="/static/a.png" alt=""/><figcaption>Figure 1. Adoption.</figcaption>'
     '<div class="sr-only">The figure shows three bars.</div></figure><p>Next.</p>',
     "three bars", "Figure 1. Adoption."),
    ("aria-describedby names a visible paragraph",
     '<img src="/static/a.png" alt="" aria-describedby="d1"/>'
     '<p id="d1">The chart rises steeply after 2022.</p><p>Discussion follows.</p>',
     "rises steeply", "Discussion follows."),
    ("description class",
     IMG.format("") + '<div class="image-description">Three people at a table.</div>'
     '<p>Kept.</p>', "Three people", "Kept."),
    ("description class, Swedish",
     IMG.format("") + '<p class="bildbeskrivning">Tre personer vid ett bord.</p>'
     '<p>Kvar.</p>', "Tre personer", "Kvar."),
    ("syntolkning class",
     IMG.format("") + '<div class="syntolkning">En hand håller en telefon.</div>'
     '<p>Kvar.</p>', "håller en telefon", "Kvar."),
    ("disclosure: Image description",
     IMG.format("") + '<details><summary>Image description</summary>'
     '<p>A timeline from 2019 to 2025.</p></details><p>Kept.</p>',
     "timeline from 2019", "Kept."),
    ("disclosure: Visa bildbeskrivning",
     IMG.format("") + '<details><summary>Visa bildbeskrivning</summary>'
     '<p>En tidslinje från 2019.</p></details><p>Kvar.</p>',
     "tidslinje", "Kvar."),
    ("labelled paragraph",
     IMG.format("") + '<p><strong>Image description:</strong> A robot reading.</p>'
     '<p>Kept.</p>', "robot reading", "Kept."),
    ("labelled paragraph, Swedish",
     IMG.format("") + '<p><strong>Bildbeskrivning:</strong> En robot som läser.</p>'
     '<p>Kvar.</p>', "robot som läser", "Kvar."),
    ("labelled paragraph, bilingual",
     IMG.format("") + '<p>Image description / Bildbeskrivning: Två grafer sida vid sida.</p>'
     '<p>Kept.</p>', "Två grafer", "Kept."),
    ("Syntolkning: label",
     IMG.format("") + '<p>Syntolkning: En kvinna vid en dator.</p><p>Kvar.</p>',
     "kvinna vid en dator", "Kvar."),
    ("Beskrivning av bilden: label",
     IMG.format("") + '<p>Beskrivning av bilden – ett flödesschema.</p><p>Kvar.</p>',
     "flödesschema", "Kvar."),
    ("standalone label heading takes its section",
     IMG.format("") + '<h4>Bildbeskrivning</h4><p>Första stycket.</p><p>Andra stycket.</p>'
     '<h3>Nästa avsnitt</h3><p>Behålls.</p>', "Andra stycket", "Behålls."),
    ("standalone label ends with its container",
     '<div>' + IMG.format("") + '<p><b>Image description</b></p><p>Two arrows.</p></div>'
     '<p>Outside the container.</p>', "Two arrows", "Outside the container."),
    ("inline SVG title and desc",
     '<svg role="img"><title>Pie chart</title><desc>Sixty percent use AI daily.</desc>'
     '</svg><p>Kept.</p>', "Sixty percent", "Kept."),
    ("inline clip:rect is sr-only",
     IMG.format("") + '<span style="position:absolute;clip:rect(1px,1px,1px,1px)">'
     'Described here.</span><p>Kept.</p>', "Described here", "Kept."),
]

# Ordinary course prose that mentions images and must survive.
KEPT = [
    ("a prompt exercise, no image in the component",
     '<p>Image description: a cat on a skateboard, watercolour.</p>',
     "a cat on a skateboard"),
    ("describing images as a topic",
     IMG.format("") + '<p>Describe the image you want in one sentence.</p>',
     "Describe the image you want"),
    ("alt text as a subject, mid-sentence",
     IMG.format("") + '<p>Always write alt text for your own images.</p>',
     "Always write alt text"),
    ("a visible caption",
     '<figure><img src="/static/a.png" alt="x"/><figcaption>Source: OECD, 2024.'
     '</figcaption></figure>', "Source: OECD, 2024."),
    ("visible text right after an image",
     IMG.format("x") + '<p>This unit starts with a question.</p>',
     "This unit starts with a question."),
    ("Swedish prose about images",
     IMG.format("") + '<p>Bilder skapade med AI kan vara vilseledande.</p>',
     "Bilder skapade med AI"),
]


def find_text_verdicts():
    """find_text.py, run for real, says where a missing phrase went."""
    import subprocess
    import tempfile
    from _fixtures import make_archive

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = {
        "course.xml": '<course url_name="HT26"/>',
        "course/HT26.xml": '<course><chapter url_name="c1"/></course>',
        "chapter/c1.xml": '<chapter display_name="1. V"><sequential url_name="s"/></chapter>',
        "sequential/s.xml": '<sequential display_name="U"><vertical url_name="v"/></sequential>',
        "vertical/v.xml": '<vertical display_name="S"><html url_name="h"/></vertical>',
        "html/h.html": '<p>Synlig kurstext.</p>'
                       '<img src="/static/a.png" alt="Ett flödesschema med fyra steg"/>'
                       '<div class="sr-only">Schemat börjar med en fråga.</div>',
    }
    out = []
    with tempfile.TemporaryDirectory() as base:
        tar = os.path.join(base, "course.ft.tar.gz")
        make_archive(tar, files)
        for phrase, expect in [
                ("Schemat börjar", "left out as an image description"),
                ("flödesschema med fyra", "an image's alt text"),
                ("Synlig kurstext", "YES")]:
            run = subprocess.run(
                [sys.executable, os.path.join(root, "tools", "find_text.py"),
                 phrase, "--tar", tar],
                capture_output=True, text=True, cwd=base)
            check(out, run.returncode == 0 and expect in run.stdout,
                  f"find_text {phrase!r}: expected {expect!r}, got exit "
                  f"{run.returncode}: {run.stdout[-300:]}{run.stderr[-300:]}")
    return out


def main():
    failures = []

    for case, html, gone, kept in GONE:
        for include_hidden in (False, True):
            text, parser = seen(html, include_hidden)
            flag = " with --include-hidden" if include_hidden else ""
            check(failures, gone not in text,
                  f"{case}{flag}: description leaked: {text!r}")
            check(failures, kept in text,
                  f"{case}{flag}: page text lost: {text!r}")
            # Recorded, so reports and find_text can say where it went. SVG
            # <title>/<desc> are skipped like <script>, with nothing to record.
            check(failures, case.startswith("inline SVG")
                  or gone in " ".join(t for _w, t in parser.removed),
                  f"{case}{flag}: removal not recorded: {parser.removed}")

    for case, html, kept in KEPT:
        text, _parser = seen(html)
        check(failures, kept in text, f"{case}: must be kept, got {text!r}")

    # Hidden text that is not an image description is still restorable.
    html = '<p>Visible.</p><span class="sr-only">Skip to main content</span>'
    check(failures, "Skip to main" not in seen(html)[0],
          "hidden text is left out by default")
    check(failures, "Skip to main" in seen(html, include_hidden=True)[0],
          "--include-hidden still restores hidden text that describes no image")

    # Labels, both ways round.
    for label in ["Image description", "Image description:", "Bildbeskrivning",
                  "Visa bildbeskrivning", "Beskrivning av bilden", "Syntolkning",
                  "Alt text", "Alternativ text", "Image description / Bildbeskrivning",
                  "Bildbeskrivning (English below)", "Show image description"]:
        check(failures, is_description_label(label), f"{label!r} is a label")
    for text in ["Image generation", "Describe the image", "Bilder i kursen",
                 "Image description of the assignment is below and more text"]:
        check(failures, not is_description_label(text), f"{text!r} is not a label")
    check(failures, opens_with_description_label("Bildbeskrivning: ett diagram"),
          "a labelled description opens with its label")
    check(failures, not opens_with_description_label("Bildbeskrivningar skrivs för"),
          "a sentence that starts with the word is not a label")

    # The notebook text uses the same rules.
    stats = {}
    out = visible_text('<p>Prose.</p>' + IMG.format("Alt words") +
                       '<div class="sr-only">Long description.</div>'
                       '<ul><li>One</li></ul>', stats)
    check(failures, out == "Prose.\n- One",
          f"notebook text should be the visible blocks only, got {out!r}")
    check(failures, stats.get("image_descriptions") == 2,
          f"both descriptions counted: {stats}")

    # The notebook sync path, through the function it actually calls.
    import organize_content
    chapter = organize_content.clean_html(
        '<p>Kursens text.</p>' + IMG.format("Diagram över AI-användning") +
        '<div class="sr-only">Diagrammet visar att användningen ökar.</div>'
        '<p><b>Bildbeskrivning:</b> Tre staplar.</p><p>Mer text.</p>')
    check(failures, chapter == "Kursens text.\nMer text.",
          f"notebook chapter text must hold no description, got {chapter!r}")

    failures.extend(find_text_verdicts())

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print(f"  [PASS] {len(GONE)} description patterns removed, English and Swedish,")
        print("         with and without --include-hidden, each removal recorded")
        print(f"  [PASS] {len(KEPT)} kinds of ordinary prose about images kept")
        print("  [PASS] notebook text uses the same rules, through clean_html")
        print("  [PASS] find_text names where a missing phrase went")
    return not failures


if __name__ == "__main__":
    print("image descriptions")
    raise SystemExit(0 if main() else 1)
