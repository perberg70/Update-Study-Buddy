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


def backend_installed():
    """True when local speech-to-text is actually available here."""
    for module in ("faster_whisper", "whisper"):
        try:
            __import__(module)
            return True
        except Exception:
            continue
    return False


# Course audio linked from unit HTML - the three .m4a files in the real course
# are summaries and a briefing, 10 to 45 minutes long.
EU = "EU AI Act for the public sector.m4a"
AUDIO_FILES = {
    "course.xml": '<course url_name="HT26"/>',
    "course/HT26.xml": '<course><chapter url_name="c3"/></course>',
    "chapter/c3.xml": '<chapter display_name="3. Use cases">'
                      '<sequential url_name="s1"/><sequential url_name="s2"/></chapter>',
    "sequential/s1.xml": '<sequential display_name="Public sector">'
                         '<vertical url_name="v1"/></sequential>',
    "vertical/v1.xml": '<vertical display_name="Listen">'
                       '<html url_name="a"/><html url_name="b"/></vertical>',
    # Linked twice, under edX's URL spelling; a document and an image beside it.
    "html/a.html": '<a href="/static/EU_AI_Act_for_the_public_sector.m4a">listen</a>'
                   '<a href="/static/Section_5.m4a">summary</a>'
                   '<a href="/static/brief.pdf">brief</a>'
                   '<img src="/static/diagram.png"/>',
    "html/b.html": '<a href="/static/EU_AI_Act_for_the_public_sector.m4a">again</a>',
    # A staff-only unit's audio is not on the page, so not transcribed.
    "sequential/s2.xml": '<sequential display_name="Retired" visible_to_staff_only="true">'
                         '<vertical url_name="v2"/></sequential>',
    "vertical/v2.xml": '<vertical display_name="Old"><html url_name="old"/></vertical>',
    "html/old.html": '<a href="/static/Hidden.m4a">old</a>',
    "static/" + EU: "e" * 1000,
    # 160000 bytes is ten seconds at the assumed bitrate - the estimate path.
    "static/Section_5.m4a": "s" * 160000,
    "static/brief.pdf": "not audio",
    "static/diagram.png": "not audio",
    "static/Hidden.m4a": "h",
}


def audio_cases(base, failures):
    from asset_text import asset_key, audio_source_id
    from build_module_pdf import record_transcript
    import extract_edx

    archive = open_archive(os.path.join(base, "course.audio.tar.gz"), AUDIO_FILES)
    modules = group_modules(extract_edx.parse_course(archive)["chapters"])
    store = os.path.join(base, "audio_store")
    os.makedirs(store)

    # ffprobe stands in: the EU file measures 40 minutes, Section_5 cannot be read.
    probed = []

    def probe(path):
        probed.append(os.path.basename(path))
        return {1000: 2400.0}.get(os.path.getsize(path))

    def rows_for(**kw):
        found = tv.plan(modules, archive, store, tv.DEFAULT_MAX_MINUTES,
                        probe=probe, **kw)
        return found, {r["url_name"]: r for r in found}

    eu, s5 = asset_key(EU), asset_key("Section_5.m4a")
    found, rows = rows_for(archive_sha="sha1")

    check(failures, eu in rows and rows[eu]["kind"] == "audio" and rows[eu]["do"],
          f"linked audio should be planned: {rows.get(eu)}")
    # 40 minutes: over the 20-minute video limit, under the audio one. The two
    # limits are separate on purpose.
    check(failures, rows.get(eu, {}).get("duration") == 2400.0
          and not rows[eu]["estimated"],
          f"a probed length is measured, not estimated: {rows.get(eu)}")
    check(failures, sum(1 for r in found if r["url_name"] == eu) == 1,
          "linked from two components, still one recording")
    check(failures, rows.get(s5, {}).get("do") and rows[s5]["estimated"]
          and rows[s5]["duration"] == 10.0 and "estimated" in rows[s5]["why"],
          f"an unmeasurable file is planned on a labelled estimate: {rows.get(s5)}")
    check(failures, asset_key("Hidden.m4a") not in rows,
          "audio in a staff-only unit must not be transcribed")
    check(failures, all(r["kind"] == "audio" for r in found),
          f"documents and images are not audio: {[r['title'] for r in found]}")

    # A lowered audio limit skips it, and says which flag brings it back.
    _, tight = rows_for(archive_sha="sha1", max_audio_minutes=30)
    check(failures, not tight[eu]["do"] and "--max-audio-minutes" in tight[eu]["why"],
          f"over the audio limit should name the flag: {tight[eu]['why']!r}")

    # A current transcript is skipped - before the file is pulled out to measure.
    with open(os.path.join(store, eu + ".txt"), "w", encoding="utf-8") as fh:
        fh.write("already transcribed")
    record_transcript(store, eu, "whisper", audio_source_id(archive, EU), "sha1")
    del probed[:]
    _, current = rows_for(archive_sha="sha1")
    check(failures, not current[eu]["do"] and "already" in current[eu]["why"],
          f"a current transcript should be kept: {current[eu]['why']!r}")
    check(failures, len(probed) == 1,
          f"only the untranscribed file should be measured, probed {len(probed)}")

    # The same transcript from another export is stale, and redone.
    _, other = rows_for(archive_sha="sha2")
    check(failures, other[eu]["do"] and "another" in other[eu]["why"],
          f"a transcript from another export should be redone: {other[eu]['why']!r}")

    # --force redoes a current one, but never beats the limit.
    _, forced = rows_for(archive_sha="sha1", force=True)
    check(failures, forced[eu]["do"], "--force should re-transcribe")
    _, forced_tight = rows_for(archive_sha="sha1", force=True, max_audio_minutes=30)
    check(failures, not forced_tight[eu]["do"], "--force must not override the limit")

    # The run itself, with the network made unusable: audio must still
    # transcribe, which proves its bytes came from the archive.
    saved = (tv.download_with_retry, tv.to_audio)

    def no_network(*_a, **_k):
        raise AssertionError("audio must not be downloaded")

    def fake_to_audio(src, dest):
        with open(src, "rb") as fh:
            data = fh.read()
        with open(dest, "wb") as out:
            out.write(data)

    heard = []

    def fake_transcribe(wav):
        with open(wav, "rb") as fh:
            heard.append(fh.read())
        return "Welcome to the section on the AI Act."

    tv.download_with_retry, tv.to_audio = no_network, fake_to_audio
    work = os.path.join(base, "work")
    os.makedirs(work)
    try:
        ok = tv.transcribe_one(other[eu], archive, fake_transcribe, work, store, "sha2")
    except AssertionError as exc:
        ok = False
        check(failures, False, str(exc))
    finally:
        tv.download_with_retry, tv.to_audio = saved

    check(failures, ok and heard == [b"e" * 1000],
          "the transcriber should hear the archive's own bytes")
    with open(os.path.join(store, eu + ".txt"), encoding="utf-8") as fh:
        check(failures, "AI Act" in fh.read(), "the transcript should be written")
    with open(os.path.join(store, ".sources.json"), encoding="utf-8") as fh:
        entry = json.load(fh).get(eu, {})
    check(failures, entry.get("source") == audio_source_id(archive, EU)
          and entry.get("archive") == "sha2",
          f"provenance should name the file and the export: {entry}")
    check(failures, os.listdir(work) == [],
          f"temporary media should be removed: {os.listdir(work)}")


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

        # A missing backend must be a clear instruction, not an ImportError.
        # Only assertable where none is installed: faster-whisper is in
        # requirements.txt, so anyone who installed it - including CI - would
        # otherwise see this fail for the wrong reason.
        if backend_installed():
            print("  [SKIP] a whisper backend is installed; "
                  "the missing-backend message is not exercised")
        else:
            try:
                tv.load_transcriber("small")
                check(failures, False, "no backend is installed, so this should raise")
            except RuntimeError as exc:
                check(failures, "faster-whisper" in str(exc) and "locally" in str(exc),
                      f"the message should name the install and say it is local: {exc}")
            except Exception as exc:
                check(failures, False,
                      f"expected RuntimeError, got {type(exc).__name__}: {exc}")

    with tempfile.TemporaryDirectory() as base:
        audio_cases(base, failures)

    # Two OpenMP runtimes abort the process on Windows rather than raising, so
    # the variable has to be set before the backend is touched - the user had
    # to do it by hand, which is the bug.
    saved = os.environ.pop("KMP_DUPLICATE_LIB_OK", None)
    try:
        try:
            tv.load_transcriber("small")
        except Exception:
            pass    # the variable is set before the backend is touched either way
        check(failures, os.environ.get("KMP_DUPLICATE_LIB_OK") == "TRUE",
              "KMP_DUPLICATE_LIB_OK should be set before loading the backend")

        # A value the operator chose must survive.
        os.environ["KMP_DUPLICATE_LIB_OK"] = "FALSE"
        try:
            tv.load_transcriber("small")
        except Exception:
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
        print("  [PASS] linked audio planned once, on its own limit; measured or")
        print("         labelled as estimated; read from the archive, not the network")
    return not failures


if __name__ == "__main__":
    print("local transcription planning")
    raise SystemExit(0 if main() else 1)
