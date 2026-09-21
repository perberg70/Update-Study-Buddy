#!/usr/bin/env python3
"""Report what text is obtainable for every video in the course.

Read-only: parses the extracted OLX and lists files. Downloads nothing,
transcribes nothing.

Answers the question "can we get video transcripts, and what would it cost" by
distinguishing three cases per video:

  - a transcript file is referenced and present  -> free
  - the video is YouTube-hosted                  -> captions may be fetchable
  - neither                                      -> speech-to-text, priced by duration

It also lists every .srt/.sjson actually sitting in static/, referenced or not,
because "no transcripts" and "transcripts the resolver does not recognise" look
identical from the outside and need completely different fixes.

Usage:
    python tools/video_report.py
    python tools/video_report.py --module 1
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import COURSE_STRUCTURE_PATH, EXTRACT_DIR  # noqa: E402
from extract_edx import find_course_root  # noqa: E402
from build_module_pdf import group_modules, module_label, transcript_candidates  # noqa: E402

YOUTUBE_ATTRS = ("youtube_id_1_0", "youtube_id", "youtube")


def parse_duration(video_root):
    """Seconds from <video_asset duration=...>, or 0."""
    asset = video_root.find(".//video_asset")
    if asset is None:
        return 0.0
    try:
        return float(asset.get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def youtube_id(video_root):
    for attr in YOUTUBE_ATTRS:
        value = (video_root.get(attr) or "").strip()
        if value:
            # "1.00:abcdefg" style values carry the id after the colon.
            return value.split(":")[-1]
    for asset in video_root.findall(".//encoded_video"):
        url = asset.get("url") or ""
        if "youtu" in url:
            return url.rsplit("/", 1)[-1].split("?")[-1]
    return ""


def has_direct_mp4(video_root):
    return any((a.get("url") or "").endswith(".mp4")
               for a in video_root.findall(".//video_asset/encoded_video"))


def inspect(chapter, course_root):
    """One row per video component in *chapter*."""
    rows = []
    for seq in chapter.get("sequentials", []):
        for vert in seq.get("verticals", []):
            for comp in vert.get("components", []):
                if comp.get("type") != "video":
                    continue
                url_name = comp.get("url_name")
                path = os.path.join(course_root, "video", f"{url_name}.xml")
                row = {"unit": seq.get("title", ""), "subunit": vert.get("title", ""),
                       "title": url_name, "duration": 0.0, "mp4": False,
                       "youtube": "", "transcripts": [], "referenced": 0, "readable": False}
                if os.path.exists(path):
                    try:
                        root = ET.parse(path).getroot()
                        row["readable"] = True
                        row["title"] = html.unescape(
                            root.get("display_name") or vert.get("title") or url_name)
                        row["duration"] = parse_duration(root)
                        row["mp4"] = has_direct_mp4(root)
                        row["youtube"] = youtube_id(root)
                        row["transcripts"] = transcript_candidates(root, course_root)
                        row["referenced"] = len(root.findall(".//transcript")) + \
                            bool(root.get("transcripts")) + bool(root.get("sub"))
                    except Exception:
                        pass
                rows.append(row)
    return rows


def hhmm(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m" if seconds >= 3600 \
        else f"{seconds // 60}m{seconds % 60:02d}s"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--module", help="limit to one module number")
    parser.add_argument("--verbose", action="store_true", help="list every video")
    args = parser.parse_args()

    if not os.path.exists(COURSE_STRUCTURE_PATH):
        print(f"[FAIL] {COURSE_STRUCTURE_PATH} not found. Run extract_edx.py first.")
        return 1
    with open(COURSE_STRUCTURE_PATH, "r", encoding="utf-8") as fh:
        modules = group_modules(json.load(fh).get("chapters", []))
    if args.module:
        modules = [m for m in modules if (m["number"] or "") == args.module.strip()]
        if not modules:
            print(f"[FAIL] No module {args.module!r}.")
            return 1

    try:
        course_root = find_course_root(EXTRACT_DIR)
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}")
        return 1

    totals = {"videos": 0, "duration": 0.0, "with_transcript": 0,
              "youtube": 0, "mp4": 0, "referenced": 0, "no_source": 0}

    for module in modules:
        rows = [r for ch in module["chapters"] for r in inspect(ch, course_root)]
        if not rows:
            continue
        secs = sum(r["duration"] for r in rows)
        got = sum(1 for r in rows if r["transcripts"])
        print(f"\n{module_label(module)}")
        print(f"   {len(rows)} video(s), {hhmm(secs)} total, "
              f"{got} with a transcript file")

        for row in rows:
            totals["videos"] += 1
            totals["duration"] += row["duration"]
            totals["referenced"] += 1 if row["referenced"] else 0
            if row["transcripts"]:
                totals["with_transcript"] += 1
            if row["youtube"]:
                totals["youtube"] += 1
            if row["mp4"]:
                totals["mp4"] += 1
            if not row["transcripts"] and not row["youtube"] and not row["mp4"]:
                totals["no_source"] += 1

            if args.verbose or not row["transcripts"]:
                source = ("transcript" if row["transcripts"]
                          else "youtube" if row["youtube"]
                          else "mp4 only" if row["mp4"] else "NO SOURCE")
                dur = hhmm(row["duration"]) if row["duration"] else "unknown"
                print(f"     [{source:10}] {dur:>7}  {row['title'][:58]}")
                if row["referenced"] and not row["transcripts"]:
                    print(f"     {'':12} references a transcript, but the file is missing")

    static = os.path.join(course_root, "static")
    on_disk = sorted(glob.glob(os.path.join(static, "*.srt")) +
                     glob.glob(os.path.join(static, "*.sjson")))

    print("\n" + "=" * 68)
    print(f"  videos                    {totals['videos']}")
    print(f"  total duration            {hhmm(totals['duration'])}"
          f"{'  (durations absent from the XML)' if not totals['duration'] else ''}")
    print(f"  transcript file found     {totals['with_transcript']}")
    print(f"  reference one but missing {totals['referenced'] - totals['with_transcript']}")
    print(f"  YouTube-hosted            {totals['youtube']}")
    print(f"  direct .mp4               {totals['mp4']}")
    print(f"  no obtainable source      {totals['no_source']}")
    print()
    print(f"  transcript files in {static}: {len(on_disk)}")
    for path in on_disk[:10]:
        print(f"    {os.path.basename(path)}")
    if len(on_disk) > 10:
        print(f"    ... and {len(on_disk) - 10} more")

    if on_disk and not totals["with_transcript"]:
        print("\n  [!] Transcript files exist but none resolved to a video.")
        print("      The naming scheme differs from what the resolver expects;")
        print("      this is a cheap fix, not a transcription job.")
    elif not on_disk:
        print("\n  No transcript files in the export at all.")
        if totals["youtube"]:
            print(f"  {totals['youtube']} video(s) are YouTube-hosted - captions may be")
            print("  fetchable by id without any speech-to-text.")
        if totals["duration"]:
            hours = totals["duration"] / 3600
            print(f"  Speech-to-text would process {hhmm(totals['duration'])} of audio")
            print(f"  (~{hours * 0.5:.1f}-{hours * 3:.1f}h on CPU with local Whisper).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
