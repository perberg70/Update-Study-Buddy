"""
Build ``Organized_Course_Content`` from an edX extract.

**Chapter text (one file per chapter):** All HTML components in a chapter are merged into a single
``NN_ChapterName.txt`` — that is the only “chapter body” text file for that chapter.

**URLs (separate from merged prose):** Each link is written to ``url_sources/*.txt`` for NotebookLM,
then **removed** from the merged ``NN_ChapterName.txt`` so the chapter file stays merged **text** only.
Use ``--keep-urls-in-merged-chapter`` to duplicate URLs in both places.

**Video:** Each edX video becomes one ``.mp3`` in the chapter folder, separate from the merged .txt.
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from config import (
    EDX_COMPONENT_DIR_NAMES,
    EXTRACT_DIR,
    ORGANIZED_CONTENT_DIR,
    SCRIPT_DIR,
    chapter_dir_slug,
    expected_chapter_dir_names,
    manifest_relpath,
)


def _flatten_inner_html(fragment: str) -> str:
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    return re.sub(r"\s+", " ", fragment).strip()


def _html_anchors_to_plaintext(s: str) -> str:
    """
    edX HTML uses <a href="https://...">link text</a>. Stripping tags without this step
    drops every URL (e.g. numbered tutorials in Toolbox chapters).
    """
    pattern = re.compile(
        r'<a\b[^>]*\bhref\s*=\s*(["\'])([^"\']*)\1[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    def repl(m):
        href = html.unescape(m.group(2).strip())
        inner = _flatten_inner_html(m.group(3))
        if not href:
            return inner
        if not inner:
            return href
        if inner.lower() == href.lower():
            return href
        if href in inner or href.rstrip("/") in inner:
            return inner
        return f"{inner} {href}"

    return pattern.sub(repl, s)


def _url_from_tag_opening(tag: str) -> str:
    """First useful URL from src=, data=, or href= on an element opening tag."""
    for attr in ("src", "data", "href"):
        sm = re.search(rf"\b{attr}\s*=\s*(['\"])([^'\"]*)\1", tag, re.IGNORECASE)
        if sm:
            u = html.unescape(sm.group(2).strip())
            if u and not u.startswith("#"):
                return u
    return ""


def _html_embedded_media_to_plaintext(s: str) -> str:
    """
    Preserve URLs from embeds that would otherwise vanish when tags are stripped
    (iframes, embeds, objects). Used for all HTML blocks across every chapter.
    """

    def repl_iframe(m):
        u = _url_from_tag_opening(m.group(0))
        return f" {u} " if u else " "

    # Self-closing iframe first
    s = re.sub(r"<iframe\b[^>]*/>", repl_iframe, s, flags=re.IGNORECASE)
    # Block iframes
    s = re.sub(r"<iframe\b[^>]*>.*?</iframe>", repl_iframe, s, flags=re.IGNORECASE | re.DOTALL)

    # <embed ... />
    s = re.sub(r"<embed\b[^>]*/>", repl_iframe, s, flags=re.IGNORECASE)

    # <object data="...">...</object>
    s = re.sub(r"<object\b[^>]*>.*?</object>", repl_iframe, s, flags=re.IGNORECASE | re.DOTALL)

    return s


def _html_http_images_to_plaintext(s: str) -> str:
    """Keep absolute image URLs (figures hosted off-site); skip /static/ paths."""

    def repl(m):
        return f" [image: {m.group(2)}] "

    return re.sub(
        r'<img\b[^>]*\bsrc\s*=\s*(["\'])(https?://[^"\']+)\1[^>]*/?>',
        repl,
        s,
        flags=re.IGNORECASE,
    )


def clean_html(html_content):
    # Run on every edX HTML component so links/embeds survive tag stripping (all chapters).
    html_content = _html_anchors_to_plaintext(html_content)
    html_content = _html_embedded_media_to_plaintext(html_content)
    html_content = _html_http_images_to_plaintext(html_content)
    text = re.sub("<[^>]*>", " ", html_content)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _component_text_from_file(path: str, *, as_html: bool) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except OSError:
        return ""
    if as_html:
        return clean_html(raw)
    if raw.strip().startswith("<"):
        try:
            root = ET.fromstring(raw)
            return re.sub(r"\s+", " ", html.unescape(" ".join(root.itertext()))).strip()
        except ET.ParseError:
            return clean_html(raw)
    return clean_html(raw)


def _load_component_text(extract_dir: str, comp_type: str, url_name: str) -> str:
    """
    Resolve and normalize text-bearing edX components to chapter prose.
    """
    ctype = (comp_type or "").strip().lower()
    if not url_name:
        return ""

    if ctype == "html":
        html_path = os.path.join(extract_dir, "course", "html", f"{url_name}.html")
        return _component_text_from_file(html_path, as_html=True) if os.path.exists(html_path) else ""

    candidate_exts = (".html", ".xml")
    for ext in candidate_exts:
        p = os.path.join(extract_dir, "course", ctype, f"{url_name}{ext}")
        if os.path.exists(p):
            return _component_text_from_file(p, as_html=(ext == ".html"))
    return ""


def _extract_video_mp3(video_xml_path: str, ch_dir: str, preferred_title: str) -> tuple[dict | None, list[str]]:
    """
    Extract one MP3 from a video component. Returns manifest entry or None, plus log lines.
    """
    logs: list[str] = []
    tree = ET.parse(video_xml_path)
    root = tree.getroot()

    video_title = html.unescape(root.get("display_name", preferred_title))
    if preferred_title and len(preferred_title) > len(video_title):
        video_title = preferred_title

    video_asset = root.find(".//video_asset")
    if video_asset is not None and video_asset.get("client_video_id"):
        client_id = video_asset.get("client_video_id", "").strip()
        if client_id:
            orig_name = re.sub(r"\.[^.]+$", "", client_id)
            generic = video_title.strip().lower() in ("video", "recording", "teaser") or (
                len(video_title.split()) <= 3 and "recording" in video_title.lower()
            )
            if generic or len(orig_name) > len(video_title):
                video_title = orig_name

    clean_vid_name = re.sub(r"[^a-zA-Z0-9]", "_", video_title)
    clean_vid_name = re.sub(r"_+", "_", clean_vid_name).strip("_") or "video_audio"
    mp3_filename = f"{clean_vid_name}.mp3"
    mp3_path = os.path.join(ch_dir, mp3_filename)

    urls = [u for u in (asset.get("url") for asset in root.findall(".//video_asset/encoded_video")) if u]
    if not urls:
        logs.append(f"[WARN] No encoded_video URL found in {os.path.basename(video_xml_path)}")
        return None, logs

    # Prefer obvious mp4 URLs but try every candidate.
    urls = sorted(urls, key=lambda u: ".mp4" not in (u.lower()))
    for idx, vid_url in enumerate(urls, start=1):
        temp_mp4 = os.path.join(ch_dir, f"temp_download_{idx}.bin")
        try:
            logs.append(f"[INFO] Downloading video source {idx}/{len(urls)} for '{video_title}'")
            req = urllib.request.Request(vid_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req) as response, open(temp_mp4, "wb") as out_file:
                shutil.copyfileobj(response, out_file)

            logs.append(f"[INFO] Converting to MP3: {mp3_filename}")
            subprocess.run(
                ["ffmpeg", "-y", "-i", temp_mp4, "-q:a", "0", "-map", "a", mp3_path],
                capture_output=True,
                check=True,
            )
            return {"name": mp3_filename, "path": mp3_path, "type": "audio"}, logs
        except Exception as e:
            logs.append(f"[WARN] Video source failed ({vid_url}): {e}")
        finally:
            try:
                if os.path.exists(temp_mp4):
                    os.remove(temp_mp4)
            except OSError:
                pass

    logs.append(f"[WARN] Could not convert any encoded source to MP3 for '{video_title}'")
    return None, logs


def _validate_manifest_contract(manifest: list[dict]) -> None:
    """
    Contract: at most one non-url-split text row per chapter.
    """
    for ch in manifest:
        files = ch.get("files") or []
        merged_text_rows = [
            x for x in files
            if x.get("type") == "text"
            and str(x.get("path", "")).lower().endswith(".txt")
            and "url_sources" not in str(x.get("path", "")).replace("\\", "/").lower()
            and "toolbox_sources" not in str(x.get("path", "")).replace("\\", "/").lower()
        ]
        if len(merged_text_rows) > 1:
            raise RuntimeError(
                f"Chapter '{ch.get('chapter')}' violates contract: found {len(merged_text_rows)} merged text files."
            )


def _append_unique_file(chapter_manifest: dict, row: dict) -> None:
    files = chapter_manifest.setdefault("files", [])
    key = (row.get("name"), row.get("path"))
    for x in files:
        if (x.get("name"), x.get("path")) == key:
            return
    files.append(row)


def _manifest_file_row(name: str, path: str, file_type: str) -> dict:
    return {"name": name, "path": manifest_relpath(path), "type": file_type}


def _archive_stale_dir(src: Path, archive_root: Path) -> None:
    if not src.is_dir():
        return
    archive_root.mkdir(parents=True, exist_ok=True)
    dest = archive_root / src.name
    if dest.exists():
        suffix = src.stat().st_mtime_ns
        dest = archive_root / f"{src.name}_{suffix}"
    shutil.move(str(src), str(dest))
    try:
        rel = dest.relative_to(SCRIPT_DIR)
    except ValueError:
        rel = dest
    print(f"[ARCHIVE] Moved stale folder {src.name} → {rel}")


def prune_stale_organized_dirs(output_dir: str | Path, chapters: list[dict]) -> None:
    """Move legacy chapter folders and stray edX dirs out of Organized_Course_Content."""
    out = Path(output_dir)
    if not out.is_absolute():
        out = SCRIPT_DIR / out
    if not out.is_dir():
        return

    expected = expected_chapter_dir_names(chapters)
    keep_names = expected | {"Global_Assets"}
    archive_root = SCRIPT_DIR / "Archive" / "pruned_organized_content"
    chapter_pat = re.compile(r"^\d{2}_")

    for child in sorted(out.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name in keep_names:
            continue
        if chapter_pat.match(name) or name in EDX_COMPONENT_DIR_NAMES:
            _archive_stale_dir(child, archive_root)


def organize_course(
    extract_dir,
    output_dir,
    *,
    skip_url_extract: bool = False,
    keep_urls_in_merged_chapter: bool = False,
):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Load structure generated by extract_edx.py
    struct_path = "course_structure.json"
    if not os.path.exists(struct_path):
        print(f"Error: {struct_path} not found. Run extract_edx.py first.")
        sys.exit(1)

    with open(struct_path, "r", encoding="utf-8") as f:
        structure = json.load(f)

    prune_stale_organized_dirs(output_dir, structure["chapters"])

    manifest = []
    media_logs: list[str] = []

    for i, chapter in enumerate(structure["chapters"]):
        ch_name = chapter_dir_slug(i + 1, chapter["title"])
        ch_dir = os.path.join(output_dir, ch_name)
        os.makedirs(ch_dir, exist_ok=True)
        
        chapter_manifest = {"chapter": chapter['title'], "files": []}
        merged_text = []

        for seq in chapter["sequentials"]:
            for vert in seq["verticals"]:
                for comp in vert["components"]:
                    # 1) Process text-bearing content into one chapter-merged text stream
                    if comp["type"] in {"html", "problem", "discussion", "openassessment", "survey", "poll"}:
                        try:
                            tx = _load_component_text(extract_dir, comp["type"], comp.get("url_name", ""))
                            if tx:
                                merged_text.append(tx)
                        except Exception as e:
                            print(f"Error reading text component {comp.get('url_name')}: {e}")
                    
                    # 2. Process Video Content (Download & Convert)
                    elif comp["type"] == "video":
                        video_xml_path = os.path.join(extract_dir, "course", "video", f"{comp['url_name']}.xml")
                        if os.path.exists(video_xml_path):
                            try:
                                vert_title = html.unescape((vert.get("title") or "").strip())
                                mp3_entry, logs = _extract_video_mp3(video_xml_path, ch_dir, vert_title or comp["url_name"])
                                media_logs.extend(logs)
                                if mp3_entry:
                                    _append_unique_file(
                                        chapter_manifest,
                                        _manifest_file_row(
                                            mp3_entry["name"],
                                            mp3_entry["path"],
                                            mp3_entry["type"],
                                        ),
                                    )
                            except Exception as e:
                                print(f"Error processing video {comp['url_name']}: {e}")

        # Save merged text content for NoteBookLM
        if merged_text:
            merged_filename = f"{ch_name}.txt"
            merged_path = os.path.join(ch_dir, merged_filename)
            with open(merged_path, "w", encoding="utf-8") as mf:
                mf.write("\n\n---\n\n".join(merged_text))
            _append_unique_file(
                chapter_manifest,
                _manifest_file_row(merged_filename, merged_path, "text"),
            )

        manifest.append(chapter_manifest)

    # Process Global Assets (Existing documents/audio)
    static_output = os.path.join(output_dir, "Global_Assets")
    static_src = os.path.join(extract_dir, "course", "static")
    if os.path.exists(static_src):
        os.makedirs(static_output, exist_ok=True)
        for f in os.listdir(static_src):
            src_f = os.path.join(static_src, f)
            if os.path.isfile(src_f):
                ext = os.path.splitext(f)[1].lower()
                if ext in [".pdf", ".docx", ".xlsx", ".txt"]:
                    shutil.copy2(src_f, os.path.join(static_output, f))

    with open("processing_manifest.json", "w", encoding="utf-8") as f:
        # Save relative paths for subagent compatibility
        json.dump(manifest, f, indent=4)
    _validate_manifest_contract(manifest)
    print("[OK] Organization and media processing complete.")

    if media_logs:
        report_path = Path("media_conversion_report.txt")
        report_path.write_text("\n".join(media_logs), encoding="utf-8")
        print(f"[OK] Wrote media conversion report: {report_path}")

    # URLs (distinct from chapter text): one url_sources/*.txt per link + manifest row for Websites/link upload.
    if not skip_url_extract:
        from split_urls_from_organized import extract_urls_for_notebook

        out_root = Path(output_dir)
        if not out_root.is_absolute():
            out_root = SCRIPT_DIR / out_root
        extract_urls_for_notebook(
            out_root.resolve(),
            update_manifest=True,
            also_sections=False,
            strip_urls_from_merged=not keep_urls_in_merged_chapter,
        )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Organized_Course_Content from extracted edX tree")
    parser.add_argument(
        "--extract-dir",
        default=EXTRACT_DIR,
        help=f"Directory produced by extract_edx.py (default: {EXTRACT_DIR!r})",
    )
    parser.add_argument(
        "--output-dir",
        default=ORGANIZED_CONTENT_DIR,
        help=f"Output folder for chapter content (default: {ORGANIZED_CONTENT_DIR!r})",
    )
    parser.add_argument(
        "--skip-url-extract",
        action="store_true",
        help="Do not create url_sources/*.txt or strip merged chapter files",
    )
    parser.add_argument(
        "--keep-urls-in-merged-chapter",
        action="store_true",
        help="After extraction, keep URL strings in merged NN_Chapter.txt (default: remove them; links only in url_sources/)",
    )
    args = parser.parse_args()
    organize_course(
        args.extract_dir,
        args.output_dir,
        skip_url_extract=args.skip_url_extract,
        keep_urls_in_merged_chapter=args.keep_urls_in_merged_chapter,
    )