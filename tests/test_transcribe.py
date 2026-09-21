"""Which videos get transcribed locally, and which are left alone.

Transcription is the expensive step, so the decision matters more than the
mechanics: a webinar sent to Whisper instead of its Teams export costs an hour
of CPU for a worse result, and re-transcribing something already transcribed
costs the same for no result at all.

The model itself is stubbed - this covers everything up to and including the
write, which is where the decisions live.

Run: python tests/test_transcribe.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fixtures import open_archive  # noqa: E402

import transcribe_videos as tv  # noqa: E402
from build_module_pdf import group_modules  # noqa: E402

MP4 = '<encoded_video url="https://cdn.example/{}.mp4"/>'

FILES = {
    "course.xml": '<course url_name="HT26"/>',
    "course/HT26.xml": '<course><chapter url_name="c1"/></course>',
    "chapter/c1.xml": '<chapter display_name="1. Welcome">'
                      '<sequential url_name="s1"/><sequential url_name="s2"/></chapter>',
    "sequential/s1.xml": '<sequential display_name="Unit A">'
                         '<vertical url_name="v1"/></sequential>',
    "vertical/v1.xml": '<vertical display_name="Demos">'
                       + "".join(f'<video url_name="{n}"/>' for n in
                                 ("short", "webinar", "tube", "hastranscript",
                                  "instore", "nomp4", "nodur")) +
                       '</vertical>',
    # 6 minutes, direct mp4, nothing else: the case this tool exists for.
    "video/short.xml": '<video display_name="Erik demo">'
                       '<video_asset duration="360">' + MP4.format("short") +
                       '</video_asset></video>',
    # 75 minutes: over the line, belongs to the Teams export.
    "video/webinar.xml": '<video display_name="Webinar 3 recording">'
                         '<video_asset duration="4500">' + MP4.format("web") +
                         '</video_asset></video>',
    # YouTube-hosted: no mp4 to transcribe at all.
    "video/tube.xml": '<video display_name="Animals speaking" '
                      'youtube_id_1_0="1.00:abc123"><video_asset duration="180"/></video>',
    # Already has a transcript inside the export.
    "video/hastranscript.xml": '<video display_name="Already captioned" sub="cap">'
                               '<video_asset duration="300">' + MP4.format("hc") +
                               '</video_asset></video>',
    "static/subs_cap.srt.sjson": json.dumps({"text": ["already here"]}),
    # Already has one in the store (written by the test).
    "video/instore.xml": '<video display_name="From Teams">'
                         '<video_asset duration="300">' + MP4.format("is") +
                         '</video_asset></video>',
    # No downloadable mp4 and not YouTube.
    "video/nomp4.xml": '<video display_name="Nothing to fetch">'
                       '<video_asset duration="120"/></video>',
    # No duration recorded: transcribe rather than silently skip.
    "video/nodur.xml": '<video display_name="Unknown length">'
                       '<video_asset>' + MP4.format("nd") + '</video_asset></video>',
    # A staff-only unit: its videos are not on the page, so not transcribed.
    "sequential/s2.xml": '<sequential display_name="Retired" visible_to_staff_only="true">'
                         '<vertical url_name="v2"/></sequential>',
    "vertical/v2.xml": '<vertical display_name="Old"><video url_name="retired"/></vertical>',
    "video/retired.xml": '<video display_name="Retired clip">'
                         '<video_asset duration="300">' + MP4.format("rt") +
                         '</video_asset></video>',
}


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


def main():
    failures = []
    with tempfile.TemporaryDirectory() as base:
        archive = open_archive(os.path.join(base, "course.tv.tar.gz"), FILES)
        store = os.path.join(base, "transcripts")
        os.makedirs(store)
        with open(os.path.join(store, "instore.txt"), "w", encoding="utf-8") as fh:
            fh.write("exported from Teams by hand")

        import extract_edx
        modules = group_modules(extract_edx.parse_course(archive)["chapters"])
        rows = {r["url_name"]: r for r in
                tv.plan(modules, archive, store, tv.DEFAULT_MAX_MINUTES)}

        check(failures, rows["short"]["do"], "a 6 min clip with an mp4 should be transcribed")
        check(failures, rows["short"]["url"].endswith("short.mp4"),
              f"the mp4 url should be picked up, got {rows['short']['url']!r}")
        check(failures, rows["nodur"]["do"],
              "unknown length should be transcribed, not silently skipped")

        for name, expect in [("webinar", "limit"), ("tube", "YouTube"),
                             ("hastranscript", "in the export"),
                             ("instore", "already in"), ("nomp4", "no downloadable")]:
            check(failures, not rows[name]["do"], f"{name} should be skipped")
            check(failures, expect.lower() in rows[name]["why"].lower(),
                  f"{name}: reason should mention {expect!r}, got {rows[name]['why']!r}")

        check(failures, "retired" not in rows,
              "a staff-only unit's videos must not be transcribed")

        # --force overrides existing transcripts, but never the structural skips.
        forced = {r["url_name"]: r for r in
                  tv.plan(modules, archive, store, tv.DEFAULT_MAX_MINUTES, force=True)}
        check(failures, forced["hastranscript"]["do"] and forced["instore"]["do"],
              "--force should re-transcribe those that already have text")
        check(failures, not forced["tube"]["do"] and not forced["webinar"]["do"],
              "--force must not override 'no mp4' or the duration limit")

        # A raised limit brings the webinar in; a lowered one excludes the clip.
        wide = {r["url_name"]: r for r in tv.plan(modules, archive, store, 90)}
        check(failures, wide["webinar"]["do"], "--max-minutes 90 should include a 75 min video")
        narrow = {r["url_name"]: r for r in tv.plan(modules, archive, store, 5)}
        check(failures, not narrow["short"]["do"], "--max-minutes 5 should exclude a 6 min video")

        # No backend installed must be a clear instruction, not an ImportError.
        try:
            tv.load_transcriber("small")
            check(failures, False, "a backend is installed here; expected none")
        except RuntimeError as exc:
            check(failures, "faster-whisper" in str(exc) and "locally" in str(exc),
                  f"the message should name the install and say it is local: {exc}")
        except Exception as exc:
            check(failures, False, f"expected RuntimeError, got {type(exc).__name__}: {exc}")

    # Two OpenMP runtimes abort the process on Windows rather than raising, so
    # the variable has to be set before the backend is touched - the user had
    # to do it by hand, which is the bug.
    saved = os.environ.pop("KMP_DUPLICATE_LIB_OK", None)
    try:
        try:
            tv.load_transcriber("small")
        except RuntimeError:
            pass
        check(failures, os.environ.get("KMP_DUPLICATE_LIB_OK") == "TRUE",
              "KMP_DUPLICATE_LIB_OK should be set before loading the backend")

        # A value the operator chose must survive.
        os.environ["KMP_DUPLICATE_LIB_OK"] = "FALSE"
        try:
            tv.load_transcriber("small")
        except RuntimeError:
            pass
        check(failures, os.environ["KMP_DUPLICATE_LIB_OK"] == "FALSE",
              "an explicitly set KMP_DUPLICATE_LIB_OK must not be overwritten")
    finally:
        os.environ.pop("KMP_DUPLICATE_LIB_OK", None)
        if saved is not None:
            os.environ["KMP_DUPLICATE_LIB_OK"] = saved

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] short clips selected; webinars, YouTube, no-mp4 and")
        print("         already-transcribed all skipped with a stated reason")
        print("  [PASS] --force and --max-minutes behave; hidden units excluded")
        print("  [PASS] a missing backend explains the local install")
        print("  [PASS] KMP_DUPLICATE_LIB_OK set automatically, explicit value kept")
    return not failures


if __name__ == "__main__":
    print("local transcription planning")
    raise SystemExit(0 if main() else 1)
