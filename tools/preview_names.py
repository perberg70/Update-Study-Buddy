#!/usr/bin/env python3
"""Predict what organize_content.py will name things - without downloading anything.

organize_content.py downloads and transcodes every video, which is slow. But the
names it produces come purely from the course XML, so they can be computed after
extract_edx.py alone. This scores those predicted names against the notebook's
current sources so you can see, before spending that time, how many will match.

Usage:
    python extract_edx.py          # fast: unpack + parse XML, no downloads
    python tools/preview_names.py
"""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compare_sources as cs  # noqa: E402
from config import COURSE_STRUCTURE_PATH, CURRENT_SOURCES_FILE, EXTRACT_DIR  # noqa: E402
from extract_edx import find_course_root  # noqa: E402
from organize_content import chapter_dir_name, video_output_name  # noqa: E402


def predicted_names(structure, extract_dir):
    """Names organize_content.py would produce, as (name, type, chapter) tuples."""
    course_root = find_course_root(extract_dir)
    out = []
    for index, chapter in enumerate(structure["chapters"]):
        ch_name = chapter_dir_name(index, chapter["title"])
        has_html = False

        for seq in chapter.get("sequentials", []):
            for vert in seq.get("verticals", []):
                for comp in vert.get("components", []):
                    if comp["type"] == "html":
                        html_path = os.path.join(
                            course_root, "html", f"{comp['url_name']}.html"
                        )
                        if os.path.exists(html_path):
                            has_html = True

                    elif comp["type"] == "video":
                        xml_path = os.path.join(
                            course_root, "video", f"{comp['url_name']}.xml"
                        )
                        if not os.path.exists(xml_path):
                            continue
                        try:
                            root = ET.parse(xml_path).getroot()
                        except Exception:
                            continue
                        # organize_content only emits an mp3 when a direct mp4 exists.
                        has_mp4 = any(
                            (a.get("url") or "").endswith(".mp4")
                            for a in root.findall(".//video_asset/encoded_video")
                        )
                        if not has_mp4:
                            continue
                        stem = video_output_name(root, vert.get("title", ""), comp["url_name"])
                        out.append((f"{stem}.mp3", "audio", chapter["title"]))

        if has_html:
            out.append((f"{ch_name}.txt", "text", chapter["title"]))
    return out


def main() -> int:
    if not os.path.exists(COURSE_STRUCTURE_PATH):
        print(f"[FAIL] {COURSE_STRUCTURE_PATH} not found. Run extract_edx.py first")
        print("       (that step only unpacks and parses XML - no downloads).")
        return 1

    with open(COURSE_STRUCTURE_PATH, "r", encoding="utf-8") as fh:
        structure = json.load(fh)

    try:
        names = predicted_names(structure, EXTRACT_DIR)
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}")
        return 1
    if not names:
        print("[FAIL] No names predicted. Is edx_export/ populated?")
        return 1

    audio = sum(1 for _, t, _ in names if t == "audio")
    print(f"[OK] organize_content.py would produce {len(names)} file(s): "
          f"{len(names) - audio} text, {audio} audio")

    if not os.path.exists(CURRENT_SOURCES_FILE):
        print(f"[WARN] {CURRENT_SOURCES_FILE} not found - cannot score against the")
        print("       notebook. Run export_current_sources.py to compare.")
        for name, _, _ in names[:20]:
            print(f"   {name}")
        return 0

    current = cs.load_current_sources()
    print(f"[OK] Scoring against {len(current)} existing notebook source(s)")
    print()

    replace, keep, unmatched = [], [], []
    for name, ftype, chapter in names:
        nf = {"name": name, "chapter": chapter, "type": ftype, "path": ""}
        best, best_src = 0.0, None
        for src in current:
            score = cs.compute_match_score(nf, src)
            if score > best:
                best, best_src = score, src
        if best >= cs.HIGH_CONFIDENCE:
            replace.append((best, name, best_src))
        elif best >= cs.MATCH_THRESHOLD:
            keep.append((best, name, best_src))
        else:
            unmatched.append((best, name, best_src))

    print(f"  REPLACE by default (score >= {cs.HIGH_CONFIDENCE}) : {len(replace):>4}")
    print(f"  KEEP by default    (>= {cs.MATCH_THRESHOLD}, needs promoting) : {len(keep):>4}")
    print(f"  no match           (-> ADD, uploads as new)  : {len(unmatched):>4}")
    print()

    if keep:
        print("  Would default to KEEP - promote to REPLACE if the match is right:")
        for score, name, src in sorted(keep, reverse=True)[:12]:
            print(f"    {score:.3f}  {name[:46]}")
            print(f"           vs  {(src or '')[:60]}")
    if unmatched:
        print()
        print("  No match - these upload as NEW sources (duplicates if the notebook")
        print("  already holds an equivalent under a different name):")
        for score, name, src in sorted(unmatched, reverse=True)[:12]:
            print(f"    {score:.3f}  {name[:46]}")
            print(f"     closest  {(src or '-')[:60]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
