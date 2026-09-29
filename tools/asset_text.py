#!/usr/bin/env python3
"""Read the text out of a course document held in the archive's static/.

Handbooks, briefs and reading lists uploaded in Studio travel inside the export
and are linked from the HTML. Their words are course material, so they belong
in the module PDF alongside the prose that links them.

Images are deliberately not handled. On this course they are generated from the
unit text they illustrate, so OCR would put the same sentences in twice.

Audio is course material too - an AI summary of a section, say - but it is
speech, so nothing here reads it. This module only finds it and names it:
tools/transcribe_videos.py transcribes it locally into the transcript store
under asset_key(), and tools/build_module_pdf.py reads it back by that key.

PDF needs a third-party reader. Both candidates are tried, because neither can
be relied on: pypdf and pdfminer both import `cryptography`, which is broken in
some environments - a package that imports and then raises on use looks
available to a naive check, so the probe here imports what it will actually
call and catches everything, not only ImportError.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile

# WordprocessingML and SpreadsheetML namespaces, by local name rather than
# prefix - the prefix is not fixed by the format.
W_TEXT = "}t"
W_PARA = "}p"
W_BREAK = ("}br", "}cr")

MAX_CHARS = 200_000   # a runaway document must not swamp the module it sits in

# /static/name.ext as OLX writes it in href/src, ignoring any ?query or #frag.
STATIC_REF_RE = re.compile(r"/static/([^\"'\s>?#\\]+)")

# edX keeps the uploaded filename but makes its static URL safe, replacing
# every character outside this set with an underscore. Derived from the real
# course: "Intro Learning with AI (Mod 2).png" is linked as
# "Intro_Learning_with_AI__Mod_2_.png", and "students' learning" as
# "students__learning" - so brackets and apostrophes go the way of spaces,
# while dot, dash and underscore survive.
URL_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def url_name(stored_name):
    """The /static/ spelling of a stored filename."""
    return URL_UNSAFE_RE.sub("_", stored_name)


def build_lookup(present):
    """{url spelling: stored name}, plus any pair that collides."""
    lookup, collisions = {}, {}
    for stored in present:
        key = url_name(stored)
        if key in lookup and lookup[key] != stored:
            collisions.setdefault(key, [lookup[key]]).append(stored)
        lookup[key] = stored
    return lookup, collisions


def static_lookup(archive):
    return build_lookup(archive.listdir("static"))[0]


# Audio uploaded as course material. Speech, so the local Whisper path applies
# rather than a document reader.
AUDIO_SUFFIXES = (".m4a", ".mp3", ".wav", ".ogg", ".aac", ".flac")


def linked_static(body, lookup, accept):
    """Stored names of the static files an HTML component links, in order.

    *accept* decides which kinds count. Each file once, however often it is
    linked; links to files absent from the archive are dropped here and
    reported by tools/asset_report.py.
    """
    out, seen = [], set()
    for link in STATIC_REF_RE.findall(body or ""):
        stored = lookup.get(link)
        if stored and stored not in seen and accept(stored):
            seen.add(stored)
            out.append(stored)
    return out


def linked_documents(body, lookup):
    """Stored names of the documents an HTML component links, in order."""
    return linked_static(body, lookup, is_document)


def linked_audio(body, lookup):
    """Stored names of the audio files an HTML component links, in order."""
    return linked_static(body, lookup, is_audio)


def is_audio(name):
    return name.lower().endswith(AUDIO_SUFFIXES)


def asset_key(stored_name):
    """The transcript-store key for a static audio file.

    One definition, used by the transcriber to write and by the PDF builder to
    read, so the two cannot drift apart.

    The hyphen in the prefix is load-bearing. stored_transcript() also finds
    transcripts by slugified video *title*, and that slug maps everything
    outside [A-Za-z0-9] to an underscore - it can never contain a hyphen. So no
    video title can ever resolve to an audio file's transcript, by construction.
    """
    stem = stored_name.rsplit(".", 1)[0] if "." in stored_name else stored_name
    slug = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]", "_", stem)).strip("_")
    return f"asset-{slug or 'unnamed'}"


def audio_source_id(archive, stored_name):
    """What a transcript of a static audio file was made from.

    Recorded by the transcriber and compared by the PDF builder, so it is
    defined once - the video equivalent once disagreed with itself between
    writer and reader, and every transcript read as stale the moment it was
    written. The archive's own hash, recorded beside this, is what catches a
    new export; this catches a transcript filed under the wrong file.
    """
    return f"static/{stored_name}:{archive.size('static/' + stored_name)}"


def pdf_backend():
    """(name, extract(bytes) -> str) for the first usable reader, or ('', None).

    Imports the entry point rather than the package: `import pypdf` succeeds
    even when `from pypdf import PdfReader` dies inside cryptography, so the
    shallow check reports a reader that cannot read.
    """
    try:
        from pypdf import PdfReader
    except Exception:
        pass
    else:
        def with_pypdf(data):
            reader = PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages)

        return "pypdf", with_pypdf

    try:
        from pdfminer.high_level import extract_text
    except Exception:
        pass
    else:
        def with_pdfminer(data):
            return extract_text(io.BytesIO(data)) or ""

        return "pdfminer", with_pdfminer

    return "", None


def extract_pdf(data):
    name, extract = pdf_backend()
    if not extract:
        return "", ("no PDF reader installed - pip install pypdf "
                    "(or pdfminer.six)")
    try:
        return normalise(extract(data)), f"read with {name}"
    except Exception as exc:
        return "", f"{name} could not read it: {type(exc).__name__}: {exc}"


def extract_docx(data):
    """Paragraph text from a .docx, with the stdlib only."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
    except Exception as exc:
        return "", f"not a readable .docx: {type(exc).__name__}"

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return "", f"document.xml is not valid XML: {exc}"

    paragraphs, current = [], []
    for node in root.iter():
        if node.tag.endswith(W_TEXT) and node.text:
            current.append(node.text)
        elif node.tag.endswith(W_BREAK):
            current.append(" ")
        elif node.tag.endswith(W_PARA) and current:
            paragraphs.append("".join(current))
            current = []
    if current:
        paragraphs.append("".join(current))
    return normalise("\n".join(paragraphs)), "read with the stdlib"


def extract_xlsx(data):
    """The shared strings of a .xlsx - its labels and prose, not its numbers.

    A spreadsheet read as prose is poor either way; this at least carries the
    headings and any written notes, and says that is what it did.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if "xl/sharedStrings.xml" not in names:
                return "", "no shared strings - the sheet holds only numbers"
            xml = zf.read("xl/sharedStrings.xml")
    except Exception as exc:
        return "", f"not a readable .xlsx: {type(exc).__name__}"

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return "", f"sharedStrings.xml is not valid XML: {exc}"

    cells = []
    for node in root:
        text = "".join(t.text or "" for t in node.iter() if t.tag.endswith(W_TEXT))
        if text.strip():
            cells.append(text.strip())
    return normalise(" / ".join(cells)), "cell text only, via the stdlib"


def extract_plain(data):
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return normalise(data.decode(encoding)), "read as text"
        except UnicodeDecodeError:
            continue
    return normalise(data.decode("utf-8", "replace")), "read as text (lossy)"


READERS = {
    ".pdf": extract_pdf,
    ".docx": extract_docx,
    ".xlsx": extract_xlsx,
    ".txt": extract_plain,
    ".md": extract_plain,
    ".csv": extract_plain,
}


def normalise(text):
    """Collapse the whitespace a document reader leaves behind."""
    text = re.sub(r"[ \t\r\f\v]+", " ", text or "")
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = text.strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n\n[truncated at {MAX_CHARS} characters]"
    return text


def is_document(name):
    return any(name.lower().endswith(ext) for ext in READERS)


def asset_text(archive, stored_name):
    """(text, how) for one static file. Empty text carries the reason in *how*."""
    ext = "." + stored_name.rsplit(".", 1)[-1].lower() if "." in stored_name else ""
    reader = READERS.get(ext)
    if reader is None:
        return "", f"{ext or 'no extension'} is not a document type"

    data = archive.read_bytes(f"static/{stored_name}")
    if data is None:
        return "", "not in the archive"
    if not data:
        return "", "empty file"
    return reader(data)
