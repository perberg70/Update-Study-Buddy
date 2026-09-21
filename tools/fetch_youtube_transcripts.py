#!/usr/bin/env python3
"""Fetch captions for the course's YouTube-hosted videos into the transcript store.

Nine of the course's 26 videos are YouTube-embedded and have no .mp4, so audio
transcription cannot reach them at all. Their captions are usually published and
fetchable by video id, which is far cheaper than speech-to-text.

This sends no course content anywhere. It asks YouTube for text YouTube already
publishes, keyed by a video id taken from the course XML.

Results land in the transcript store as <url_name>.txt, so
tools/build_module_pdf.py picks them up with no further configuration.

Usage:
    python tools/fetch_youtube_transcripts.py --dry-run
    python tools/fetch_youtube_transcripts.py
    python tools/fetch_youtube_transcripts.py --module 1 --languages en sv
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

from config import COURSE_STRUCTURE_PATH, TRANSCRIPTS_DIR  # noqa: E402
from olx_archive import CourseArchiveError, open_course_archive  # noqa: E402
from build_module_pdf import group_modules, module_label  # noqa: E402
from video_report import youtube_id  # noqa: E402


def youtube_videos(modules, archive):
    """(module, url_name, title, video_id) for every YouTube-hosted video."""
    found = []
    for module in modules:
        for chapter in module["chapters"]:
            for seq in chapter.get("sequentials", []):
                for vert in seq.get("verticals", []):
                    for comp in vert.get("components", []):
                        if comp.get("type") != "video":
                            continue
                        url_name = comp.get("url_name")
                        xml = archive.read_text(f"video/{url_name}.xml")
                        if xml is None:
                            continue
                        try:
                            root = ET.fromstring(xml)
                        except Exception:
                            continue
                        vid = youtube_id(root)
                        if not vid:
                            continue
                        title = html.unescape(
                            root.get("display_name") or vert.get("title") or url_name)
                        found.append((module, url_name, title, vid))
    return found


def fetch_captions(video_id, languages):
    """(text, language, kind) for a video, or raise.

    Prefers a human-written track over auto-generated: auto captions on a
    technical talk mangle terminology badly enough to be worth avoiding when
    there is a choice.
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    listing = api.list(video_id)

    try:
        transcript = listing.find_manually_created_transcript(list(languages))
        kind = "manual"
    except Exception:
        transcript = listing.find_transcript(list(languages))
        kind = "auto-generated" if transcript.is_generated else "manual"

    fetched = transcript.fetch()
    parts = [snippet.text.strip() for snippet in fetched
             if getattr(snippet, "text", "").strip()]
    return " ".join(parts), transcript.language_code, kind


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--module", help="limit to one module number")
    parser.add_argument("--languages", nargs="+", default=["en", "sv"],
                        help="preferred caption languages, best first")
    parser.add_argument("--transcripts-dir", default=TRANSCRIPTS_DIR)
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be fetched, contact nobody")
    parser.add_argument("--force", action="store_true",
                        help="refetch even when a transcript already exists")
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

    videos = youtube_videos(modules, archive)
    if not videos:
        print("No YouTube-hosted videos found.")
        return 0

    print(f"{len(videos)} YouTube-hosted video(s)"
          f"{' - dry run, nothing will be contacted' if args.dry_run else ''}\n")

    os.makedirs(args.transcripts_dir, exist_ok=True)
    counts = {"fetched": 0, "existing": 0, "none": 0, "failed": 0}
    current = None

    for module, url_name, title, video_id in videos:
        if module is not current:
            current = module
            print(f"  {module_label(module)}")

        out_path = os.path.join(args.transcripts_dir, f"{url_name}.txt")
        if os.path.exists(out_path) and not args.force:
            counts["existing"] += 1
            print(f"    [have] {title[:52]}")
            continue

        if args.dry_run:
            print(f"    [would fetch] {title[:44]}  (id {video_id})")
            continue

        try:
            text, language, kind = fetch_captions(video_id, args.languages)
        except Exception as exc:
            name = type(exc).__name__
            if name in ("TranscriptsDisabled", "NoTranscriptFound"):
                counts["none"] += 1
                print(f"    [none] {title[:46]}  ({name})")
            else:
                counts["failed"] += 1
                print(f"    [FAIL] {title[:46]}  ({name}: {exc})")
            continue

        if not text.strip():
            counts["none"] += 1
            print(f"    [none] {title[:46]}  (empty transcript)")
            continue

        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        counts["fetched"] += 1
        print(f"    [ok]   {title[:46]}  ({language}, {kind}, {len(text)} chars)")

    print()
    if args.dry_run:
        print("  Dry run: nothing fetched, nothing written.")
        return 0
    print(f"  fetched {counts['fetched']}, already present {counts['existing']}, "
          f"no captions {counts['none']}, failed {counts['failed']}")
    if counts["fetched"]:
        print(f"  Written to {args.transcripts_dir}/ - re-run build_module_pdf.py "
              "to include them.")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
