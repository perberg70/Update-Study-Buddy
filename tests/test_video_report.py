"""Video source classification: transcript / youtube / mp4-only / none.

Run: python tests/test_video_report.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fixtures import open_archive  # noqa: E402

from video_report import inspect, parse_duration, youtube_id, has_direct_mp4  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402


def build_fixture(base):
    files = {
        "course.xml": '<course url_name="r"/>',

        "video/v1.xml": '<video display_name="Has Transcript" sub="abc">'
                        '<video_asset client_video_id="a.mp4" duration="300">'
                        '<encoded_video url="https://x/a.mp4"/></video_asset></video>',
        "static/subs_abc.srt.sjson": json.dumps({"text": ["hello"]}),

        "video/v2.xml": '<video display_name="Broken Reference">'
                        '<video_asset client_video_id="b.mp4" duration="1800">'
                        '<encoded_video url="https://x/b.mp4"/></video_asset>'
                        '<transcript language="en" src="missing.srt"/></video>',

        "video/v3.xml": '<video display_name="YouTube Hosted" '
                        'youtube_id_1_0="1.00:dQw4w9WgXcQ">'
                        '<video_asset duration="2700"/></video>',
    }
    # v4 sits in a staff-only subunit: in the export, absent from the course.
    files["video/v4.xml"] = ('<video display_name="Staff Only">'
                             '<video_asset duration="600">'
                             '<encoded_video url="https://x/d.mp4"/></video_asset></video>')

    chapter = {"title": "1. Test", "sequentials": [{"title": "U", "verticals": [
        {"title": "S", "components": [{"type": "video", "url_name": f"v{i}"}
                                      for i in (1, 2, 3)]},
        {"title": "Retired", "hidden": "visible_to_staff_only=true",
         "components": [{"type": "video", "url_name": "v4"}]}]}]}
    return open_archive(os.path.join(base, "course.test.tar.gz"), files), chapter


def main():
    failures = []
    with tempfile.TemporaryDirectory() as base:
        archive, chapter = build_fixture(base)
        rows = {r["title"]: r for r in inspect(chapter, archive)}

        if len(rows) != 4:
            failures.append(f"expected 4 rows, got {len(rows)}")

        # Reported, but marked - a reporter that silently dropped it would
        # disagree with the PDF's count for no visible reason.
        staff = rows.get("Staff Only", {})
        if not staff:
            failures.append("a hidden video should still be reported")
        elif staff.get("hidden") != "visible_to_staff_only=true":
            failures.append(f"it should carry why: {staff.get('hidden')!r}")
        for name in ("Has Transcript", "Broken Reference", "YouTube Hosted"):
            if rows.get(name, {}).get("hidden"):
                failures.append(f"{name} is visible and must not be marked hidden")

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

        total = sum(r["duration"] for r in rows.values() if not r["hidden"])
        if total != 4800:
            failures.append(f"visible durations should total 4800s, got {total}")

    root = ET.fromstring('<video><video_asset duration="nonsense"/></video>')
    if parse_duration(root) != 0.0:
        failures.append("an unparseable duration should be 0, not a crash")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] transcript / dangling-reference / youtube / mp4 all classified")
        print("  [PASS] durations total, bad duration handled")
        print("  [PASS] hidden videos reported and marked, excluded from totals")
    return not failures


if __name__ == "__main__":
    print("video source classification")
    raise SystemExit(0 if main() else 1)
