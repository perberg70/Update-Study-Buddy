"""Minimal PDF text checking for tests, with no third-party dependency.

pdfminer and pypdf both pull in `cryptography`, which is broken in some
sandboxes, so this reads what reportlab actually emits: ASCII85 + Flate encoded
content streams, and /ToUnicode CMaps for TrueType subset fonts.

`contains()` is the reliable entry point. It encodes the needle *through* each
font's CMap and looks for those bytes, rather than merging subset encodings
that may assign the same code to different characters.
"""

from __future__ import annotations

import base64
import re
import zlib


def _inflate(body: bytes) -> bytes:
    for step in (lambda b: base64.a85decode(b.strip(), adobe=True), zlib.decompress):
        try:
            body = step(body)
        except Exception:
            pass
    return body


def _streams(path: str):
    raw = open(path, "rb").read()
    return [_inflate(m.group(1))
            for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S)]


def _cmaps(streams):
    """One code -> character mapping per font subset found."""
    maps = []
    for stream in streams:
        if b"beginbfchar" not in stream:
            continue
        table = {}
        for block in re.findall(rb"beginbfchar(.*?)endbfchar", stream, re.S):
            for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
                try:
                    char = bytes.fromhex(dst.decode()).decode("utf-16-be")
                except Exception:
                    continue
                if char and char != "\x00":
                    table[bytes.fromhex(src.decode())] = char
        if table:
            maps.append(table)
    return maps


def _show_strings(content: bytes):
    """Raw operands of text-showing operators, in order."""
    out = []
    for match in re.finditer(rb"\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>", content):
        token = match.group(0)
        if token.startswith(b"<"):
            try:
                out.append(bytes.fromhex(re.sub(rb"\s", b"", token[1:-1]).decode()))
            except Exception:
                pass
            continue
        data = token[1:-1]
        data = re.sub(rb"\\(\d{1,3})", lambda m: bytes([int(m.group(1), 8) & 0xFF]), data)
        data = re.sub(rb"\\([()\\])", rb"\1", data)
        out.append(data)
    return out


def _candidate_texts(path: str):
    """One decoding per font subset, plus the built-in-font reading.

    reportlab splits strings across TJ array elements for kerning, so a needle
    is rarely contiguous in the byte stream - decode first, then search. Each
    candidate is correct for the runs set in its own font and garbled
    elsewhere, which is why they are kept separate rather than merged.
    """
    streams = _streams(path)
    chunks = _show_strings(b"\n".join(streams))
    texts = [" ".join(c.decode("latin-1") for c in chunks)]
    for table in _cmaps(streams):
        texts.append(" ".join(
            "".join(table.get(bytes([b]), "\ufffd") for b in chunk) for chunk in chunks
        ))
    return [re.sub(r"\s+", " ", t) for t in texts]


def contains(path: str, needle: str) -> bool:
    """True when *needle* appears in the PDF's rendered text."""
    flat = re.sub(r"\s+", " ", needle)
    return any(flat in text for text in _candidate_texts(path))


def extract_text(path: str) -> str:
    """Best-effort readable dump. Subset codes may be ambiguous across fonts;
    use contains() for assertions."""
    streams = _streams(path)
    content = b"\n".join(streams)
    merged = {}
    for table in _cmaps(streams):
        merged.update(table)

    out = []
    for match in re.finditer(rb"\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>", content):
        token = match.group(0)
        if token.startswith(b"<"):
            data = bytes.fromhex(re.sub(rb"\s", b"", token[1:-1]).decode())
        else:
            data = token[1:-1]
            data = re.sub(rb"\\(\d{1,3})", lambda m: bytes([int(m.group(1), 8) & 0xFF]), data)
            data = re.sub(rb"\\([()\\])", rb"\1", data)
        out.append("".join(merged.get(bytes([b]), chr(b)) for b in data))

    return re.sub(r"\s+", " ", " ".join(out))
