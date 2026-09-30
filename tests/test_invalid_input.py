#!/usr/bin/env python3
"""Test invalid input validation and error logging for course exports."""

import logging
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from extract_edx import parse_course
from olx_archive import CourseArchive, CourseArchiveError
from tests._fixtures import make_archive


def check(failures, condition, message):
    if not condition:
        failures.append(message)


def test_non_existent_and_invalid_paths():
    failures = []
    # Test None / non-string path
    try:
        CourseArchive(None)
        failures.append("CourseArchive(None) should have raised CourseArchiveError")
    except CourseArchiveError:
        pass

    # Test non-existent file
    try:
        CourseArchive("/tmp/non_existent_export_archive_123456.tar.gz")
        failures.append("Non-existent archive path should have raised CourseArchiveError")
    except CourseArchiveError as exc:
        check(failures, "not found" in str(exc), f"Unexpected message: {exc}")

    # Test directory path instead of file
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            CourseArchive(tmpdir)
            failures.append("Directory path should have raised CourseArchiveError")
        except CourseArchiveError as exc:
            check(failures, "is not a file" in str(exc), f"Unexpected message: {exc}")

    return failures


def test_empty_and_corrupt_tar_files():
    failures = []
    with tempfile.TemporaryDirectory() as tmpdir:
        empty_file = os.path.join(tmpdir, "empty.tar.gz")
        open(empty_file, "wb").close()

        try:
            CourseArchive(empty_file)
            failures.append("Empty archive file should have raised CourseArchiveError")
        except CourseArchiveError as exc:
            check(failures, "cannot read" in str(exc) or "contains no files" in str(exc),
                  f"Unexpected error message for empty file: {exc}")

        corrupt_file = os.path.join(tmpdir, "corrupt.tar.gz")
        with open(corrupt_file, "wb") as fh:
            fh.write(b"not a gzip or tar archive")

        try:
            CourseArchive(corrupt_file)
            failures.append("Corrupt archive file should have raised CourseArchiveError")
        except CourseArchiveError as exc:
            check(failures, "cannot read" in str(exc), f"Unexpected error message: {exc}")

    return failures


def test_missing_and_malformed_xml():
    failures = []
    with tempfile.TemporaryDirectory() as tmpdir:
        # Missing course.xml
        tar1 = os.path.join(tmpdir, "no_course_xml.tar.gz")
        make_archive(tar1, {"other.txt": "hello"})
        try:
            CourseArchive(tar1)
            failures.append("Missing course.xml should have raised CourseArchiveError")
        except CourseArchiveError as exc:
            check(failures, "no course.xml in the archive" in str(exc),
                  f"Unexpected error message: {exc}")

        # course.xml missing url_name
        tar2 = os.path.join(tmpdir, "missing_url_name.tar.gz")
        make_archive(tar2, {"course.xml": "<course/>"})
        archive2 = CourseArchive(tar2)
        try:
            parse_course(archive2)
            failures.append("Missing url_name in course.xml should have raised CourseArchiveError")
        except CourseArchiveError as exc:
            check(failures, "missing required 'url_name'" in str(exc),
                  f"Unexpected error message: {exc}")

        # course.xml points to non-existent course file
        tar3 = os.path.join(tmpdir, "missing_course_root.tar.gz")
        make_archive(tar3, {"course.xml": '<course url_name="c1"/>'})
        archive3 = CourseArchive(tar3)
        try:
            parse_course(archive3)
            failures.append("Missing referenced course/c1.xml should have raised CourseArchiveError")
        except CourseArchiveError as exc:
            check(failures, "course/c1.xml not found" in str(exc),
                  f"Unexpected error message: {exc}")

        # malformed course.xml
        tar4 = os.path.join(tmpdir, "malformed.tar.gz")
        make_archive(tar4, {"course.xml": "<course url_name='c1'"})
        archive4 = CourseArchive(tar4)
        try:
            parse_course(archive4)
            failures.append("Malformed course.xml should have raised CourseArchiveError")
        except CourseArchiveError as exc:
            check(failures, "course.xml is missing or unreadable" in str(exc),
                  f"Unexpected error message: {exc}")

    return failures


def test_logging_triggers(caplog=None):
    failures = []
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create an archive with missing child referenced XML to trigger logging warnings
        tar = os.path.join(tmpdir, "missing_children.tar.gz")
        files = {
            "course.xml": '<course url_name="run1"/>',
            "course/run1.xml": '<course><chapter url_name="ch1"/></course>',
            "chapter/ch1.xml": '<chapter display_name="Ch 1"><sequential url_name="seq1"/></chapter>',
            # sequential/seq1.xml is missing!
        }
        make_archive(tar, files)
        archive = CourseArchive(tar)
        parsed = parse_course(archive)
        check(failures, len(parsed["chapters"]) == 1, "Expected 1 chapter parsed")
        check(failures, len(parsed["chapters"][0]["sequentials"]) == 1,
              "Expected 1 sequential in chapter despite missing file")

    return failures


def main():
    logging.basicConfig(level=logging.INFO)
    all_failures = []
    all_failures.extend(test_non_existent_and_invalid_paths())
    all_failures.extend(test_empty_and_corrupt_tar_files())
    all_failures.extend(test_missing_and_malformed_xml())
    all_failures.extend(test_logging_triggers())

    if all_failures:
        print(f"[FAIL] test_invalid_input.py ({len(all_failures)} failure(s)):")
        for fail in all_failures:
            print(f"  - {fail}")
        return False

    print("[PASS] test_invalid_input.py")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
