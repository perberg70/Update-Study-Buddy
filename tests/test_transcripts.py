"""Transcript parsing and store lookup.

Run: python tests/test_transcripts.py
"""

import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fixtures import open_archive  # noqa: E402

import xml.etree.ElementTree as ET  # noqa: E402
from build_module_pdf import (archive_transcript, read_transcript,  # noqa: E402
                              stored_transcript, transcript_candidates,
                              video_entry)

TEAMS_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.500
<v Per Berg>Welcome everyone.</v>

2
00:00:04.500 --> 00:00:09.000
<v Per Berg>Today we look at generative AI.</v>

3
00:00:09.000 --> 00:00:14.000
<v Erik Sterner>Thanks Per.</v>
"""

SRT = """1
00:00:01,000 --> 00:00:02,000
First line

2
00:00:02,000 --> 00:00:03,000
Second line
"""


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


def main():
    failures = []
    with tempfile.TemporaryDirectory() as base:
        w = lambda name, text: io.open(os.path.join(base, name), "w",
                                       encoding="utf-8-sig").write(text)

        w("v1.vtt", TEAMS_VTT)
        text = read_transcript(os.path.join(base, "v1.vtt"))
        check(failures, "Per Berg: Welcome everyone." in text, "speaker prefix missing")
        check(failures, "Erik Sterner: Thanks Per." in text, "speaker change not marked")
        check(failures, text.count("Per Berg:") == 1,
              "a repeated speaker should be labelled once, not per cue")
        check(failures, "-->" not in text and "WEBVTT" not in text,
              "timecodes or header leaked into the text")

        w("v2.srt", SRT)
        srt = read_transcript(os.path.join(base, "v2.srt"))
        check(failures, srt == "First line Second line", f"srt parse: {srt!r}")

        w("v3.sjson", json.dumps({"text": ["alpha", "", "beta"]}))
        check(failures, read_transcript(os.path.join(base, "v3.sjson")) == "alpha beta",
              "sjson should join non-empty cues")

        w("v4.txt", "  plain   text  file \n more ")
        check(failures, read_transcript(os.path.join(base, "v4.txt")) == "plain text file more",
              "plain text should just be whitespace-normalised")

        # store lookup: by url_name, and by slugified display title
        check(failures, stored_transcript("v1", "", base).startswith("Per Berg:"),
              "lookup by url_name failed")
        w("Webinar_1_Recording.txt", "from the title key")
        check(failures, stored_transcript("nomatch", "Webinar 1 - Recording", base)
              == "from the title key", "lookup by slugified title failed")
        check(failures, stored_transcript("absent", "Also Absent", base) == "",
              "a missing transcript should be empty, not an error")
        check(failures, stored_transcript("v1", "", os.path.join(base, "nope")) == "",
              "a missing store directory should be empty, not an error")

        # Transcripts referenced by the OLX are read out of the archive itself.
        archive = open_archive(os.path.join(base, "course.t.tar.gz"), {
            "course.xml": '<course url_name="r"/>',
            "video/v1.xml": '<video display_name="Webinar" sub="abc"/>',
            "static/subs_abc.srt.sjson": json.dumps({"text": ["from", "the archive"]}),
            "video/v2.xml": '<video display_name="Dangling">'
                            '<transcript language="en" src="gone.srt"/></video>',
            "video/v3.xml": '<video display_name="Stored Elsewhere"/>',
        })

        found = transcript_candidates(ET.fromstring(archive.read_text("video/v1.xml")),
                                      archive)
        check(failures, found == ["static/subs_abc.srt.sjson"],
              f"sub= should resolve inside the archive, got {found}")
        check(failures, archive_transcript(archive, found[0]) == "from the archive",
              "an archived .sjson should parse like one on disk")
        check(failures, transcript_candidates(
            ET.fromstring(archive.read_text("video/v2.xml")), archive) == [],
              "a reference to a file the archive lacks must not resolve")

        stats = dict.fromkeys(
            ("videos", "transcripts", "from_olx", "from_store", "video_missing"), 0)
        title, text = video_entry("v1", "S", archive, stats)
        check(failures, (title, text) == ("Webinar", "from the archive"),
              f"video_entry should read the archive, got {(title, text)!r}")
        check(failures, stats["from_olx"] == 1, "an archived transcript counts as from_olx")

        w("v3.txt", "dropped in by hand")
        _, text = video_entry("v3", "S", archive, stats, transcripts_dir=base)
        check(failures, text == "dropped in by hand",
              "the store is still consulted when the archive has nothing")
        check(failures, stats["from_store"] == 1, "a store hit counts as from_store")

        _, text = video_entry("absent", "Subunit", archive, stats)
        check(failures, text.startswith("[no transcript"),
              "a video the archive lacks should be marked, not crash")
        check(failures, stats["video_missing"] == 1, "a missing video is counted")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] vtt with speaker tags, srt, sjson, txt")
        print("  [PASS] store lookup by url_name and by title, missing handled")
        print("  [PASS] OLX transcripts resolved and read inside the archive")
    return not failures


if __name__ == "__main__":
    print("transcript parsing and store")
    raise SystemExit(0 if main() else 1)
