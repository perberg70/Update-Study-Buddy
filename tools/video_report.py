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
import html
import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import COURSE_STRUCTURE_PATH  # noqa: E402
from olx_archive import CourseArchiveError, open_course_archive  # noqa: E402
from build_module_pdf import (group_modules, module_label,  # noqa: E402
                              transcript_candidates, youtube_id)

def parse_duration(video_root):
    """Seconds from <video_asset duration=...>, or 0."""
    asset = video_root.find(".//video_asset")
    if asset is None:
        return 0.0
    try:
        return float(asset.get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def has_direct_mp4(video_root):
    return any((a.get("url") or "").endswith(".mp4")
               for a in video_root.findall(".//video_asset/encoded_video"))


def inspect(chapter, archive):
    """One row per video component in *chapter*.

    Hidden videos are reported, not dropped: this says what the export holds.
    But each carries why a student cannot see it, so the count here and the
    count in a module PDF - which skips them - do not disagree silently.
    """
    rows = []
    for seq in chapter.get("sequentials", []):
        for vert in seq.get("verticals", []):
            hidden = (chapter.get("hidden") or seq.get("hidden")
                      or vert.get("hidden") or "")
            for comp in vert.get("components", []):
                if comp.get("type") != "video":
                    continue
                url_name = comp.get("url_name")
                xml = archive.read_text(f"video/{url_name}.xml")
                row = {"unit": seq.get("title", ""), "subunit": vert.get("title", ""),
                       "title": url_name, "duration": 0.0, "mp4": False,
                       "youtube": "", "transcripts": [], "referenced": 0,
                       "readable": False, "hidden": hidden}
                if xml is not None:
                    try:
                        root = ET.fromstring(xml)
                        row["readable"] = True
                        row["title"] = html.unescape(
                            root.get("display_name") or vert.get("title") or url_name)
                        row["duration"] = parse_duration(root)
                        row["mp4"] = has_direct_mp4(root)
                        row["youtube"] = youtube_id(root)
                        row["transcripts"] = transcript_candidates(root, archive)
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
    parser.add_argument("--tar", dest="tar_path",
                        help="course .tar.gz to read (default: the one "
                             "course_structure.json was built from)")
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
        archive = open_course_archive(args.tar_path)
    except (CourseArchiveError, FileNotFoundError) as exc:
        print(f"[FAIL] {exc}")
        return 1

    totals = {"videos": 0, "duration": 0.0, "with_transcript": 0, "hidden": 0,
              "youtube": 0, "mp4": 0, "referenced": 0, "no_source": 0}

    for module in modules:
        rows = [r for ch in module["chapters"] for r in inspect(ch, archive)]
        if not rows:
            continue
        visible = [r for r in rows if not r["hidden"]]
        secs = sum(r["duration"] for r in visible)
        got = sum(1 for r in visible if r["transcripts"])
        print(f"\n{module_label(module)}")
        print(f"   {len(visible)} video(s) students can see, {hhmm(secs)} total, "
              f"{got} with a transcript file")
        if len(rows) != len(visible):
            print(f"   {len(rows) - len(visible)} more in the export but hidden "
                  "from students (not built into PDFs)")

        for row in rows:
            if row["hidden"]:
                if args.verbose:
                    print(f"     [{'hidden':10}] {hhmm(row['duration']):>7}  "
                          f"{row['title'][:58]}")
                    print(f"     {'':12} {row['hidden']}")
                totals["hidden"] += 1
                continue
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

    on_disk = sorted(f"static/{name}" for name in archive.listdir("static")
                     if name.lower().endswith((".srt", ".sjson")))

    print("\n" + "=" * 68)
    print(f"  videos students can see   {totals['videos']}")
    if totals["hidden"]:
        print(f"  hidden from students      {totals['hidden']}  "
              "(--verbose lists them)")
    print(f"  total duration            {hhmm(totals['duration'])}"
          f"{'  (durations absent from the XML)' if not totals['duration'] else ''}")
    print(f"  transcript file found     {totals['with_transcript']}")
    print(f"  reference one but missing {totals['referenced'] - totals['with_transcript']}")
    print(f"  YouTube-hosted            {totals['youtube']}")
    print(f"  direct .mp4               {totals['mp4']}")
    print(f"  no obtainable source      {totals['no_source']}")
    print()
    print(f"  transcript files in the archive's static/: {len(on_disk)}")
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
