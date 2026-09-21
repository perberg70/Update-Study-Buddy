"""YouTube id parsing, and how fetch outcomes are classified.

The live HTTP path is not exercised here - it is stubbed - so this covers
everything except the request itself.

Run: python tests/test_youtube_fetch.py
"""

import io
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import fetch_youtube_transcripts as fyt  # noqa: E402
from video_report import youtube_id  # noqa: E402


class TranscriptsDisabled(Exception):
    pass


class NoTranscriptFound(Exception):
    pass


ID_CASES = [
    ('<video youtube_id_1_0="1.00:dQw4w9WgXcQ"/>', "dQw4w9WgXcQ", "1.00 form"),
    ('<video youtube="0.75:slow,1.00:real,1.25:fast"/>', "real", "multi-speed picks 1.00"),
    ('<video youtube_id_1_0="bareId"/>', "bareId", "bare id"),
    ('<video><video_asset><encoded_video url="https://youtu.be/abc123"/>'
     '</video_asset></video>', "abc123", "youtu.be url"),
    ('<video><video_asset><encoded_video url="https://cdn/x.mp4"/>'
     '</video_asset></video>', "", "plain mp4 is not youtube"),
]


def build_course(base):
    course = os.path.join(base, "course")
    os.makedirs(os.path.join(course, "video"))
    w = lambda p, t: io.open(p, "w", encoding="utf-8").write(t)
    w(f"{course}/course.xml", '<course url_name="r"/>')
    for name, vid in [("ok", "idOK"), ("disabled", "idDIS"),
                      ("missing", "idNONE"), ("boom", "idERR"), ("empty", "idEMPTY")]:
        w(f"{course}/video/{name}.xml",
          f'<video display_name="{name}" youtube_id_1_0="1.00:{vid}"/>')
    structure = {"chapters": [{"title": "1. M", "sequentials": [{"title": "U", "verticals": [
        {"title": "S", "components": [{"type": "video", "url_name": n}
                                      for n in ("ok", "disabled", "missing", "boom", "empty")]}]}]}]}
    path = os.path.join(base, "structure.json")
    json.dump(structure, io.open(path, "w", encoding="utf-8"))
    return course, path


def main():
    failures = []

    for xml, expected, why in ID_CASES:
        got = youtube_id(ET.fromstring(xml))
        if got != expected:
            failures.append(f"{why}: expected {expected!r}, got {got!r}")

    with tempfile.TemporaryDirectory() as base:
        course, structure = build_course(base)
        store = os.path.join(base, "transcripts")

        def stub(video_id, languages):
            if video_id == "idOK":
                return "captions for the good one", "en", "manual"
            if video_id == "idDIS":
                raise TranscriptsDisabled("owner disabled captions")
            if video_id == "idNONE":
                raise NoTranscriptFound("no track in en/sv")
            if video_id == "idEMPTY":
                return "   ", "en", "auto-generated"
            raise ConnectionError("network went away")

        real_fetch, real_argv = fyt.fetch_captions, sys.argv
        fyt.fetch_captions = stub
        sys.argv = ["fetch_youtube_transcripts.py",
                    "--transcripts-dir", store]
        os.environ["COURSE_STRUCTURE_PATH"] = structure
        fyt.COURSE_STRUCTURE_PATH = structure
        fyt.EXTRACT_DIR = base
        try:
            code = fyt.main()
        finally:
            fyt.fetch_captions, sys.argv = real_fetch, real_argv

        if code != 1:
            failures.append(f"a hard failure should exit non-zero, got {code}")

        written = os.path.join(store, "ok.txt")
        if not os.path.exists(written):
            failures.append("a successful fetch should be written to the store")
        elif io.open(written, encoding="utf-8").read() != "captions for the good one":
            failures.append("stored text does not match what was fetched")

        for name in ("disabled", "missing", "boom", "empty"):
            if os.path.exists(os.path.join(store, f"{name}.txt")):
                failures.append(f"{name}: nothing should be written when there is no text")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] youtube id parsing, including multi-speed and url forms")
        print("  [PASS] success written; disabled / none / error / empty write nothing")
        print("  [PASS] a hard error exits non-zero")
    return not failures


if __name__ == "__main__":
    print("youtube caption fetching (HTTP stubbed)")
    raise SystemExit(0 if main() else 1)
