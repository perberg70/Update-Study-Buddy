"""Transcript parsing and store lookup.

Run: python tests/test_transcripts.py
"""

import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from build_module_pdf import read_transcript, stored_transcript  # noqa: E402

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

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] vtt with speaker tags, srt, sjson, txt")
        print("  [PASS] store lookup by url_name and by title, missing handled")
    return not failures


if __name__ == "__main__":
    print("transcript parsing and store")
    raise SystemExit(0 if main() else 1)
