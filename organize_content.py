import os
import sys
import time
import xml.etree.ElementTree as ET
import json
import shutil
import re
import subprocess
import urllib.request
import html

from config import (COURSE_STRUCTURE_PATH, DOWNLOAD_RETRIES, DOWNLOAD_TIMEOUT,
                    EXTRACT_DIR, MANIFEST_PATH, ORGANIZED_CONTENT_DIR)
from extract_edx import find_course_root


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


def clean_html(html_content):
    # Strip HTML tags and normalize whitespace
    text = re.sub('<[^>]*>', ' ', html_content)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def organize_course(extract_dir, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Load structure generated by extract_edx.py
    struct_path = COURSE_STRUCTURE_PATH
    if not os.path.exists(struct_path):
        print(f"Error: {struct_path} not found. Run extract_edx.py first.")
        sys.exit(1)

    with open(struct_path, "r", encoding="utf-8") as f:
        structure = json.load(f)

    # The archive may or may not wrap everything in a course/ directory.
    try:
        course_root = find_course_root(extract_dir)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    manifest = []
    video_failures = []

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
                        html_path = os.path.join(course_root, "html", f"{comp['url_name']}.html")
                        if os.path.exists(html_path):
                            try:
                                with open(html_path, "r", encoding="utf-8") as hf:
                                    merged_text.append(clean_html(hf.read()))
                            except Exception as e:
                                print(f"Error reading HTML {comp['url_name']}: {e}")
                    
                    # 2. Process Video Content (Download & Convert)
                    elif comp["type"] == "video":
                        video_xml_path = os.path.join(course_root, "video", f"{comp['url_name']}.xml")
                        if os.path.exists(video_xml_path):
                            try:
                                tree = ET.parse(video_xml_path)
                                root = tree.getroot()
                                
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

                                    # Already converted on an earlier run: reuse it.
                                    # Without this, a run interrupted at video 15 of 17
                                    # starts again from the first one.
                                    if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
                                        print(f"   [skip] {mp3_filename} already built")
                                        chapter_manifest["files"].append(
                                            {"name": mp3_filename, "path": mp3_path, "type": "audio"})
                                        break

                                    temp_mp4 = os.path.join(ch_dir, f".{clean_vid_name}.download.mp4")
                                    try:
                                        print(f"   [get]  {video_title[:56]}", flush=True)
                                        written = download_with_retry(vid_url, temp_mp4)
                                        print(f"   [conv] {mp3_filename} ({human_size(written)})",
                                              flush=True)
                                        extract_audio(temp_mp4, mp3_path)
                                        chapter_manifest["files"].append(
                                            {"name": mp3_filename, "path": mp3_path, "type": "audio"})
                                    except Exception as exc:
                                        print(f"   [FAIL] {video_title[:46]}: {exc}")
                                        video_failures.append((video_title, str(exc)))
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
    static_src = os.path.join(course_root, "static")
    if os.path.exists(static_src):
        os.makedirs(static_output, exist_ok=True)
        for f in os.listdir(static_src):
            src_f = os.path.join(static_src, f)
            if os.path.isfile(src_f):
                ext = os.path.splitext(f)[1].lower()
                if ext in [".pdf", ".docx", ".xlsx", ".txt"]:
                    shutil.copy2(src_f, os.path.join(static_output, f))

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        # Save relative paths for subagent compatibility
        json.dump(manifest, f, indent=4)

    total_files = sum(len(c["files"]) for c in manifest)
    if video_failures:
        print(f"\n[WARN] {len(video_failures)} video(s) failed:")
        for title, reason in video_failures:
            print(f"   - {title[:48]}: {reason[:90]}")
        print("   Re-run to retry only these; completed files are skipped.")
    print(f"[OK] Organization complete: {total_files} file(s) in {MANIFEST_PATH}")
    return 1 if video_failures else 0

if __name__ == "__main__":
    raise SystemExit(organize_course(EXTRACT_DIR, ORGANIZED_CONTENT_DIR))