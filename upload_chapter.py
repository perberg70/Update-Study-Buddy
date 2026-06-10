#!/usr/bin/env python3
"""
Upload NotebookLM sources for **one course chapter** only.

Fast path for chapter 1 (default): merged chapter text + MP3s (~8 files), no link URLs.

Prerequisites:
  1. Chrome with remote debugging:
       & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222
  2. Log in, open your notebook, show the Sources panel.
  3. ``processing_manifest.json`` and ``Organized_Course_Content/`` exist (run organize_content.py once).

Usage:
  python upload_chapter.py              # Chapter 1: text + audio only
  python upload_chapter.py --dry-run    # List files without uploading
  python upload_chapter.py --include-links   # Also upload url_sources/ links (~41 for ch.1)
  python upload_chapter.py --chapter 2    # Another chapter
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from upload_agent import display_name_for_upload_row, load_manifest_items, resolve_project_path, run_upload

SCRIPT_DIR = Path(__file__).resolve().parent

CHAPTER_1_TITLE = "1. Welcome & What GenAI Can Do Today"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload sources for a single course chapter to NotebookLM.",
    )
    parser.add_argument(
        "--chapter",
        default="1",
        help='Course module number or folder hint (default: "1" = Welcome & What GenAI Can Do Today)',
    )
    parser.add_argument(
        "--include-links",
        action="store_true",
        help="Also upload url_sources/ website links (slow; dozens of clicks for chapter 1)",
    )
    parser.add_argument(
        "--text-only",
        action="store_true",
        help="Upload only the merged chapter .txt (no MP3s, no links)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the upload list and exit",
    )
    args = parser.parse_args()

    include_urls = args.include_links
    if args.text_only:
        types = {"text"}
        include_urls = False
    else:
        types = {"text", "audio"}
        if include_urls:
            types = None  # all types

    try:
        items = load_manifest_items(
            chapter=args.chapter,
            include_url_splits=include_urls,
            types=types,
        )
    except FileNotFoundError as e:
        print(f"[FAIL] {e}")
        sys.exit(1)

    if not items:
        print(f"[FAIL] No manifest rows matched chapter {args.chapter!r}.")
        print("       Run organize_content.py first, or try --chapter 1")
        sys.exit(1)

    chapter_label = items[0].get("chapter", args.chapter)
    print(f"Chapter: {chapter_label}")
    print(f"Files to upload: {len(items)}")
    for row in items:
        path = resolve_project_path(row.get("path", ""))
        exists = Path(path).is_file()
        tag = "OK" if exists else "MISSING"
        title = display_name_for_upload_row(row)
        print(f"  [{tag}] {row.get('type', '?'):5}  {title}")

    missing = [r for r in items if not Path(resolve_project_path(r.get("path", ""))).is_file()]
    if missing:
        print(f"\n[FAIL] {len(missing)} file(s) missing on disk. Run organize_content.py first.")
        sys.exit(1)

    if args.dry_run:
        print("\n[DRY RUN] No uploads performed.")
        return

    if not include_urls and not args.text_only:
        print("\n[INFO] Uploading merged chapter text + MP3s only.")
        print("       Add --include-links to upload url_sources/ as well.")

    print()
    run_upload(upload_items=items, include_url_splits=include_urls)


if __name__ == "__main__":
    main()
