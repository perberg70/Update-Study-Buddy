"""Video source classification: transcript / youtube / mp4-only / none.

Run: python tests/test_video_report.py
"""

import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from video_report import inspect, parse_duration, youtube_id, has_direct_mp4  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402


def build_fixture(base):
    course = os.path.join(base, "course")
    os.makedirs(os.path.join(course, "video"))
    os.makedirs(os.path.join(course, "static"))
    w = lambda p, t: io.open(p, "w", encoding="utf-8").write(t)

    w(f"{course}/video/v1.xml", '<video display_name="Has Transcript" sub="abc">'
      '<video_asset client_video_id="a.mp4" duration="300">'
      '<encoded_video url="https://x/a.mp4"/></video_asset></video>')
    w(f"{course}/static/subs_abc.srt.sjson", json.dumps({"text": ["hello"]}))

    w(f"{course}/video/v2.xml", '<video display_name="Broken Reference">'
      '<video_asset client_video_id="b.mp4" duration="1800">'
      '<encoded_video url="https://x/b.mp4"/></video_asset>'
      '<transcript language="en" src="missing.srt"/></video>')

    w(f"{course}/video/v3.xml",
      '<video display_name="YouTube Hosted" youtube_id_1_0="1.00:dQw4w9WgXcQ">'
      '<video_asset duration="2700"/></video>')

    chapter = {"title": "1. Test", "sequentials": [{"title": "U", "verticals": [
        {"title": "S", "components": [{"type": "video", "url_name": f"v{i}"}
                                      for i in (1, 2, 3)]}]}]}
    return course, chapter


def main():
    failures = []
    with tempfile.TemporaryDirectory() as base:
        course, chapter = build_fixture(base)
        rows = {r["title"]: r for r in inspect(chapter, course)}

        if len(rows) != 3:
            failures.append(f"expected 3 videos, got {len(rows)}")

        got = rows.get("Has Transcript", {})
        if not got.get("transcripts"):
            failures.append("a referenced, present .sjson should resolve")
        if got.get("duration") != 300:
            failures.append(f"duration should be 300, got {got.get('duration')}")

        broken = rows.get("Broken Reference", {})
        if broken.get("transcripts"):
            failures.append("a transcript reference to a missing file must not resolve")
        if not broken.get("referenced"):
            failures.append("the dangling reference should still be counted")
        if not broken.get("mp4"):
            failures.append("direct mp4 should be detected")

        tube = rows.get("YouTube Hosted", {})
        if tube.get("youtube") != "dQw4w9WgXcQ":
            failures.append(f"youtube id mis-parsed: {tube.get('youtube')!r}")
        if tube.get("mp4"):
            failures.append("a YouTube-only video has no direct mp4")

        total = sum(r["duration"] for r in rows.values())
        if total != 4800:
            failures.append(f"durations should total 4800s, got {total}")

    root = ET.fromstring('<video><video_asset duration="nonsense"/></video>')
    if parse_duration(root) != 0.0:
        failures.append("an unparseable duration should be 0, not a crash")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] transcript / dangling-reference / youtube / mp4 all classified")
        print("  [PASS] durations total, bad duration handled")
    return not failures


if __name__ == "__main__":
    print("video source classification")
    raise SystemExit(0 if main() else 1)
