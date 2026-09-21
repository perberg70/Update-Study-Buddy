#!/usr/bin/env python3
"""Build one structured PDF per course module.

A module is a group of chapters sharing a leading number: "3.", "3.A", "3.B"
and "3.C" are all module 3. A chapter with no leading number is its own module.

The PDF carries the course hierarchy as real heading levels - module, chapter
(only when a module spans several), unit, subunit - followed by that subunit's
prose and video text, so the document is navigable rather than one flat dump.

Reads the extracted course only. Uploads nothing and touches no notebook.

Usage:
    python tools/build_module_pdf.py --module 1
    python tools/build_module_pdf.py --list
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from xml.sax.saxutils import escape as xml_escape

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (COURSE_STRUCTURE_PATH,  # noqa: E402
                    ORGANIZED_CONTENT_DIR, TRANSCRIPTS_DIR)
from olx_archive import (CourseArchiveError, describe_source,  # noqa: E402
                         open_course_archive)

MODULE_NUMBER_RE = re.compile(r"^\s*(\d+)")
NO_TRANSCRIPT = "[no transcript in export]"

# Text hidden from sighted users but left for screen readers. It is real
# content and correct markup, but it is not what the course page shows - and a
# long image description read as body prose is actively confusing in a study
# document. Skipped by default, restored with --include-hidden.
HIDDEN_CLASS_RE = re.compile(
    r"\b(sr-only|sr_only|screen-?reader(-only|-text)?|visually-?hidden|"
    r"hidden|hide|a11y-?only|accessible-?text|invisible)\b", re.I)
HIDDEN_STYLE_RE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|"
    r"(?:left|top|text-indent)\s*:\s*-\d{4,}", re.I)


def hides_content(attrs):
    """True when an element's own attributes keep it off the rendered page."""
    values = dict(attrs)
    if HIDDEN_CLASS_RE.search(values.get("class") or ""):
        return True
    if HIDDEN_STYLE_RE.search(values.get("style") or ""):
        return True
    if "hidden" in values:
        return True
    return (values.get("aria-hidden") or "").strip().lower() == "true"


# --------------------------------------------------------------------------
# Module grouping
# --------------------------------------------------------------------------

def module_number(chapter_title):
    """Leading number of a chapter title, or None: '3.A Track' -> '3'."""
    match = MODULE_NUMBER_RE.match(chapter_title or "")
    return match.group(1) if match else None


def group_modules(chapters):
    """Group chapters into modules, preserving course order."""
    modules, by_number = [], {}
    for chapter in chapters:
        number = module_number(chapter.get("title", ""))
        if number is None:
            modules.append({"number": None,
                            "title": chapter.get("title", "(untitled)"),
                            "chapters": [chapter]})
            continue
        if number not in by_number:
            by_number[number] = {"number": number, "title": None, "chapters": []}
            modules.append(by_number[number])
        by_number[number]["chapters"].append(chapter)

    for module in modules:
        if module["title"] is None:
            first = module["chapters"][0].get("title", "")
            module["title"] = MODULE_NUMBER_RE.sub("", first).lstrip(". ").strip()
    return modules


def module_label(module):
    return (f"Module {module['number']}: {module['title']}"
            if module["number"] else module["title"])


def module_filename(module):
    if module["number"]:
        return f"Module_{module['number']}.pdf"
    slug = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]", "_", module["title"])).strip("_")
    return f"Module_{slug}.pdf"


# --------------------------------------------------------------------------
# HTML -> blocks
# --------------------------------------------------------------------------

class HtmlToBlocks(HTMLParser):
    """Turn course HTML into ('head'|'item'|'para', text) blocks.

    Unlike a tag-stripping regex this drops script/style *content*, keeps
    paragraph and list boundaries, and (via convert_charrefs) resolves entities
    so no literal &amp; or &ouml; reaches the document.
    """

    SKIP = {"script", "style", "head", "title"}
    VOID = {"br", "img", "hr", "input", "meta", "link", "area", "base",
            "col", "embed", "param", "source", "track", "wbr"}
    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BREAKS = {"p", "div", "section", "article", "br", "tr", "ul", "ol", "table", "blockquote"}

    def __init__(self, include_hidden=False):
        super().__init__(convert_charrefs=True)
        self.blocks, self._buf, self._skip, self._kind, self._href = [], [], 0, "para", ""
        self.include_hidden = include_hidden
        # Depth counting, not a flag: nested elements inside a hidden one must
        # not un-hide it when the inner element closes.
        self._hidden_depth = 0
        self._open = []
        self.hidden_chars = 0

    def _flush(self):
        text = " ".join("".join(self._buf).split())
        if text:
            self.blocks.append((self._kind, text))
        self._buf, self._kind = [], "para"

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
            return
        if self._skip:
            return

        if not self.include_hidden and tag not in self.VOID:
            hiding = hides_content(attrs)
            self._open.append((tag, hiding))
            if hiding:
                if not self._hidden_depth:
                    self._flush()   # keep hidden text out of the block being built
                self._hidden_depth += 1

        if self._hidden_depth:
            return

        if tag in self.HEADINGS:
            self._flush()
            self._kind = "head"
        elif tag == "li":
            self._flush()
            self._kind = "item"
        elif tag in self.BREAKS:
            self._flush()
        elif tag == "img":
            alt = dict(attrs).get("alt", "").strip()
            if alt:
                self._buf.append(f" [Image: {alt}] ")
        elif tag == "a":
            self._href = dict(attrs).get("href", "") or ""

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return

        if not self.include_hidden and tag not in self.VOID:
            for i in range(len(self._open) - 1, -1, -1):
                if self._open[i][0] == tag:
                    for _, hiding in self._open[i:]:
                        if hiding:
                            self._hidden_depth = max(0, self._hidden_depth - 1)
                    del self._open[i:]
                    break

        if self._hidden_depth:
            return

        if tag == "a":
            if self._href.startswith(("http://", "https://")):
                self._buf.append(f" <{self._href}> ")
            self._href = ""
        elif tag in self.HEADINGS or tag == "li" or tag in self.BREAKS:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        if self._hidden_depth:
            self.hidden_chars += len(data.strip())
            return
        self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def html_blocks(archive, relpath, include_hidden=False, stats=None):
    try:
        parser = HtmlToBlocks(include_hidden=include_hidden)
        parser.feed(archive.read_text(relpath) or "")
        parser.close()
        if stats is not None and parser.hidden_chars:
            stats["hidden_text_components"] = stats.get("hidden_text_components", 0) + 1
            stats["hidden_chars"] = stats.get("hidden_chars", 0) + parser.hidden_chars
        return parser.blocks
    except Exception as exc:
        return [("para", f"[could not read {os.path.basename(relpath)}: {exc}]")]


# --------------------------------------------------------------------------
# Video transcripts
# --------------------------------------------------------------------------

def transcript_candidates(video_root, archive):
    """Transcript members an OLX <video> points at, that the archive actually holds.

    Returned as archive-relative paths ("static/subs_abc.srt.sjson").
    """
    paths = []

    for node in video_root.findall(".//transcript"):
        src = (node.get("src") or "").strip()
        if src:
            paths.append("static/" + os.path.basename(src))

    raw = video_root.get("transcripts")
    if raw:
        try:
            for value in json.loads(html.unescape(raw)).values():
                if value:
                    paths.append("static/" + os.path.basename(str(value)))
        except Exception:
            pass

    sub = (video_root.get("sub") or "").strip()
    if sub:
        paths.append(f"static/subs_{sub}.srt.sjson")
        paths.append(f"static/{sub}.srt.sjson")
        paths.append(f"static/{sub}.srt")

    seen, existing = set(), []
    for path in paths:
        if path not in seen and archive.exists(path):
            seen.add(path)
            existing.append(path)
    return existing


# Teams exports WebVTT with speaker tags: <v Per Berg>...</v>. Keeping the
# speaker makes a webinar transcript readable as dialogue rather than one wall
# of text, so the tag is unwrapped rather than stripped.
VTT_SPEAKER_RE = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>|$)", re.I | re.S)
VTT_TAG_RE = re.compile(r"<[^>]+>")
TIMECODE_RE = re.compile(r"-->")


def read_transcript(path):
    """Plain text of a transcript file on disk (the transcript store)."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as fh:
            raw = fh.read()
    except Exception:
        return ""
    return transcript_text(raw, path)


def archive_transcript(archive, relpath):
    """Plain text of a transcript member inside the course archive."""
    data = archive.read_bytes(relpath)
    if data is None:
        return ""
    return transcript_text(data.decode("utf-8-sig", "ignore"), relpath)


def transcript_text(raw, name=""):
    """Plain text of .sjson, .srt, .vtt or .txt transcript content.

    *name* is used only to tell a plain-text transcript from a cue-based one.
    """
    if not raw:
        return ""

    if raw.lstrip().startswith("{"):
        try:
            data = json.loads(raw)
            return " ".join(s.strip() for s in data.get("text", []) if s and s.strip())
        except Exception:
            pass

    if name.lower().endswith(".txt"):
        return " ".join(raw.split())

    out, speaker = [], None
    for line in raw.splitlines():
        line = line.strip()
        if (not line or line.isdigit() or TIMECODE_RE.search(line)
                or line.upper().startswith(("WEBVTT", "NOTE ", "STYLE"))):
            continue
        match = VTT_SPEAKER_RE.search(line)
        if match:
            name, said = match.group(1).strip(), VTT_TAG_RE.sub("", match.group(2)).strip()
            if not said:
                continue
            out.append(f"{name}: {said}" if name != speaker else said)
            speaker = name
        else:
            out.append(VTT_TAG_RE.sub("", line))
    return " ".join(part for part in out if part)


def stored_transcript(url_name, title, transcripts_dir):
    """A transcript dropped into the store, by url_name or by slugified title.

    url_name is the stable key; the title form exists so a file exported from
    Teams can be renamed to something recognisable by hand.
    """
    if not transcripts_dir or not os.path.isdir(transcripts_dir):
        return ""
    keys = [url_name]
    if title:
        keys.append(re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]", "_", title)).strip("_"))
    for key in keys:
        if not key:
            continue
        for ext in (".txt", ".vtt", ".srt", ".sjson", ".json"):
            candidate = os.path.join(transcripts_dir, f"{key}{ext}")
            if os.path.exists(candidate):
                text = read_transcript(candidate)
                if text:
                    return text
    return ""


def video_entry(url_name, vertical_title, archive, stats, transcripts_dir=None):
    """(title, transcript text) for one video component."""
    xml = archive.read_text(f"video/{url_name}.xml")
    title = vertical_title or url_name
    if xml is None:
        stats["video_missing"] += 1
        return title, NO_TRANSCRIPT
    try:
        root = ET.fromstring(xml)
    except Exception:
        stats["video_missing"] += 1
        return title, NO_TRANSCRIPT

    title = html.unescape(root.get("display_name") or vertical_title or url_name)
    stats["videos"] += 1

    for candidate in transcript_candidates(root, archive):
        text = archive_transcript(archive, candidate)
        if text:
            stats["transcripts"] += 1
            stats["from_olx"] += 1
            return title, text

    text = stored_transcript(url_name, title, transcripts_dir)
    if text:
        stats["transcripts"] += 1
        stats["from_store"] += 1
        return title, text

    return title, NO_TRANSCRIPT


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

# Course HTML routinely contains em-dashes, curly quotes and ellipses, none of
# which exist in Latin-1. reportlab's built-in Helvetica silently drops them, so
# a Unicode TrueType font is registered instead. Vera ships inside reportlab
# itself, so this works wherever reportlab installs; nicer system fonts are
# preferred when present.
FONT_CANDIDATES = [
    ("DejaVuSans", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("Arial", "arial.ttf", "arialbd.ttf"),
    ("Vera", "Vera.ttf", "VeraBd.ttf"),
]

# Applied only if no Unicode font can be registered, so text degrades to a
# readable approximation rather than vanishing.
TRANSLITERATE = {
    "\u2014": "--", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ",
    "\u2022": "*", "\u2192": "->", "\u00ad": "",
}


def _font_dirs():
    import reportlab
    dirs = [os.path.join(os.path.dirname(reportlab.__file__), "fonts"),
            "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype",
            "/Library/Fonts", os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")]
    return [d for d in dirs if os.path.isdir(d)]


def register_unicode_font():
    """Register a Unicode font family. Returns (regular, bold) or None."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    dirs = _font_dirs()
    for family, regular_file, bold_file in FONT_CANDIDATES:
        regular = next((os.path.join(d, regular_file) for d in dirs
                        if os.path.exists(os.path.join(d, regular_file))), None)
        if not regular:
            continue
        bold = next((os.path.join(d, bold_file) for d in dirs
                     if os.path.exists(os.path.join(d, bold_file))), None) or regular
        try:
            pdfmetrics.registerFont(TTFont(family, regular))
            bold_name = f"{family}-Bold"
            pdfmetrics.registerFont(TTFont(bold_name, bold))
            pdfmetrics.registerFontFamily(family, normal=family, bold=bold_name)
            return family, bold_name
        except Exception:
            continue
    return None


def safe_text(text, unicode_ok):
    if unicode_ok:
        return text
    for bad, good in TRANSLITERATE.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


def build_styles(fonts=None):
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("ModuleTitle", parent=base["Title"], fontSize=22,
                                spaceAfter=14 * mm),
        "chapter": ParagraphStyle("Chapter", parent=base["Heading1"], fontSize=17,
                                  spaceBefore=10 * mm, spaceAfter=4 * mm),
        "unit": ParagraphStyle("Unit", parent=base["Heading2"], fontSize=14,
                               spaceBefore=7 * mm, spaceAfter=3 * mm),
        "subunit": ParagraphStyle("Subunit", parent=base["Heading3"], fontSize=12,
                                  spaceBefore=5 * mm, spaceAfter=2 * mm),
        "inner": ParagraphStyle("InnerHead", parent=base["Heading4"], fontSize=11,
                                spaceBefore=3 * mm, spaceAfter=1 * mm),
        "body": ParagraphStyle("Body", parent=base["BodyText"], fontSize=10,
                               leading=14, spaceAfter=2 * mm),
        "item": ParagraphStyle("Item", parent=base["BodyText"], fontSize=10,
                               leading=14, leftIndent=6 * mm, bulletIndent=2 * mm,
                               spaceAfter=1 * mm),
        "video": ParagraphStyle("Video", parent=base["BodyText"], fontSize=10,
                                leading=14, leftIndent=4 * mm, spaceBefore=2 * mm,
                                spaceAfter=2 * mm, textColor="#333333"),
    }

    if fonts:
        regular, bold = fonts
        for name, style in styles.items():
            style.fontName = bold if name in {"title", "chapter", "unit",
                                              "subunit", "inner"} else regular
    return styles


def module_story(module, archive, styles, stats, unicode_ok=True,
                 transcripts_dir=None, include_hidden=False):
    """Flowables for one module, in course order."""
    from reportlab.platypus import Paragraph, Spacer
    from reportlab.lib.units import mm

    def para(text, style, bullet=None):
        return Paragraph(xml_escape(safe_text(text, unicode_ok)), style, bulletText=bullet)

    story = [para(module_label(module), styles["title"])]
    multi = len(module["chapters"]) > 1

    def skip(node, kind):
        """Record a node students cannot see, and say so rather than dropping it."""
        if include_hidden or not node.get("hidden"):
            return False
        stats["hidden_nodes"].append(
            (kind, node.get("title", "(untitled)"), node["hidden"]))
        return True

    for chapter in module["chapters"]:
        if skip(chapter, "chapter"):
            continue
        if multi:
            story.append(para(chapter.get("title", "(untitled)"), styles["chapter"]))

        for seq in chapter.get("sequentials", []):
            if skip(seq, "unit"):
                continue
            story.append(para(seq.get("title", "(untitled unit)"), styles["unit"]))
            stats["units"] += 1

            for vert in seq.get("verticals", []):
                if skip(vert, "subunit"):
                    continue
                story.append(para(vert.get("title", "(untitled subunit)"), styles["subunit"]))
                stats["subunits"] += 1

                for comp in vert.get("components", []):
                    ctype, url_name = comp.get("type"), comp.get("url_name")

                    if ctype == "html":
                        relpath = f"html/{url_name}.html"
                        if not archive.exists(relpath):
                            continue
                        stats["html"] += 1
                        for kind, text in html_blocks(archive, relpath,
                                                      include_hidden, stats):
                            if kind == "head":
                                story.append(para(text, styles["inner"]))
                            elif kind == "item":
                                story.append(para(text, styles["item"], bullet="•"))
                            else:
                                story.append(para(text, styles["body"]))

                    elif ctype == "video":
                        title, text = video_entry(url_name, vert.get("title", ""),
                                                  archive, stats, transcripts_dir)
                        story.append(para(f"Video: {title}", styles["inner"]))
                        story.append(para(text, styles["video"]))

                    else:
                        stats["skipped"][ctype] = stats["skipped"].get(ctype, 0) + 1

        story.append(Spacer(1, 4 * mm))
    return story


def write_pdf(path, module, story):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    label = module_label(module)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillGray(0.4)
        canvas.drawString(20 * mm, 12 * mm, label[:90])
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, str(doc.page))
        canvas.restoreState()

    SimpleDocTemplate(
        path, pagesize=A4, title=label, author="Update Study Buddy",
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
    ).build(story, onFirstPage=footer, onLaterPages=footer)


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--module", help="module number, or a chapter title fragment")
    parser.add_argument("--list", action="store_true", help="list modules and exit")
    parser.add_argument("--out-dir", default=ORGANIZED_CONTENT_DIR)
    parser.add_argument("--transcripts-dir", default=TRANSCRIPTS_DIR,
                        help="directory of transcripts keyed by video url_name")
    parser.add_argument("--tar", dest="tar_path",
                        help="course .tar.gz to read (default: the one "
                             "course_structure.json was built from)")
    parser.add_argument("--include-hidden", action="store_true",
                        help="also include staff-only units and text hidden from "
                             "sighted users (both are skipped by default)")
    args = parser.parse_args()

    if not os.path.exists(COURSE_STRUCTURE_PATH):
        print(f"[FAIL] {COURSE_STRUCTURE_PATH} not found. Run extract_edx.py first.")
        return 1
    with open(COURSE_STRUCTURE_PATH, "r", encoding="utf-8") as fh:
        structure = json.load(fh)
    modules = group_modules(structure.get("chapters", []))
    describe_source(structure)

    if args.list or not args.module:
        print(f"{len(modules)} module(s):\n")
        for module in modules:
            chapters = ", ".join(c.get("title", "?") for c in module["chapters"])
            print(f"  {module_filename(module):24} {module_label(module)}")
            if len(module["chapters"]) > 1:
                print(f"  {'':24} chapters: {chapters}")
        if not args.module:
            print("\nPass --module <number> to build one.")
        return 0

    wanted = args.module.strip().lower()
    chosen = [m for m in modules if (m["number"] or "").lower() == wanted]
    if not chosen:
        chosen = [m for m in modules if wanted in (m["title"] or "").lower()]
    if len(chosen) != 1:
        print(f"[FAIL] {'No' if not chosen else 'Ambiguous'} module for {args.module!r}. "
              "Use --list to see the options.")
        return 1
    module = chosen[0]

    try:
        archive = open_course_archive(args.tar_path)
    except (CourseArchiveError, FileNotFoundError) as exc:
        print(f"[FAIL] {exc}")
        return 1

    try:
        fonts = register_unicode_font()
        styles = build_styles(fonts)
    except ImportError:
        print("[FAIL] reportlab is required: pip install reportlab")
        return 1

    if fonts:
        print(f"[OK] Unicode font: {fonts[0]}")
    else:
        print("[WARN] No Unicode font found; em-dashes and curly quotes will be")
        print("       transliterated to ASCII rather than dropped.")

    stats = {"units": 0, "subunits": 0, "html": 0, "videos": 0, "transcripts": 0,
             "from_olx": 0, "from_store": 0, "video_missing": 0, "skipped": {},
             "hidden_nodes": [], "hidden_text_components": 0, "hidden_chars": 0}
    story = module_story(module, archive, styles, stats, unicode_ok=bool(fonts),
                         transcripts_dir=args.transcripts_dir,
                         include_hidden=args.include_hidden)

    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, module_filename(module))
    write_pdf(out_path, module, story)

    print(f"[OK] {out_path}")
    print(f"     {module_label(module)}")
    print(f"     {len(module['chapters'])} chapter(s), {stats['units']} unit(s), "
          f"{stats['subunits']} subunit(s), {stats['html']} html component(s)")
    if stats["videos"] or stats["video_missing"]:
        sources = []
        if stats["from_olx"]:
            sources.append(f"{stats['from_olx']} from the export")
        if stats["from_store"]:
            sources.append(f"{stats['from_store']} from {args.transcripts_dir}/")
        detail = f" ({', '.join(sources)})" if sources else ""
        print(f"     videos: {stats['videos']}, with transcript: {stats['transcripts']}"
              f"{detail}, unreadable xml: {stats['video_missing']}")
        if stats["videos"] and not stats["transcripts"]:
            print("     [WARN] No transcripts found. Drop them into "
                  f"{args.transcripts_dir}/ named")
            print("            <video url_name>.vtt (or .txt/.srt) and re-run.")
    if stats["skipped"]:
        detail = ", ".join(f"{n} {t}" for t, n in sorted(stats["skipped"].items()))
        print(f"     not included: {detail}")

    # Anything left out because students cannot see it is named, never dropped
    # quietly - the point is that the PDF matches the course page, and that is
    # only checkable if the difference is stated.
    if stats["hidden_nodes"]:
        print(f"\n     {len(stats['hidden_nodes'])} node(s) skipped as hidden "
              "from students:")
        for kind, title, why in stats["hidden_nodes"][:10]:
            print(f"       {kind:8} {str(title)[:40]:40} {why}")
        if len(stats["hidden_nodes"]) > 10:
            print(f"       ... and {len(stats['hidden_nodes']) - 10} more")
    if stats["hidden_text_components"]:
        print(f"\n     {stats['hidden_chars']} character(s) of screen-reader-only "
              f"text skipped across {stats['hidden_text_components']} component(s).")
        print("     That is text the course page does not show (sr-only / "
              "display:none).")
    if stats["hidden_nodes"] or stats["hidden_text_components"]:
        print("     Re-run with --include-hidden to keep it, or trace one phrase with:")
        print('       python tools/find_text.py "<a phrase from the PDF>"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
