"""What a student sees in a course HTML component, as text.

Every output that turns course HTML into text reads it through here: the
module PDFs (tools/build_module_pdf.py) and the chapter text files the notebook
sync uploads (organize_content.py). tools/find_text.py uses the same rules to
explain what it finds. One definition, so they cannot disagree about what is on
the page - and they did: the sync path stripped tags with a regex and so carried
every screen-reader description into the notebook, while the PDF path hid those
but printed each image's alt text.

Two kinds of text are left out.

Text hidden from sighted users - sr-only, display:none, aria-hidden and the
like. Left out by default; include_hidden restores it, for diagnosis.

Image descriptions, in English or Swedish. Left out always, include_hidden or
not: on this course the images are drawn from the unit text beside them, so a
description says the same thing a second time. Recognised as

  * an image's alt text - never emitted at all;
  * an element an image names with aria-describedby or aria-details;
  * an element whose class names it one (image-description, alt-text,
    bildbeskrivning, syntolkning ...);
  * hidden text inside a <figure>, or straight after an image - the course's
    own pattern for a long description;
  * in a component that contains an image: a <details> whose <summary> is a
    description label, a paragraph that opens with one ("Image description:",
    "Bildbeskrivning:", "Syntolkning:" ...), and a label standing alone as a
    heading or paragraph together with what follows it, up to the next heading
    or the end of the element holding it;
  * an inline SVG's <title> and <desc>.

The label rules need an image in the same component because this is a course
about generative AI: "Image description: a cat on a skateboard" in a unit with
no image is far more likely a prompt example than a description.

Visible captions (<figcaption>) are page text and stay, unless they too open
with a description label.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# --------------------------------------------------------------------------
# Hidden from sighted users
# --------------------------------------------------------------------------

HIDDEN_CLASS_RE = re.compile(
    r"\b(sr-only|sr_only|screen-?reader(-only|-text)?|visually-?hidden|"
    r"hidden|hide|a11y-?only|accessible-?text|invisible)\b", re.I)
# clip:rect(...) and clip-path:inset(50%) are the inline form of sr-only.
# position:absolute alone is not: layout uses it constantly.
HIDDEN_STYLE_RE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|"
    r"clip\s*:\s*rect\(|clip-path\s*:\s*inset\(\s*50%|"
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
# Image descriptions
# --------------------------------------------------------------------------

IMAGE_TAGS = {"img", "svg", "picture", "figure", "canvas"}
IMAGE_ROLES = {"img", "figure", "image"}

DESCRIPTION_CLASS_RE = re.compile(
    r"(?<![A-Za-z])(?:(?:image|img|figure|fig|picture|graphic)[-_]?"
    r"(?:desc|description|alt)|long[-_]?desc(?:ription)?|alt[-_]?text|"
    r"text[-_]?alternative|bild[-_]?beskrivning|syntolkning)(?![A-Za-z])", re.I)

_LABELS = [
    # English
    r"image\s+descriptions?",
    r"(?:picture|figure|graphic|chart|diagram|visual|photo)\s+descriptions?",
    r"descriptions?\s+of\s+(?:the\s+|this\s+)?(?:image|picture|figure|graphic|"
    r"chart|diagram|illustration|photo)",
    r"(?:long|text)\s+descriptions?",
    r"alt(?:ernative)?[\s-]?text",
    r"text\s+alternative",
    # Swedish
    r"bildbeskrivning(?:ar|en|arna)?",
    r"(?:figur|diagram|grafik|foto)beskrivning(?:ar|en|arna)?",
    r"beskrivning\s+av\s+(?:bilden|bild|bilderna|figuren|figur|diagrammet|"
    r"diagram|illustrationen|grafiken|fotot)",
    r"syntolkning(?:en|ar)?",
    r"alternativ\s+text",
    r"alt[\s-]?text(?:en)?",
    r"textalternativ(?:et)?",
]
_LABEL = r"(?:" + "|".join(_LABELS) + r")"
# "Show image description", "Visa bildbeskrivning" - disclosure wording.
_VERB = r"(?:(?:show|view|read|open|hide|see|visa|läs|se|öppna|dölj)\s+)?"
# "Image description / Bildbeskrivning", "Bildbeskrivning (English below)".
_ONE = rf"{_VERB}{_LABEL}(?:\s*\([^)]{{0,40}}\))?"
_LABELLED = rf"^[^\w]*{_ONE}(?:\s*[/|]\s*{_ONE})*"

BARE_LABEL_RE = re.compile(_LABELLED + r"\s*[:：.]?\s*$", re.I)
OPENS_WITH_LABEL_RE = re.compile(_LABELLED + r"\s*[:：–—-]\s*\S", re.I)


def is_description_label(text):
    """The whole text is a description label, as a heading or summary would be."""
    return bool(BARE_LABEL_RE.match(text or ""))


def opens_with_description_label(text):
    """The text is a description introduced by its label."""
    return bool(OPENS_WITH_LABEL_RE.match(text or ""))


def _is_image(tag, attrs):
    return tag in IMAGE_TAGS or (attrs.get("role") or "").strip().lower() in IMAGE_ROLES


class _Prescan(HTMLParser):
    """What the main pass must know before it starts: which ids an image names
    as its description, and whether the component has an image at all."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.described_ids, self.has_image = set(), False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if _is_image(tag, values):
            self.has_image = True
            for key in ("aria-describedby", "aria-details"):
                self.described_ids.update((values.get(key) or "").split())


# --------------------------------------------------------------------------
# HTML -> blocks
# --------------------------------------------------------------------------

class HtmlToBlocks(HTMLParser):
    """Turn course HTML into ('head'|'item'|'para', text) blocks.

    Unlike a tag-stripping regex this drops script/style *content*, keeps
    paragraph and list boundaries, and (via convert_charrefs) resolves entities
    so no literal &amp; or &ouml; reaches the document.

    Use parse_html(), which runs the prescan this needs; constructed directly,
    only the rules that need no prescan apply.
    """

    SKIP = {"script", "style", "head", "title", "desc"}
    VOID = {"br", "img", "hr", "input", "meta", "link", "area", "base",
            "col", "embed", "param", "source", "track", "wbr"}
    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BREAKS = {"p", "div", "section", "article", "br", "tr", "ul", "ol", "table",
              "blockquote", "figure", "figcaption", "details"}

    def __init__(self, include_hidden=False, described_ids=(), has_image=False):
        super().__init__(convert_charrefs=True)
        self.include_hidden = include_hidden
        self.described_ids = set(described_ids)
        self.has_image = has_image
        self.blocks, self._buf, self._kind, self._href = [], [], "para", ""
        self._skip = 0
        # Every open non-void element, as a dict. Suppression is counted by
        # depth, not held in a flag, so a nested element closing cannot end
        # its parent's suppression early.
        self._tags = []
        self._hidden_depth = 0
        self._desc_depth = 0
        self._desc_reason, self._desc_text = "", []
        self._figure_depth = 0
        self._after_image = False
        self._details = []          # open <details>: blocks index, summary text, in summary
        self._label_depth = None    # set while inside a standalone label's section
        self.hidden_chars = 0
        self.removed = []           # (why, text) for each image description left out

    # -- bookkeeping ---------------------------------------------------------

    def _suppressed(self):
        return self._desc_depth or self._hidden_depth

    def _remove(self, why, text):
        text = " ".join((text or "").split())
        if text:
            self.removed.append((why, text))

    def _flush(self):
        text = " ".join("".join(self._buf).split())
        kind, self._buf, self._kind = self._kind, [], "para"
        if not text:
            return
        if self._details and self._details[-1]["open"]:
            # A <summary> is judged whole, with its <details>, in _close_summary.
            self.blocks.append((kind, text))
            return
        if self.has_image and self._label_depth is not None:
            if kind == "head" and not is_description_label(text):
                self._label_depth = None
            else:
                self._remove("under a description label", text)
                return
        if self.has_image and is_description_label(text):
            self._remove("description label", text)
            self._label_depth = len(self._tags)
            return
        if self.has_image and opens_with_description_label(text):
            self._remove("opens with a description label", text)
            return
        self.blocks.append((kind, text))

    def _describes_image(self, attrs, visually_hidden):
        if attrs.get("id") and attrs["id"] in self.described_ids:
            return "named by an image's aria-describedby"
        if DESCRIPTION_CLASS_RE.search(attrs.get("class") or ""):
            return "class marks it an image description"
        if visually_hidden and self._figure_depth:
            return "hidden text inside a figure"
        if visually_hidden and self._after_image:
            return "hidden text straight after an image"
        return ""

    # -- parsing -------------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        values = dict(attrs)

        if tag in self.VOID:
            if tag == "img":
                # Alt text is a description by definition: never emitted.
                # Recorded, so a report can say where it went.
                if not self._desc_depth:
                    self._remove("alt text", values.get("alt"))
                self._after_image = True
            elif tag == "br" and not self._suppressed():
                self._flush()
            return

        visually_hidden = hides_content(attrs)
        hides = visually_hidden and not self.include_hidden
        why = "" if self._desc_depth else self._describes_image(values, visually_hidden)
        if not visually_hidden:
            self._after_image = False

        # Flush before this element joins the stack, so a standalone label's
        # section is measured from the element that holds it.
        block_level = (tag in self.HEADINGS or tag == "li" or tag in self.BREAKS
                       or tag == "summary")
        if not self._suppressed() and (hides or why or block_level):
            self._flush()

        entry = {"tag": tag, "hides": hides, "desc": bool(why), "details": False}
        self._tags.append(entry)
        if hides:
            self._hidden_depth += 1
        if why:
            self._desc_reason, self._desc_text = why, []
            self._desc_depth += 1
        if tag == "figure":
            self._figure_depth += 1
        if _is_image(tag, values) and tag != "figure":
            self._after_image = True

        if self._suppressed():
            return

        if tag == "details":
            self._details.append({"start": len(self.blocks), "summary": [],
                                  "open": False})
            entry["details"] = True
        elif tag == "summary" and self._details:
            self._details[-1]["open"] = True
        elif tag in self.HEADINGS:
            self._kind = "head"
        elif tag == "li":
            self._kind = "item"
        elif tag == "a":
            self._href = values.get("href", "") or ""

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in self.VOID:
            if tag == "br" and not self._suppressed():
                self._flush()
            return

        index = next((i for i in range(len(self._tags) - 1, -1, -1)
                      if self._tags[i]["tag"] == tag), None)
        if index is None:
            return          # a stray end tag: nothing of ours to close
        was_suppressed = self._suppressed()

        if tag == "summary" and self._details and not was_suppressed:
            self._close_summary(index)
        if tag == "a" and not was_suppressed:
            if self._href.startswith(("http://", "https://")):
                self._buf.append(f" <{self._href}> ")
            self._href = ""

        for entry in self._tags[index:]:
            if entry["hides"]:
                self._hidden_depth = max(0, self._hidden_depth - 1)
            if entry["desc"]:
                self._desc_depth = max(0, self._desc_depth - 1)
                if not self._desc_depth:
                    self._remove(self._desc_reason, "".join(self._desc_text))
                    self._desc_text = []
            if entry["tag"] == "figure":
                self._figure_depth = max(0, self._figure_depth - 1)
            if entry["details"] and self._details:
                self._details.pop()
        del self._tags[index:]

        # Flushed after the element leaves the stack, for the same reason.
        if not was_suppressed and (tag in self.HEADINGS or tag == "li"
                                   or tag in self.BREAKS or tag == "summary"):
            self._flush()

        # A standalone label's section ends with the element that held it.
        if self._label_depth is not None and len(self._tags) < self._label_depth:
            self._flush()
            self._label_depth = None

    def _close_summary(self, summary_index):
        """A <summary> that labels its <details> as a description takes it all."""
        self._flush()
        details = self._details[-1]
        details["open"] = False
        label = " ".join("".join(details["summary"]).split())
        if not self.has_image or not (is_description_label(label)
                                      or opens_with_description_label(label)):
            return
        # One record for the whole disclosure: its label and what it holds.
        opening = " ".join(text for _kind, text in self.blocks[details["start"]:])
        del self.blocks[details["start"]:]
        for entry in reversed(self._tags[:summary_index]):
            if entry["tag"] == "details":
                if not entry["desc"]:
                    entry["desc"] = True
                    self._desc_reason = "disclosure labelled as a description"
                    self._desc_text = [opening, " "]
                    self._desc_depth += 1
                break

    def handle_data(self, data):
        if self._skip:
            return
        if self._desc_depth:
            self._desc_text.append(data)
            return
        if self._hidden_depth:
            self.hidden_chars += len(data.strip())
            return
        if data.strip():
            self._after_image = False
        self._buf.append(data)
        if self._details and self._details[-1]["open"]:
            self._details[-1]["summary"].append(data)

    def close(self):
        super().close()
        self._flush()
        if self._desc_depth and self._desc_text:
            self._remove(self._desc_reason, "".join(self._desc_text))


def parse_html(html, include_hidden=False):
    """Parse one component. The parser carries .blocks, .removed, .hidden_chars."""
    scan = _Prescan()
    scan.feed(html or "")
    scan.close()
    parser = HtmlToBlocks(include_hidden=include_hidden,
                          described_ids=scan.described_ids,
                          has_image=scan.has_image)
    parser.feed(html or "")
    parser.close()
    return parser


def record(stats, parser):
    """Add one parsed component's leftovers to a running *stats* dict."""
    if stats is None:
        return
    if parser.hidden_chars:
        stats["hidden_text_components"] = stats.get("hidden_text_components", 0) + 1
        stats["hidden_chars"] = stats.get("hidden_chars", 0) + parser.hidden_chars
    if parser.removed:
        stats["image_descriptions"] = (stats.get("image_descriptions", 0)
                                       + len(parser.removed))
        by_reason = stats.setdefault("image_descriptions_by_reason", {})
        for why, _text in parser.removed:
            by_reason[why] = by_reason.get(why, 0) + 1


def visible_text(html, stats=None):
    """The component's visible text, one block per line, list items dashed."""
    parser = parse_html(html)
    record(stats, parser)
    return "\n".join(f"- {text}" if kind == "item" else text
                     for kind, text in parser.blocks)
