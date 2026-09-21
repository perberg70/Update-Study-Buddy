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


def exports_dir_files(exports):
    return [os.path.join(exports, n) for n in os.listdir(exports)
            if n.endswith(".tar.gz")]


def reload_config(project, exports):
    """config reads its directories at import time, so rebind them."""
    config.PROJECT_DIR = Path(project)
    config.EXPORTS_DIR = Path(exports)
    return config


def main():
    failures = []

    # edXUpdater builds an import archive named exactly course.tar.gz, which
    # course*.tar.gz matches and CourseArchive accepts - so it would be used to
    # build study material from the wrong tree, silently.
    for name, expect_warning, why in [
        ("course.tar.gz", True, "edXUpdater's exact output name"),
        ("COURSE.TAR.GZ", True, "case must not evade it"),
        ("course.hp_m6v88.tar.gz", False, "a Studio export carries a run id"),
        ("course.6gehwzol.tar.gz", False, "another real export name"),
        ("courses.tar.gz", False, "a different name entirely"),
        (os.path.join("a", "b", "course.tar.gz"), True, "matched on basename"),
    ]:
        got = bool(config.looks_like_import_archive(name))
        check(failures, got == expect_warning,
              f"looks_like_import_archive({name!r}): {why}")
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

            # A bare course.tar.gz warns but must remain usable: the name is
            # evidence, not proof, and an export may have been renamed.
            for stale in exports_dir_files(exports):
                os.remove(stale)
            imported = make_archive(os.path.join(exports, "course.tar.gz"), COURSE)
            check(failures, os.path.samefile(cfg.resolve_tar_path(), imported),
                  "a bare course.tar.gz must still resolve, not be refused")
            check(failures, bool(cfg.looks_like_import_archive(imported)),
                  "...but it must be flagged")
            os.remove(imported)
            good = make_archive(os.path.join(exports, "course.good.tar.gz"), COURSE)

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
        print("  [PASS] edXUpdater's course.tar.gz is flagged but still usable")
    return not failures


if __name__ == "__main__":
    print("where the course export lives")
    raise SystemExit(0 if main() else 1)
