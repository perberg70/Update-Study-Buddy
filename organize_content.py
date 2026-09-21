"""Turn a parsed course into per-chapter text and audio for the notebook.

Content is read straight out of the course archive. Nothing is unpacked to a
shared directory first, so a run cannot pick up leftovers from a previous
export - see olx_archive.py.

Usage:
    python organize_content.py
    python organize_content.py --tar course.hp_m6v88.tar.gz
"""

import argparse
import os
import sys
import time
import xml.etree.ElementTree as ET
import json
import re
import subprocess
import urllib.request
import html

from config import (COURSE_STRUCTURE_PATH, DOWNLOAD_RETRIES, DOWNLOAD_TIMEOUT,
                    MANIFEST_PATH, ORGANIZED_CONTENT_DIR)
from olx_archive import CourseArchiveError, open_for_structure


def slugify(text):
    """Collapse anything non-alphanumeric to single underscores."""
    slug = re.sub(r"[^a-zA-Z0-9]", "_", text)
    return re.sub(r"_+", "_", slug).strip("_")


def chapter_dir_name(index, chapter_title):
    """Folder/text-file stem for a chapter, e.g. '01_Welcome_What_GenAI_Can_Do_Today'.

    *index* is zero-based. Leading "1. " numbering in the title is stripped so the
    result is '01_Welcome...' rather than '01_1_Welcome...'.
    """
    title_for_slug = re.sub(r"^\s*\d+\.?\s*", "", (chapter_title or "").strip())
    return f"{index + 1:02d}_{slugify(title_for_slug)}"


def video_output_name(video_root, vertical_title="", fallback=""):
    """Filename stem for a video's extracted audio, without the .mp3 extension.

    Prefers the longest of: the video's display_name, its vertical's title, and the
    original uploaded filename (client_video_id), on the assumption that the longest
    is the most descriptive. A generic display_name is always overridden.
    """
    video_title = html.unescape(video_root.get("display_name", fallback) or fallback)
    vert_title = html.unescape((vertical_title or "").strip())
    if vert_title and len(vert_title) > len(video_title):
        video_title = vert_title

    video_asset = video_root.find(".//video_asset")
    if video_asset is not None and video_asset.get("client_video_id"):
        client_id = video_asset.get("client_video_id", "").strip()
        if client_id:
            orig_name = re.sub(r"\.[^.]+$", "", client_id)
            generic = video_title.strip().lower() in ("video", "recording", "teaser") or (
                len(video_title.split()) <= 3 and "recording" in video_title.lower()
            )
            if generic or len(orig_name) > len(video_title):
                video_title = orig_name

    return slugify(video_title)


def human_size(num_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.0f}{unit}" if unit == "B" else f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024.0


def download_with_retry(url, dest, timeout=DOWNLOAD_TIMEOUT, retries=DOWNLOAD_RETRIES):
    """Download *url* to *dest*, retrying with backoff. Returns bytes written.

    Writes to a .part file and renames on success, so an interrupted download
    can never be mistaken for a complete one. The timeout matters most here:
    without it a stalled CDN response hangs an unattended run indefinitely with
    no output at all.
    """
    # A User-Agent is required; the CDN returns 403 to the urllib default.
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    partial = dest + ".part"
    last_error = None

    for attempt in range(1, retries + 1):
        written = 0
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                total = int(response.headers.get("Content-Length") or 0)
                if total:
                    print(f"      {human_size(total)} to fetch...", flush=True)
                with open(partial, "wb") as out:
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)
                        written += len(chunk)
            if total and written < total:
                raise IOError(f"truncated: got {written} of {total} bytes")
            os.replace(partial, dest)
            return written
        except Exception as exc:
            last_error = exc
            if os.path.exists(partial):
                try:
                    os.remove(partial)
                except OSError:
                    pass
            if attempt < retries:
                delay = 2 ** attempt
                print(f"      attempt {attempt} of {retries} failed "
                      f"({type(exc).__name__}: {str(exc)[:70]}); retrying in {delay}s",
                      flush=True)
                time.sleep(delay)

    raise IOError(f"download failed after {retries} attempt(s): {last_error}")


def extract_audio(source, dest):
    """Convert *source* to mp3 with ffmpeg, surfacing ffmpeg's own error text."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", source, "-q:a", "0", "-map", "a", dest],
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", "replace").strip()
        tail = "\n        ".join(stderr.splitlines()[-4:]) or "(no output)"
        raise RuntimeError(f"ffmpeg exited {result.returncode}:\n        {tail}")


def unique_name(filename, taken):
    """Avoid silently overwriting when two videos slugify to the same name.

    Only collisions *within this run* are disambiguated. An existing file on
    disk is a completed download to be reused, not a clash.
    """
    stem, ext = os.path.splitext(filename)
    candidate, n = filename, 2
    while candidate.lower() in taken:
        candidate = f"{stem}_{n}{ext}"
        n += 1
    taken.add(candidate.lower())
    return candidate


# Which export and which source URL produced each mp3. Without it, the
# skip-if-exists below reuses an mp3 whenever the filename matches - and the
# filename comes from the video's title, which commonly survives a re-record.
# A second update would then ship last year's audio beside this year's text.
AUDIO_INDEX_NAME = ".audio_sources.json"


def load_audio_index(output_dir):
    try:
        with open(os.path.join(output_dir, AUDIO_INDEX_NAME), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_audio_index(output_dir, index):
    try:
        with open(os.path.join(output_dir, AUDIO_INDEX_NAME), "w", encoding="utf-8") as fh:
            json.dump(index, fh, indent=2, sort_keys=True)
    except OSError as exc:
        print(f"[WARN] Could not write {AUDIO_INDEX_NAME}: {exc}")


def record_audio(output_dir, index, key, url, archive_sha):
    """Note that *key* was built from this url and export, and save immediately.

    Written per video rather than once at the end. The index exists so an
    interrupted run resumes, and an index held only in memory until the loop
    finishes records nothing at all when the run is interrupted - which is the
    only time resumability matters.
    """
    index[key] = {"url": url, "archive": archive_sha}
    save_audio_index(output_dir, index)


def forget_audio(output_dir, index, key):
    """Drop an entry and save, so a failed rebuild leaves no stale claim."""
    if index.pop(key, None) is not None:
        save_audio_index(output_dir, index)


def audio_is_current(index, key, url, archive_sha):
    """True when the mp3 on disk was built from this URL and this export."""
    entry = index.get(key)
    if not isinstance(entry, dict):
        return False
    return entry.get("url") == url and entry.get("archive") == archive_sha


def clean_html(html_content):
    # Strip HTML tags and normalize whitespace
    text = re.sub('<[^>]*>', ' ', html_content)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def organize_course(archive, output_dir, structure=None):
    """Build per-chapter text and audio. *archive* supplies both and is recorded."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Normally handed in by main(), already checked against the archive. Loaded
    # here only for callers that pass none; re-reading it in the checked path
    # would defeat the check.
    if structure is None:
        if not os.path.exists(COURSE_STRUCTURE_PATH):
            print(f"Error: {COURSE_STRUCTURE_PATH} not found. Run extract_edx.py first.")
            sys.exit(1)
        with open(COURSE_STRUCTURE_PATH, "r", encoding="utf-8") as f:
            structure = json.load(f)

    archive_sha = archive.fingerprint()["sha256"]
    audio_index = load_audio_index(output_dir)

    manifest = []
    video_failures = []
    reused = rebuilt = 0

    for i, chapter in enumerate(structure["chapters"]):
        # Create a clean folder name for the chapter (match old scheme: 01_Welcome___What_..., not 01_1__Welcome_...)
        ch_name = chapter_dir_name(i, chapter["title"])
        ch_dir = os.path.join(output_dir, ch_name)
        os.makedirs(ch_dir, exist_ok=True)
        
        chapter_manifest = {"chapter": chapter['title'], "files": []}
        merged_text = []
        used_names = set()

        for seq in chapter["sequentials"]:
            for vert in seq["verticals"]:
                for comp in vert["components"]:
                    # 1. Process HTML Content
                    if comp["type"] == "html":
                        raw = archive.read_text(f"html/{comp['url_name']}.html")
                        if raw is not None:
                            merged_text.append(clean_html(raw))
                    
                    # 2. Process Video Content (Download & Convert)
                    elif comp["type"] == "video":
                        video_xml = archive.read_text(f"video/{comp['url_name']}.xml")
                        if video_xml is not None:
                            try:
                                root = ET.fromstring(video_xml)

                                video_title = root.get("display_name", comp["url_name"])
                                clean_vid_name = video_output_name(
                                    root, vert.get("title", ""), comp["url_name"]
                                )

                                # Find direct MP4 link in edX metadata
                                for asset in root.findall(".//video_asset/encoded_video"):
                                    vid_url = asset.get('url')
                                    if not (vid_url and vid_url.endswith('.mp4')):
                                        continue

                                    mp3_filename = unique_name(f"{clean_vid_name}.mp3", used_names)
                                    mp3_path = os.path.join(ch_dir, mp3_filename)

                                    # Reuse an mp3 from an earlier run, so a run
                                    # interrupted at video 15 of 17 does not start
                                    # again from the first. Only when it was built
                                    # from this same URL and this same export: the
                                    # filename comes from the video title, which a
                                    # re-recorded video commonly keeps.
                                    index_key = os.path.relpath(mp3_path, output_dir).replace(os.sep, "/")
                                    on_disk = os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0
                                    if on_disk and audio_is_current(audio_index, index_key,
                                                                    vid_url, archive_sha):
                                        reused += 1
                                        print(f"   [skip] {mp3_filename} already built "
                                              "from this export")
                                        chapter_manifest["files"].append(
                                            {"name": mp3_filename, "path": mp3_path, "type": "audio"})
                                        break
                                    if on_disk:
                                        rebuilt += 1
                                        print(f"   [stale] {mp3_filename} came from a "
                                              "different export or URL; rebuilding")

                                    temp_mp4 = os.path.join(ch_dir, f".{clean_vid_name}.download.mp4")
                                    try:
                                        print(f"   [get]  {video_title[:56]}", flush=True)
                                        written = download_with_retry(vid_url, temp_mp4)
                                        print(f"   [conv] {mp3_filename} ({human_size(written)})",
                                              flush=True)
                                        extract_audio(temp_mp4, mp3_path)
                                        record_audio(output_dir, audio_index,
                                                     index_key, vid_url, archive_sha)
                                        chapter_manifest["files"].append(
                                            {"name": mp3_filename, "path": mp3_path, "type": "audio"})
                                    except Exception as exc:
                                        print(f"   [FAIL] {video_title[:46]}: {exc}")
                                        video_failures.append((video_title, str(exc)))
                                        forget_audio(output_dir, audio_index, index_key)
                                        if os.path.exists(mp3_path):
                                            # A partial mp3 would be reused as complete
                                            # by the skip check above.
                                            try:
                                                os.remove(mp3_path)
                                            except OSError:
                                                pass
                                    finally:
                                        for leftover in (temp_mp4, temp_mp4 + ".part"):
                                            if os.path.exists(leftover):
                                                try:
                                                    os.remove(leftover)
                                                except OSError:
                                                    pass
                                    break
                            except Exception as e:
                                print(f"Error processing video {comp['url_name']}: {e}")
                                video_failures.append((comp.get("url_name", "?"), str(e)))

        # Save merged text content for NoteBookLM
        if merged_text:
            merged_filename = f"{ch_name}.txt"
            merged_path = os.path.join(ch_dir, merged_filename)
            with open(merged_path, "w", encoding="utf-8") as mf:
                mf.write("\n\n---\n\n".join(merged_text))
            chapter_manifest["files"].append({"name": merged_filename, "path": merged_path, "type": "text"})

        manifest.append(chapter_manifest)

    # Process Global Assets (Existing documents/audio)
    static_output = os.path.join(output_dir, "Global_Assets")
    assets = [f for f in archive.listdir("static")
              if os.path.splitext(f)[1].lower() in (".pdf", ".docx", ".xlsx", ".txt")]
    if assets:
        os.makedirs(static_output, exist_ok=True)
        for name in assets:
            archive.extract_to(f"static/{name}", os.path.join(static_output, name))

    # Entries are already saved as each mp3 is built; this final write is what
    # records removals, when a video has gone from the course.
    save_audio_index(output_dir, audio_index)

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        # Save relative paths for subagent compatibility
        json.dump(manifest, f, indent=4)

    total_files = sum(len(c["files"]) for c in manifest)
    if reused or rebuilt:
        print(f"[OK] Audio: {reused} reused from this export, "
              f"{rebuilt} rebuilt because they came from another.")
    if video_failures:
        print(f"\n[WARN] {len(video_failures)} video(s) failed:")
        for title, reason in video_failures:
            print(f"   - {title[:48]}: {reason[:90]}")
        print("   Re-run to retry only these; completed files are skipped.")
    print(f"[OK] Organization complete: {total_files} file(s) in {MANIFEST_PATH}")
    return 1 if video_failures else 0

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tar", dest="tar_path",
                        help="course .tar.gz to read (default: the one "
                             "course_structure.json was built from)")
    parser.add_argument("--out-dir", default=ORGANIZED_CONTENT_DIR)
    parser.add_argument("--allow-stale", action="store_true",
                        help="run even when course_structure.json and the archive "
                             "disagree about which export they came from")
    args = parser.parse_args()

    if not os.path.exists(COURSE_STRUCTURE_PATH):
        print(f"Error: {COURSE_STRUCTURE_PATH} not found. Run extract_edx.py first.")
        return 1
    with open(COURSE_STRUCTURE_PATH, "r", encoding="utf-8") as fh:
        structure = json.load(fh)

    try:
        archive = open_for_structure(structure, args.tar_path, args.allow_stale)
    except (CourseArchiveError, FileNotFoundError) as exc:
        print(f"Error: {exc}")
        return 1

    print(f"[OK] Reading {os.path.basename(archive.tar_path)}")
    return organize_course(archive, args.out_dir, structure)


if __name__ == "__main__":
    raise SystemExit(main())