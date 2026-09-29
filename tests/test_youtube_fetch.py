"""YouTube id parsing, and how fetch outcomes are classified.

The live HTTP path is not exercised here - it is stubbed - so this covers
everything except the request itself.

Run: python tests/test_youtube_fetch.py
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fixtures import make_archive  # noqa: E402

from olx_archive import CourseArchive  # noqa: E402

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


VIDEOS = [("ok", "idOK"), ("disabled", "idDIS"), ("missing", "idNONE"),
          ("boom", "idERR"), ("empty", "idEMPTY")]


def build_course(base):
    files = {"course.xml": '<course url_name="r"/>'}
    for name, vid in VIDEOS:
        files[f"video/{name}.xml"] = (
            f'<video display_name="{name}" youtube_id_1_0="1.00:{vid}"/>')
    tar = make_archive(os.path.join(base, "course.yt.tar.gz"), files)

    structure = {"chapters": [{"title": "1. M", "sequentials": [{"title": "U", "verticals": [
        {"title": "S", "components": [{"type": "video", "url_name": n}
                                      for n, _ in VIDEOS]}]}]}]}
    # This tool writes into the transcript store, so it checks that the
    # structure and the archive came from the same export.
    structure["_source"] = CourseArchive(tar).fingerprint()
    path = os.path.join(base, "structure.json")
    json.dump(structure, io.open(path, "w", encoding="utf-8"))
    return tar, path


def main():
    failures = []

    for xml, expected, why in ID_CASES:
        got = youtube_id(ET.fromstring(xml))
        if got != expected:
            failures.append(f"{why}: expected {expected!r}, got {got!r}")

    with tempfile.TemporaryDirectory() as base:
        tar, structure = build_course(base)
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
        real_require = fyt.require_captions_backend
        fyt.fetch_captions = stub
        fyt.require_captions_backend = lambda: None
        sys.argv = ["fetch_youtube_transcripts.py",
                    "--transcripts-dir", store, "--tar", tar]
        fyt.COURSE_STRUCTURE_PATH = structure
        try:
            code = fyt.main()
        finally:
            fyt.fetch_captions, sys.argv = real_fetch, real_argv
            fyt.require_captions_backend = real_require

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

        # A missing package is a precondition, not content that failed. It
        # must be reported once, with pip install, and attempt nothing.
        def absent():
            raise RuntimeError("youtube-transcript-api is not installed.\n"
                               "  pip install youtube-transcript-api")

        real_fetch, real_argv = fyt.fetch_captions, sys.argv
        real_require = fyt.require_captions_backend
        fyt.require_captions_backend = absent
        fyt.fetch_captions = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no video should be attempted with no backend"))
        buf = io.StringIO()
        sys.argv = ["fetch_youtube_transcripts.py", "--tar", tar,
                    "--transcripts-dir", os.path.join(base, "store2")]
        try:
            with contextlib.redirect_stdout(buf):
                code = fyt.main()
        finally:
            fyt.fetch_captions, sys.argv = real_fetch, real_argv
            fyt.require_captions_backend = real_require
        out = buf.getvalue()

        if code != 1:
            failures.append(f"a missing backend should exit 1, got {code}")
        if "pip install youtube-transcript-api" not in out:
            failures.append(f"the failure should name the install command:\n{out}")
        for marker in ("[ok]", "[none]", "[have]", "fetched 0"):
            if marker in out:
                failures.append(f"no per-video output with no backend, saw {marker!r}")

        # --dry-run contacts nothing, so it must work without the package.
        fyt.require_captions_backend = absent
        buf = io.StringIO()
        sys.argv = ["fetch_youtube_transcripts.py", "--dry-run", "--tar", tar,
                    "--transcripts-dir", os.path.join(base, "store3")]
        try:
            with contextlib.redirect_stdout(buf):
                code = fyt.main()
        finally:
            sys.argv = real_argv
            fyt.require_captions_backend = real_require
        dry = buf.getvalue()
        if code != 0 or dry.count("[would fetch]") != len(VIDEOS):
            failures.append(f"--dry-run should list all {len(VIDEOS)} videos "
                            f"without the package, got code={code}:\n{dry}")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] youtube id parsing, including multi-speed and url forms")
        print("  [PASS] success written; disabled / none / error / empty write nothing")
        print("  [PASS] a hard error exits non-zero")
        print("  [PASS] a missing package is one instruction, not a failed video;")
        print("         --dry-run still works without it")
    return not failures


if __name__ == "__main__":
    print("youtube caption fetching (HTTP stubbed)")
    raise SystemExit(0 if main() else 1)
