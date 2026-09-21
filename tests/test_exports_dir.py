"""Where the course export lives, and what happens when it is ambiguous.

Picking the wrong export produced a document that disagreed with the course
page and took a long time to diagnose, so the rule is: one obvious folder, and
never a silent choice between two files.

Run: python tests/test_exports_dir.py
"""

import importlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fixtures import make_archive  # noqa: E402

import config  # noqa: E402

COURSE = {
    "course.xml": '<course url_name="r"/>',
    "course/r.xml": "<course/>",
}


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


def reload_config(project, exports):
    """config reads its directories at import time, so rebind them."""
    config.PROJECT_DIR = Path(project)
    config.EXPORTS_DIR = Path(exports)
    return config


def main():
    failures = []
    with tempfile.TemporaryDirectory() as base:
        project = os.path.join(base, "proj")
        exports = os.path.join(project, "course_exports")
        os.makedirs(exports)
        cfg = reload_config(project, exports)

        cwd = os.getcwd()
        os.chdir(project)
        try:
            # Nothing anywhere: the error names the folder and the routine.
            try:
                cfg.resolve_tar_path()
                failures.append("no export should raise")
            except FileNotFoundError as exc:
                for want in ("course_exports", "start_run.py"):
                    if want not in str(exc):
                        failures.append(f"the message should name {want}: {exc}")

            # One in the folder: used.
            good = make_archive(os.path.join(exports, "course.good.tar.gz"), COURSE)
            chosen, here, elsewhere = cfg.find_exports()
            check(failures, [p.name for p in here] == ["course.good.tar.gz"],
                  f"the folder's archive should be chosen, got {here}")
            check(failures, not elsewhere, f"nothing else should be found: {elsewhere}")
            check(failures, os.path.samefile(cfg.resolve_tar_path(), good),
                  "resolve_tar_path should return the folder's archive")

            # A stray in the project root is reported, not used, not fatal.
            stray = make_archive(os.path.join(project, "course.stray.tar.gz"), COURSE)
            chosen, here, elsewhere = cfg.find_exports()
            check(failures, [p.name for p in here] == ["course.good.tar.gz"],
                  "course_exports/ should still take precedence")
            check(failures, [p.name for p in elsewhere] == ["course.stray.tar.gz"],
                  f"the stray must be reported, not hidden: {elsewhere}")
            check(failures, os.path.samefile(cfg.resolve_tar_path(), good),
                  "a stray in the root must not change the choice")

            # Two in the folder: refuse, and name both.
            make_archive(os.path.join(exports, "course.second.tar.gz"), COURSE)
            try:
                cfg.resolve_tar_path()
                failures.append("two archives in the folder should raise")
            except FileNotFoundError as exc:
                for want in ("course.good.tar.gz", "course.second.tar.gz", "--tar"):
                    if want not in str(exc):
                        failures.append(f"the refusal should name {want}: {exc}")

            # Naming one explicitly always wins.
            check(failures, cfg.resolve_tar_path(stray) == stray,
                  "an explicit path should be returned unchanged")

            # So does the environment variable.
            os.environ["EDX_TAR_PATH"] = stray
            try:
                check(failures, cfg.resolve_tar_path() == stray,
                      "EDX_TAR_PATH should win over the folder")
            finally:
                del os.environ["EDX_TAR_PATH"]
        finally:
            os.chdir(cwd)
            importlib.reload(config)

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] course_exports/ wins; a stray elsewhere is reported, not used")
        print("  [PASS] two in one folder refuses and names both")
        print("  [PASS] --tar and EDX_TAR_PATH override the search")
    return not failures


if __name__ == "__main__":
    print("where the course export lives")
    raise SystemExit(0 if main() else 1)
