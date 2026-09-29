"""Three bugs found by review on PR #11, each introduced by this branch.

Run: python tests/test_review_findings.py
"""

import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

from _fixtures import make_archive  # noqa: E402

import delete_agent  # noqa: E402
import organize_content as oc  # noqa: E402
from olx_archive import CourseArchive, CourseArchiveError, open_for_structure  # noqa: E402

COURSE = {"course.xml": '<course url_name="r"/>', "course/r.xml": "<course/>"}


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


def test_same_title_replace(failures):
    """Uploading before deleting must not delete the replacement too.

    apply_review uploads first, so a REPLACE whose new file carries the old
    title leaves two rows with that title. max_deletions=None then meant
    "delete every match" - both of them.
    """
    with tempfile.TemporaryDirectory() as base:
        review = os.path.join(base, "review.json")
        json.dump({
            "pairs": [
                # Same title on both sides: the dangerous case.
                {"old_name": "01_Welcome.txt", "new_name": "01_Welcome.txt",
                 "action": "REPLACE"},
                # Case and spacing differ only - still the same row in the panel.
                {"old_name": "02  Learning.txt", "new_name": "02 learning.txt",
                 "action": "REPLACE"},
                # Genuinely different titles: deleting every match is correct.
                {"old_name": "1 Ch - Old.mp3", "new_name": "New.mp3",
                 "action": "REPLACE"},
                # A plain DELETE has no replacement arriving.
                {"old_name": "Retired.txt", "new_name": "Retired.txt",
                 "action": "DELETE"},
            ],
            "current_only": [{"name": "Orphan.txt", "action": "DELETE"}],
            "new_only": [],
        }, io.open(review, "w", encoding="utf-8"))

        real = delete_agent.REVIEW_PATH
        delete_agent.REVIEW_PATH = review
        try:
            plan = {item["name"]: item for item in delete_agent.get_sources_to_remove()}
        finally:
            delete_agent.REVIEW_PATH = real

    check(failures, plan["01_Welcome.txt"].get("keep_one_copy") is True,
          "a same-title REPLACE must keep one copy, not delete every match")
    check(failures, plan["02  Learning.txt"].get("keep_one_copy") is True,
          "titles differing only in case/space are the same row")
    check(failures, plan["1 Ch - Old.mp3"].get("max_deletions", "missing") is None,
          "a differently-titled REPLACE should still delete every match")
    check(failures, not plan["Retired.txt"].get("keep_one_copy"),
          "DELETE uploads no replacement, so nothing needs keeping")
    check(failures, not plan["Orphan.txt"].get("keep_one_copy"),
          "current_only DELETE uploads no replacement either")


HEAD_WINDOW = 8 * 1024 * 1024   # what the old partial hash covered


def build_padded(path, pad, tail):
    """A course archive over HEAD_WINDOW, differing only after it.

    Two details make this a real test rather than one that passes anyway:

    - the pad is random, so gzip cannot shrink it below the window. A 9 MB
      text pad compresses to 9 KB, which would leave the differing member
      inside the old hash's reach.
    - gzip stamps both an mtime and the source filename into its header, so
      two archives otherwise identical differ at byte 10. Zeroing both
      isolates the property under test: when the leading bytes match, a
      partial hash cannot tell the archives apart. Real exports usually do
      differ in that header, so the practical failure this fixes is tail
      corruption or truncation going unnoticed, rather than two whole exports
      being confused for each other.
    """
    with open(path, "wb") as raw:
        # filename="" and mtime=0: gzip otherwise stamps both into its header,
        # so two archives differ at byte 10 for reasons unrelated to content.
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                def add(name, data):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))

                add("course.xml", b'<course url_name="r"/>')
                add("course/r.xml", b"<course/>")
                add("static/pad.bin", pad)
                add("html/late.html", tail)
    return path


def test_whole_archive_hash(failures):
    """The fingerprint must cover the whole archive, not just its head."""
    with tempfile.TemporaryDirectory() as base:
        pad = os.urandom(HEAD_WINDOW + 1024 * 1024)   # same in both, incompressible
        a = build_padded(os.path.join(base, "course.a.tar.gz"), pad, b"<p>export A</p>")
        b = build_padded(os.path.join(base, "course.b.tar.gz"), pad, b"<p>export B</p>")

        # The fixture is only meaningful if it defeats the old hash.
        def head_hash(path):
            with open(path, "rb") as fh:
                return hashlib.sha256(fh.read(HEAD_WINDOW)).hexdigest()[:12]

        check(failures, os.path.getsize(a) > HEAD_WINDOW,
              "the fixture must exceed the old hash window to test anything")
        check(failures, head_hash(a) == head_hash(b),
              "the old partial hash should have collided here - otherwise this "
              "test would pass without the fix")

        fa = CourseArchive(a).fingerprint()
        fb = CourseArchive(b).fingerprint()
        check(failures, "sha256" in fa, "fingerprint should expose sha256")
        check(failures, "sha256_head" not in fa,
              "the partial-hash key must be gone, not left to be compared by mistake")
        check(failures, fa["sha256"] != fb["sha256"],
              "archives differing only in a late member must hash differently")

        # And the guard built on it must actually refuse the mismatch.
        structure = {"chapters": [], "_source": {"tar": a, "sha256": fa["sha256"]}}
        try:
            open_for_structure(structure, b)
            failures.append("open_for_structure accepted a late-member mismatch")
        except CourseArchiveError:
            pass

        # A structure carrying only the old partial key is unverifiable.
        stale = {"chapters": [], "_source": {"tar": a, "sha256_head": "abc123def456"}}
        try:
            open_for_structure(stale, a)
            failures.append("a structure with only sha256_head must be refused")
        except CourseArchiveError as exc:
            check(failures, "extract_edx.py" in str(exc),
                  f"the refusal should name the fix: {exc}")
        if open_for_structure(stale, a, allow_stale=True) is None:
            failures.append("--allow-stale should still proceed")


def test_audio_cache_invalidation(failures):
    """An mp3 is reused only when built from this URL and this export."""
    with tempfile.TemporaryDirectory() as base:
        index = {}
        key, url, sha = "01_Ch/Demo.mp3", "https://cdn/demo.mp4", "aaaa1111bbbb2222"

        check(failures, not oc.audio_is_current(index, key, url, sha),
              "an unknown mp3 must not be reused")

        index[key] = {"url": url, "archive": sha}
        check(failures, oc.audio_is_current(index, key, url, sha),
              "same url and same export should reuse")
        check(failures, not oc.audio_is_current(index, key, url, "cccc3333dddd4444"),
              "a new export must rebuild, even with the same url")
        check(failures, not oc.audio_is_current(index, key, "https://cdn/other.mp4", sha),
              "a new url must rebuild, even within the same export")

        # Round-trips to disk, and a corrupt index is empty rather than fatal.
        oc.save_audio_index(base, index)
        check(failures, oc.load_audio_index(base) == index, "index should round-trip")
        io.open(os.path.join(base, oc.AUDIO_INDEX_NAME), "w").write("{not json")
        check(failures, oc.load_audio_index(base) == {},
              "a corrupt index should read as empty, not raise")


def test_audio_index_survives_interruption(failures):
    """Each mp3 is recorded as it is built, not once the whole run finishes.

    The index exists so an interrupted run resumes. Held in memory until the
    loop ends, it records nothing at all when the run is interrupted - which
    is the only time resumability matters.
    """
    with tempfile.TemporaryDirectory() as base:
        live = {}
        oc.record_audio(base, live, "01_Ch/A.mp3", "https://cdn/a.mp4", "sha-one")

        # Nothing else has run: a separate reader must already see it.
        on_disk = oc.load_audio_index(base)
        check(failures, on_disk == {"01_Ch/A.mp3": {"url": "https://cdn/a.mp4",
                                                    "archive": "sha-one"}},
              f"one mp3 should be on disk immediately, got {on_disk}")

        oc.record_audio(base, live, "01_Ch/B.mp3", "https://cdn/b.mp4", "sha-one")
        check(failures, len(oc.load_audio_index(base)) == 2,
              "the second should be there too, without waiting for the run to end")

        # As if the run died here: a fresh process reuses both.
        reloaded = oc.load_audio_index(base)
        check(failures, oc.audio_is_current(reloaded, "01_Ch/A.mp3",
                                            "https://cdn/a.mp4", "sha-one"),
              "an interrupted run's work must be reusable next time")

        # A failed rebuild must not leave a claim to an mp3 that was deleted.
        oc.forget_audio(base, live, "01_Ch/A.mp3")
        check(failures, "01_Ch/A.mp3" not in oc.load_audio_index(base),
              "forget_audio should remove the entry from disk, not just memory")
        check(failures, "01_Ch/B.mp3" in oc.load_audio_index(base),
              "...and leave the others alone")
        oc.forget_audio(base, live, "not-there.mp3")   # must not raise


def test_fingerprint_memoised(failures):
    """Hashing a few hundred MB should happen once per instance, not per call."""
    with tempfile.TemporaryDirectory() as base:
        path = make_archive(os.path.join(base, "course.m.tar.gz"), COURSE)
        archive = CourseArchive(path)

        first = archive.fingerprint()
        second = archive.fingerprint()
        check(failures, first == second,
              "repeated calls must agree - read_at is a timestamp, so this only "
              "holds if the result is cached")

        # Mutating what a caller got back must not poison the cache.
        second["sha256"] = "tampered"
        check(failures, archive.fingerprint()["sha256"] == first["sha256"],
              "fingerprint() should hand out a copy, not its own dict")

        # A changed file is still caught, because that means a new instance.
        with open(path, "ab") as fh:
            fh.write(b"appended")
        check(failures, CourseArchive(path).fingerprint()["sha256"] != first["sha256"],
              "a new CourseArchive must hash afresh, or which_archive.py goes blind")


def test_transcript_staleness(failures):
    """A generated transcript is reused only while it matches its video.

    Same failure the audio cache had: a re-recorded video keeps its OLX
    url_name, so existence-only lookup embeds last year's speech in this
    year's PDF - and both fetch tools count that file as already done, so the
    documented workflow never refreshes it.
    """
    import build_module_pdf as bmp

    with tempfile.TemporaryDirectory() as base:
        # Generated by this project, from a known video and export.
        bmp.record_transcript(base, "vid1", "whisper", "https://cdn/a.mp4", "sha-one")

        check(failures, not bmp.transcript_is_stale(base, "vid1",
                                                    "https://cdn/a.mp4", "sha-one"),
              "same video and same export is current")
        check(failures, bmp.transcript_is_stale(base, "vid1",
                                                "https://cdn/NEW.mp4", "sha-one"),
              "a different video must invalidate it")
        check(failures, bmp.transcript_is_stale(base, "vid1",
                                                "https://cdn/a.mp4", "sha-two"),
              "a different export must invalidate it")

        # A hand-dropped file - a Teams export - has no recorded provenance and
        # must always be trusted: the person who put it there is the authority.
        check(failures, not bmp.transcript_is_stale(base, "hand_dropped",
                                                    "https://cdn/x.mp4", "sha-one"),
              "an unrecorded transcript is hand-supplied and always used")

        # The identity the PDF computes must equal what the generators record.
        # The first version of this did not: video_source_id read the raw
        # attribute while fetch_youtube_transcripts recorded the parsed id, so
        # every fetched caption was stale the moment it was written. A unit
        # test with matching literals would not have caught it.
        import video_report
        import xml.etree.ElementTree as ET
        for xml, why in [
            ('<video youtube_id_1_0="1.00:abc123XYZ_1"/>', "1.00-prefixed id"),
            ('<video youtube="0.75:slow,1.00:real,1.25:fast"/>', "multi-speed"),
            ('<video youtube_id_1_0="bare"/>', "bare id"),
        ]:
            root = ET.fromstring(xml)
            check(failures, bmp.video_source_id(root) == video_report.youtube_id(root),
                  f"{why}: the PDF and the fetcher must agree on the video id "
                  f"({bmp.video_source_id(root)!r} vs {video_report.youtube_id(root)!r})")

        mp4 = ET.fromstring('<video><video_asset>'
                            '<encoded_video url="https://cdn/a.mp4"/>'
                            '</video_asset></video>')
        check(failures, bmp.video_source_id(mp4) == "https://cdn/a.mp4",
              "an mp4-only video is identified by its url, as transcribe records it")

        # Missing identity must not be read as a mismatch.
        check(failures, not bmp.transcript_is_stale(base, "vid1", "", ""),
              "unknown identity should not invalidate a recorded transcript")

        # A corrupt index must not take the run down.
        io.open(bmp.transcript_index_path(base), "w").write("{not json")
        check(failures, not bmp.transcript_is_stale(base, "vid1", "z", "z"),
              "a corrupt index should read as empty, not raise")


def test_delete_bound_survives_virtualization(failures):
    """A count that cannot see offscreen rows must not cap deletions at zero.

    count_source_occurrences reads the rendered DOM only and returns 0 on any
    error, while delete_one_source scrolls to find a row. Capping the loop at
    the count skipped deletions the scrolling search would have made, and
    --apply still reported success.
    """
    import delete_agent as da

    check(failures, getattr(da, "DELETE_SCAN_LIMIT", 0) > 1,
          "there should be a runaway guard, not a count, bounding exact deletes")

    src = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "delete_agent.py"), encoding="utf-8").read()
    # The exact-match branch must not be bounded by the observed count alone.
    check(failures, "max_deletions = max(observed, DELETE_SCAN_LIMIT)" in src,
          "exact matching should let the scrolling search decide when to stop")
    check(failures, "if fuzzy:" in src and "max_deletions = observed" in src,
          "fuzzy must stay bounded by what was seen - it can match unrelated rows")


def main():
    failures = []
    test_same_title_replace(failures)
    test_whole_archive_hash(failures)
    test_audio_cache_invalidation(failures)
    test_audio_index_survives_interruption(failures)
    test_fingerprint_memoised(failures)
    test_transcript_staleness(failures)
    test_delete_bound_survives_virtualization(failures)

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] same-title REPLACE keeps one copy; other rows unchanged")
        print("  [PASS] whole-archive hash distinguishes a difference past 8 MiB,")
        print("         where the old partial hash provably collided")
        print("  [PASS] cached audio invalidated by a new export or a new url")
        print("  [PASS] each mp3 recorded as built, so an interrupted run resumes")
        print("  [PASS] fingerprint memoised per instance, fresh for a new one")
        print("  [PASS] generated transcripts invalidated; hand-dropped trusted")
        print("  [PASS] exact deletes bounded by the scrolling search, not a")
        print("         count that cannot see offscreen rows")
    return not failures


if __name__ == "__main__":
    print("PR #11 review findings")
    raise SystemExit(0 if main() else 1)
