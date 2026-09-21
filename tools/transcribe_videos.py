#!/usr/bin/env python3
"""Transcribe a module's short videos locally, into the transcript store.

Runs entirely on this machine. Audio is never sent anywhere: AGENTS.md forbids
disclosing course content to a third-party service without the course owner's
explicit authorisation, so a cloud speech-to-text API is not an option here
and none is offered.

Only short videos are considered. Webinar recordings run to an hour or more
and are transcribed from their Teams export instead - drop those VTT files
straight into the transcript store. `--max-minutes` sets the line.

Skipped automatically: videos that already have a transcript (from the OLX,
from YouTube captions, or dropped in by hand), and YouTube-hosted videos,
which have no .mp4 to transcribe - use tools/fetch_youtube_transcripts.py for
those.

Results land as <url_name>.txt, which tools/build_module_pdf.py reads with no
further configuration.

Usage:
    python tools/transcribe_videos.py --module 1 --dry-run
    python tools/transcribe_videos.py --module 1
    python tools/transcribe_videos.py --module 1 --model medium --max-minutes 30
"""

from __future__ import annotations

import argparse
import html
import os
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import COURSE_STRUCTURE_PATH, TRANSCRIPTS_DIR  # noqa: E402
from olx_archive import CourseArchiveError, open_for_structure  # noqa: E402
from build_module_pdf import (group_modules, module_label,  # noqa: E402
                              record_transcript, stored_transcript,
                              transcript_candidates, transcript_is_stale)
from video_report import has_direct_mp4, parse_duration, youtube_id  # noqa: E402
from organize_content import download_with_retry, human_size  # noqa: E402

DEFAULT_MODEL = "small"
DEFAULT_MAX_MINUTES = 20.0

# Rough CPU speed relative to real time, used only to state an estimate up
# front. Wildly machine-dependent; it is a magnitude, not a promise.
SPEED_HINT = {"tiny": 0.08, "base": 0.12, "small": 0.25, "medium": 0.7, "large-v3": 1.4}


def direct_mp4(video_root):
    """First direct .mp4 URL on a <video>, or ''."""
    for asset in video_root.findall(".//video_asset/encoded_video"):
        url = asset.get("url") or ""
        if url.endswith(".mp4"):
            return url
    return ""


def plan(modules, archive, transcripts_dir, max_minutes, force=False,
         archive_sha=""):
    """One row per video, each with a decision and the reason for it."""
    rows = []
    for module in modules:
        for chapter in module["chapters"]:
            if chapter.get("hidden"):
                continue
            for seq in chapter.get("sequentials", []):
                if seq.get("hidden"):
                    continue
                for vert in seq.get("verticals", []):
                    if vert.get("hidden"):
                        continue
                    for comp in vert.get("components", []):
                        if comp.get("type") != "video":
                            continue
                        rows.append(_classify(comp, vert, archive, transcripts_dir,
                                              max_minutes, force, archive_sha))
    return rows


def _classify(comp, vert, archive, transcripts_dir, max_minutes, force,
              archive_sha=""):
    url_name = comp.get("url_name")
    row = {"url_name": url_name, "title": url_name, "duration": 0.0,
           "url": "", "do": False, "why": ""}

    xml = archive.read_text(f"video/{url_name}.xml")
    if xml is None:
        row["why"] = "no video xml in the archive"
        return row
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        row["why"] = "video xml is malformed"
        return row

    row["title"] = html.unescape(
        root.get("display_name") or vert.get("title") or url_name)
    row["duration"] = parse_duration(root)

    existing = ""
    if not force:
        for candidate in transcript_candidates(root, archive):
            existing = existing or f"transcript in the export ({candidate})"
        if not existing and stored_transcript(url_name, row["title"], transcripts_dir):
            # Only counts as done while it still matches this video and export.
            if transcript_is_stale(transcripts_dir, url_name,
                                   direct_mp4(root), archive_sha):
                existing = ""
                row["why"] = "existing transcript is from another video or export"
            else:
                existing = f"transcript already in {transcripts_dir}/"
    if existing:
        row["why"] = existing
        return row

    if not has_direct_mp4(root):
        row["why"] = ("YouTube-hosted - run fetch_youtube_transcripts.py"
                      if youtube_id(root) else "no downloadable .mp4")
        return row

    if row["duration"] and row["duration"] > max_minutes * 60:
        row["why"] = (f"{row['duration'] / 60:.0f} min, over the "
                      f"{max_minutes:.0f} min limit - use the Teams export")
        return row
    if not row["duration"]:
        row["why"] = "no duration in the xml - length unknown, transcribing anyway"

    row["url"] = direct_mp4(root)
    row["do"] = True
    return row


OMP_HINT = (
    "If this still aborts with OMP: Error #15, set the variable yourself before\n"
    "  running:  set KMP_DUPLICATE_LIB_OK=TRUE")


def load_transcriber(model_name):
    """A local transcribe(path) -> text, from whichever backend is installed."""
    # ctranslate2 (faster-whisper's backend) and numpy/MKL each ship their own
    # Intel OpenMP runtime. Loading both aborts the process on Windows with
    # "OMP: Error #15" - an abort, not an exception, so there is nothing to
    # catch and nothing useful in the output. Setting this before the import
    # is the documented workaround.
    #
    # setdefault, not assignment: a value the operator set deliberately wins.
    # Announced rather than silent, because this suppresses a real
    # duplicate-runtime condition rather than fixing it.
    if "KMP_DUPLICATE_LIB_OK" not in os.environ:
        os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        print("[INFO] KMP_DUPLICATE_LIB_OK=TRUE set for this run (two OpenMP "
              "runtimes would\n       otherwise abort the process on Windows).")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        pass
    except Exception as exc:
        raise RuntimeError(f"faster-whisper failed to load: {exc}\n  {OMP_HINT}")
    else:
        try:
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
        except Exception as exc:
            raise RuntimeError(
                f"faster-whisper could not load the {model_name!r} model: {exc}\n"
                f"  A model name must be one of tiny/base/small/medium/large-v3.\n"
                f"  The first run downloads it, so this can also be a network failure.\n"
                f"  {OMP_HINT}")

        def transcribe(path):
            segments, _info = model.transcribe(path, vad_filter=True)
            return " ".join(s.text.strip() for s in segments if s.text.strip())

        return transcribe, f"faster-whisper ({model_name}, cpu/int8)"

    try:
        import whisper
    except ImportError:
        raise RuntimeError(
            "No local speech-to-text backend installed.\n"
            "  pip install faster-whisper     (recommended: far quicker on CPU)\n"
            "  pip install openai-whisper     (alternative; pulls in torch)\n"
            "Both run locally - no audio leaves this machine.")

    model = whisper.load_model(model_name)

    def transcribe(path):
        return (model.transcribe(path).get("text") or "").strip()

    return transcribe, f"openai-whisper ({model_name})"


def to_audio(source, dest):
    """16 kHz mono wav - what the models want, and far smaller than the mp4."""
    import subprocess
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", source, "-vn", "-ac", "1", "-ar", "16000", dest],
        capture_output=True, check=False)
    if result.returncode != 0:
        tail = "\n        ".join(
            (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-4:])
        raise RuntimeError(f"ffmpeg exited {result.returncode}:\n        {tail}")


def hhmm(seconds):
    seconds = int(seconds or 0)
    return (f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m" if seconds >= 3600
            else f"{seconds // 60}m{seconds % 60:02d}s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--module", help="module number (default: every module)")
    parser.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                        help=f"longest video to transcribe (default {DEFAULT_MAX_MINUTES:.0f})")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"whisper model size (default {DEFAULT_MODEL})")
    parser.add_argument("--transcripts-dir", default=TRANSCRIPTS_DIR)
    parser.add_argument("--tar", dest="tar_path",
                        help="course .tar.gz to read (default: the one "
                             "course_structure.json was built from)")
    parser.add_argument("--allow-stale", action="store_true",
                        help="run even when course_structure.json and the archive "
                             "disagree about which export they came from")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the plan and an estimate; download nothing")
    parser.add_argument("--force", action="store_true",
                        help="re-transcribe even when a transcript exists")
    parser.add_argument("--keep-audio", action="store_true",
                        help="leave the extracted .wav files in place")
    args = parser.parse_args()

    import json
    if not os.path.exists(COURSE_STRUCTURE_PATH):
        print(f"[FAIL] {COURSE_STRUCTURE_PATH} not found. Run extract_edx.py first.")
        return 1
    with open(COURSE_STRUCTURE_PATH, "r", encoding="utf-8") as fh:
        structure = json.load(fh)

    modules = group_modules(structure.get("chapters", []))
    if args.module:
        modules = [m for m in modules if (m["number"] or "") == args.module.strip()]
        if not modules:
            print(f"[FAIL] No module {args.module!r}.")
            return 1

    try:
        archive = open_for_structure(structure, args.tar_path, args.allow_stale)
    except (CourseArchiveError, FileNotFoundError) as exc:
        print(f"[FAIL] {exc}")
        return 1

    archive_sha = archive.fingerprint()["sha256"]
    rows = plan(modules, archive, args.transcripts_dir, args.max_minutes,
                args.force, archive_sha)
    todo = [r for r in rows if r["do"]]
    skipped = [r for r in rows if not r["do"]]

    print(f"\n{module_label(modules[0]) if len(modules) == 1 else f'{len(modules)} modules'}")
    print(f"  {len(rows)} video(s): {len(todo)} to transcribe, {len(skipped)} skipped\n")

    for row in todo:
        length = hhmm(row["duration"]) if row["duration"] else "length unknown"
        print(f"  [do]   {length:>7}  {row['title'][:54]}")
        if not row["duration"]:
            print(f"  {'':14}  {row['why']}")
    for row in skipped:
        print(f"  [skip] {'':7}  {row['title'][:54]}")
        print(f"  {'':14}  {row['why']}")

    if not todo:
        print("\nNothing to transcribe.")
        return 0

    total = sum(r["duration"] for r in todo)
    if total:
        factor = SPEED_HINT.get(args.model, 0.3)
        print(f"\n  {hhmm(total)} of audio. On a typical CPU the {args.model} model "
              f"runs at roughly\n  {factor:.2f}x real time, so expect very roughly "
              f"{hhmm(total * factor)} - machine-dependent.")

    if args.dry_run:
        print("\nDry run: nothing downloaded, nothing transcribed.")
        return 0

    try:
        transcribe, backend = load_transcriber(args.model)
    except RuntimeError as exc:
        print(f"\n[FAIL] {exc}")
        return 1
    except Exception as exc:
        print(f"\n[FAIL] Could not load the {args.model!r} model: {exc}")
        return 1
    print(f"\n[OK] Local backend: {backend}")

    os.makedirs(args.transcripts_dir, exist_ok=True)
    work = os.path.join(args.transcripts_dir, ".work")
    os.makedirs(work, exist_ok=True)

    done = failed = 0
    for index, row in enumerate(todo, start=1):
        label = row["title"][:48]
        print(f"\n  ({index}/{len(todo)}) {label}")
        mp4 = os.path.join(work, f"{row['url_name']}.mp4")
        wav = os.path.join(work, f"{row['url_name']}.wav")
        out = os.path.join(args.transcripts_dir, f"{row['url_name']}.txt")
        started = time.time()
        try:
            print("    downloading...", flush=True)
            written = download_with_retry(row["url"], mp4)
            print(f"    converting {human_size(written)} to 16 kHz mono...", flush=True)
            to_audio(mp4, wav)
            print("    transcribing (this is the slow part)...", flush=True)
            text = transcribe(wav)
            if not text.strip():
                print("    [none] no speech recognised - nothing written")
                failed += 1
                continue
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(text.strip())
            record_transcript(args.transcripts_dir, row["url_name"], "whisper",
                              row["url"], archive_sha)
            done += 1
            print(f"    [OK] {len(text.split())} words in {hhmm(time.time() - started)}"
                  f" -> {out}")
        except Exception as exc:
            failed += 1
            print(f"    [FAIL] {type(exc).__name__}: {str(exc).splitlines()[0][:100]}")
        finally:
            for leftover in ([mp4, mp4 + ".part"] + ([] if args.keep_audio else [wav])):
                if os.path.exists(leftover):
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass

    if not args.keep_audio:
        try:
            os.rmdir(work)
        except OSError:
            pass

    print(f"\n  transcribed {done}, failed {failed}")
    if done:
        print(f"  Re-run build_module_pdf.py to fold them into the PDF:")
        print(f"    python tools/build_module_pdf.py --module {args.module or '<n>'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
