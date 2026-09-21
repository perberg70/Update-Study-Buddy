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


def main():
    failures = []
    test_same_title_replace(failures)
    test_whole_archive_hash(failures)
    test_audio_cache_invalidation(failures)

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] same-title REPLACE keeps one copy; other rows unchanged")
        print("  [PASS] whole-archive hash distinguishes a difference past 8 MiB,")
        print("         where the old partial hash provably collided")
        print("  [PASS] cached audio invalidated by a new export or a new url")
    return not failures


if __name__ == "__main__":
    print("PR #11 review findings")
    raise SystemExit(0 if main() else 1)
