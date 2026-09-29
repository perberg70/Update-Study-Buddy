"""Build small OLX archives for tests.

Everything now reads the course out of a .tar.gz, so fixtures have to be
archives rather than directories. Writing the files out first and taring them
keeps the fixtures readable and exercises the same path a real export takes.
"""

import io
import os
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.path.join(ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "tools"))

from olx_archive import CourseArchive  # noqa: E402


def write_tree(base, files):
    """Write {relative path: text} under *base* and return it."""
    for relpath, text in files.items():
        full = os.path.join(base, relpath.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with io.open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
    return base


def make_archive(path, files, prefix=""):
    """Tar {relative path: text} to *path*, optionally under a wrapper directory."""
    src = path + ".src"
    write_tree(src, files)
    with tarfile.open(path, "w:gz") as tar:
        for dirpath, _dirs, names in os.walk(src):
            for name in names:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, src).replace(os.sep, "/")
                tar.add(full, arcname=f"{prefix}/{rel}" if prefix else rel)
    return path


def open_archive(path, files, prefix=""):
    """make_archive, then open it."""
    return CourseArchive(make_archive(path, files, prefix))
