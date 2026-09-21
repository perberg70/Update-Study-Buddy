"""Read an OLX course directly from its .tar.gz, without unpacking it.

The pipeline used to extract into edx_export/ and read from there. That
directory was persistent mutable state shared between runs: extracting a second
archive over it left behind every file the new one did not contain, so a course
could have its structure from one export and its content from another, with
nothing recording which. Reading from the archive makes that impossible rather
than merely unlikely - there is one input, and it is named.

Small text members are cached on open in a single sequential pass. Random access
into a gzip stream re-reads from the start each time, so per-file seeking would
be quadratic across a few hundred components.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import posixpath
import tarfile

# OLX directories holding structure and content. Everything here is text and
# small enough to hold in memory.
CACHED_DIRS = ("course/", "chapter/", "sequential/", "vertical/", "html/",
               "video/", "about/", "policies/", "problem/", "discussion/")

# static/ can hold large media, so only transcript-shaped files are cached.
CACHED_STATIC_SUFFIXES = (".srt", ".sjson", ".vtt", ".json", ".txt", ".xml")
MAX_CACHED_BYTES = 5 * 1024 * 1024


class CourseArchiveError(RuntimeError):
    """The archive is unreadable, or holds no recognisable OLX course."""


class CourseArchive:
    """Read-only view of the OLX tree inside a course archive.

    Paths are relative to the course root: "html/h1.html", "video/v1.xml".
    """

    def __init__(self, tar_path: str):
        self.tar_path = os.path.abspath(tar_path)
        if not os.path.exists(self.tar_path):
            raise CourseArchiveError(f"{tar_path} not found")

        self._sizes: dict[str, int] = {}
        self._cache: dict[str, bytes] = {}
        self.root = ""

        try:
            with tarfile.open(self.tar_path, "r:gz") as tar:
                self._scan(tar)
        except tarfile.TarError as exc:
            raise CourseArchiveError(f"cannot read {tar_path}: {exc}") from exc

        if not self._sizes:
            raise CourseArchiveError(f"{tar_path} contains no files")

    # -- loading ---------------------------------------------------------

    def _scan(self, tar):
        raw = {}
        for member in tar:
            if not member.isfile():
                continue
            name = member.name.replace("\\", "/").lstrip("./")
            raw[name] = member

        self.root = self._find_root(raw)
        prefix = f"{self.root}/" if self.root else ""

        for name, member in raw.items():
            if prefix and not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if not rel:
                continue
            self._sizes[rel] = member.size
            if self._should_cache(rel, member.size):
                handle = tar.extractfile(member)
                if handle is not None:
                    self._cache[rel] = handle.read()

    @staticmethod
    def _find_root(raw):
        """Directory prefix holding course.xml: '' at archive root, else its name."""
        candidates = [n for n in raw if posixpath.basename(n) == "course.xml"]
        if not candidates:
            top = sorted({n.split("/", 1)[0] for n in raw})[:12]
            raise CourseArchiveError(
                "no course.xml in the archive. Top-level entries: " + ", ".join(top))
        # Shallowest wins: course/course.xml inside the root is the OLX course
        # definition, not the archive root marker.
        candidates.sort(key=lambda n: n.count("/"))
        best = candidates[0]
        return posixpath.dirname(best)

    @staticmethod
    def _should_cache(rel, size):
        if size > MAX_CACHED_BYTES:
            return False
        if rel == "course.xml" or rel.startswith(CACHED_DIRS):
            return True
        if rel.startswith("static/"):
            return rel.lower().endswith(CACHED_STATIC_SUFFIXES)
        return False

    # -- reading ---------------------------------------------------------

    def exists(self, relpath: str) -> bool:
        return self._norm(relpath) in self._sizes

    def size(self, relpath: str) -> int:
        return self._sizes.get(self._norm(relpath), -1)

    def read_bytes(self, relpath: str):
        """Contents, or None when absent. Uncached members are re-read."""
        rel = self._norm(relpath)
        if rel in self._cache:
            return self._cache[rel]
        if rel not in self._sizes:
            return None
        prefix = f"{self.root}/" if self.root else ""
        with tarfile.open(self.tar_path, "r:gz") as tar:
            handle = tar.extractfile(prefix + rel)
            return handle.read() if handle else None

    def read_text(self, relpath: str, encoding="utf-8"):
        data = self.read_bytes(relpath)
        return None if data is None else data.decode(encoding, "replace")

    def listdir(self, subdir: str):
        """Immediate file names under *subdir*."""
        prefix = self._norm(subdir).rstrip("/") + "/"
        return sorted({rel[len(prefix):] for rel in self._sizes
                       if rel.startswith(prefix) and "/" not in rel[len(prefix):]})

    def extract_to(self, relpath: str, dest: str) -> bool:
        """Write one member to *dest*. Used for static assets that must be files."""
        data = self.read_bytes(relpath)
        if data is None:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with open(dest, "wb") as out:
            out.write(data)
        return True

    @staticmethod
    def _norm(relpath):
        return str(relpath).replace("\\", "/").lstrip("./")

    # -- provenance ------------------------------------------------------

    def fingerprint(self) -> dict:
        """Identity of this archive, for open_for_structure to compare.

        The whole file is hashed. It used to be the first 8 MiB truncated to
        12 hex characters, which is not an identity: a course archive is
        hundreds of MB, its early tar members are the structure files that
        change least between exports, and open_for_structure treats equality
        of this value as proof that structure and content came from the same
        export. Two exports differing only in later HTML, transcripts or media
        would have passed that check.

        Reading a few hundred MB costs about a second, once per command.
        """
        digest = hashlib.sha256()
        with open(self.tar_path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        return {
            "tar": self.tar_path,
            "size_bytes": os.path.getsize(self.tar_path),
            "sha256": digest.hexdigest()[:16],
            "tar_modified": dt.datetime.fromtimestamp(
                os.path.getmtime(self.tar_path)).isoformat(" ", "seconds"),
            "read_at": dt.datetime.now().isoformat(" ", "seconds"),
            "files_in_archive": len(self._sizes),
            "course_root": self.root or "(archive root)",
        }

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __repr__(self):
        return (f"<CourseArchive {os.path.basename(self.tar_path)} "
                f"root={self.root or '/'} files={len(self._sizes)}>")


def describe_source(structure):
    """Say which archive a course_structure.json came from, so output can be traced."""
    source = (structure or {}).get("_source") or {}
    if source.get("tar"):
        sha = source.get("sha256") or source.get("sha256_head") or "?"
        print(f"[OK] Course source: {os.path.basename(source['tar'])} "
              f"(sha:{sha}, read {source.get('read_at', '?')})")
    else:
        print("[WARN] course_structure.json records no source archive. Re-run")
        print("       extract_edx.py --tar <archive> to record one.")


def open_for_structure(structure, explicit_tar=None, allow_stale=False):
    """Open the archive a *structure* was parsed from, refusing a mismatch.

    Anything that produces an artifact - a PDF, an mp3, a transcript - takes
    its skeleton from course_structure.json and its content from the archive.
    Nothing forces those to be the same export, and when they are not the
    result is a document whose structure says one thing and whose text says
    another, which is exactly the failure this project spent a session
    chasing.

    A warning is not enough: the last one printed correctly, above sixty lines
    of transcription output, and was gone by the time it mattered. So this
    raises, names the fix, and offers allow_stale for the operator who has
    looked and decided.
    """
    source = (structure or {}).get("_source") or {}
    recorded = source.get("sha256")

    if not recorded and not allow_stale:
        # sha256_head was a partial hash of the first 8 MiB; it cannot be
        # compared against a full-file hash, so a structure carrying only that
        # is as unverifiable as one carrying nothing.
        older = " It records only the older partial hash, which cannot be\n" \
                "  compared against a whole-archive one." if source.get("sha256_head") \
                else " It was written before provenance was tracked."
        raise CourseArchiveError(
            "course_structure.json records no usable source archive, so there is\n"
            "  no way to tell whether it describes the archive about to be read."
            + older + "\n"
            "  Fix:      python extract_edx.py --tar <archive>\n"
            "  Override: --allow-stale (builds anyway, provenance unverified)")

    archive = open_course_archive(explicit_tar)
    if allow_stale:
        return archive

    current = archive.fingerprint()["sha256"]
    if recorded == current:
        # Say it once, here, on the path that actually proceeds - callers do
        # not also call describe_source, or every run prints its source twice.
        print(f"[OK] Course source: {os.path.basename(archive.tar_path)} "
              f"(sha:{current}, structure and content agree)")
        return archive

    if recorded != current:
        raise CourseArchiveError(
            "course_structure.json was parsed from a different archive than the\n"
            "  one being read. The document would take its structure from one\n"
            "  export and its content from another.\n"
            f"  structure came from: sha:{recorded} ({os.path.basename(source.get('tar', '?'))})\n"
            f"  about to read:       sha:{current} ({os.path.basename(archive.tar_path)})\n"
            "  Fix:      python extract_edx.py --tar "
            f"{os.path.basename(archive.tar_path)}\n"
            "  Override: --allow-stale (builds anyway, provenance unverified)")


def open_course_archive(explicit_tar=None, structure_path=None):
    """Open the archive a run should read.

    Preference order:
      1. an explicitly named archive;
      2. the one recorded in course_structure.json, so every artifact is built
         from the same export that produced the structure;
      3. resolve_tar_path's newest-match, with a warning - that is a guess.
    """
    import json

    from config import COURSE_STRUCTURE_PATH, resolve_tar_path

    if explicit_tar:
        return CourseArchive(explicit_tar)

    structure_path = structure_path or COURSE_STRUCTURE_PATH
    if os.path.exists(structure_path):
        try:
            with open(structure_path, encoding="utf-8") as fh:
                recorded = (json.load(fh).get("_source") or {}).get("tar")
        except Exception:
            recorded = None
        if recorded and os.path.exists(recorded):
            return CourseArchive(recorded)
        if recorded:
            print(f"[WARN] {structure_path} names {os.path.basename(recorded)}, "
                  "which is no longer there.")

    chosen = resolve_tar_path()
    print(f"[WARN] No archive recorded; falling back to the newest match: "
          f"{os.path.basename(chosen)}")
    print("       Pass --tar to be certain which export is used.")
    return CourseArchive(chosen)
