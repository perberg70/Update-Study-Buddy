"""Preflight checks for Update Study Buddy.

Usage:
    python preflight.py
"""

import os
import shutil
import socket
import subprocess
import sys

from config import CDP_URL, resolve_tar_path

# olx_archive is imported inside check_tarball, not here. On a checkout that
# predates it - main, say - a module-scope import raises ModuleNotFoundError
# before any check runs, so the one command that would have said "you are on
# the wrong branch" is the one command that cannot start. Checking the code
# must not depend on the code being complete.

REPO_DIR = os.path.dirname(os.path.abspath(__file__))


def _git(*args):
    """Run a git command in the repo. Returns stdout, or None if it failed."""
    try:
        result = subprocess.run(("git",) + args, cwd=REPO_DIR, capture_output=True,
                                check=False, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def check_repo_state() -> bool:
    """Say which version of this tool is about to run.

    Every other check here asks about the environment; this one asks about the
    code, which is the thing most likely to be wrong. The tooling lives on a
    feature branch, so `git checkout main` leaves a pipeline that is missing
    most of its scripts and says nothing about it.

    No fetch: a network call here could hang the one command you run to find
    out why things are hanging. The comparison is against the last-fetched
    ref, and the output says so.
    """
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        print("[WARN] Not a git checkout - cannot tell which version this is.")
        return True

    head = _git("rev-parse", "--short", "HEAD") or "?"
    print(f"[OK] Code: branch {branch} at {head}")

    dirty = _git("status", "--porcelain")
    if dirty:
        count = len(dirty.splitlines())
        print(f"     [!] {count} uncommitted change(s) - `git pull` may refuse or merge.")

    upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream is None:
        # This is how "I ran git pull and nothing happened" happens.
        print(f"     [FAIL] {branch} tracks no remote branch, so `git pull` does")
        print(f"            nothing. Fix: git pull origin {branch}")
        return False

    behind = _git("rev-list", "--count", "HEAD..@{u}")
    if behind and behind != "0":
        print(f"     [!] {behind} commit(s) behind {upstream} as of the last fetch.")
        print(f"         Run: git pull origin {branch}")
    else:
        print(f"     up to date with {upstream} as of the last fetch")
    return True


def check_python() -> bool:
    ok = sys.version_info >= (3, 9)
    print(f"[{'OK' if ok else 'FAIL'}] Python {sys.version.split()[0]} (requires >=3.9)")
    return ok


def check_ffmpeg() -> bool:
    ok = shutil.which("ffmpeg") is not None
    print(f"[{'OK' if ok else 'FAIL'}] ffmpeg {'found' if ok else 'not found on PATH'}")
    return ok


def check_playwright() -> bool:
    try:
        import playwright  # noqa: F401

        print("[OK] playwright package available")
        return True
    except Exception:
        print("[FAIL] playwright package missing (run: pip install playwright)")
        return False


# (import name, pip name, what it enables). Each is genuinely optional: the
# pipeline's core path needs none of them, and a missing one is reported as a
# command to run rather than discovered mid-run as a traceback.
OPTIONAL_DEPS = (
    ("reportlab", "reportlab", "module PDFs (tools/build_module_pdf.py)"),
    ("youtube_transcript_api", "youtube-transcript-api",
     "YouTube captions (tools/fetch_youtube_transcripts.py)"),
    ("faster_whisper", "faster-whisper",
     "local transcription (tools/transcribe_videos.py)"),
)


def check_optional_deps() -> bool:
    """Report the per-feature packages before a run needs them.

    Always returns True - these do not gate preflight. The point is that a
    missing one is visible here, with its install command, instead of surfacing
    as a ModuleNotFoundError partway through a job.
    """
    missing = []
    for module, package, enables in OPTIONAL_DEPS:
        try:
            __import__(module)
        except Exception:
            missing.append((package, enables))
        else:
            print(f"[OK] {package} available - {enables}")

    for package, enables in missing:
        print(f"[WARN] {package} missing - {enables}")
        print(f"       pip install {package}")
    return True


def check_cdp_port() -> bool:
    host_port = CDP_URL.removeprefix("http://")
    host, _, port_str = host_port.partition(":")
    port = int(port_str or "9222")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        ok = sock.connect_ex((host, port)) == 0
    print(f"[{'OK' if ok else 'WARN'}] CDP endpoint {CDP_URL} {'reachable' if ok else 'not reachable'}")
    return ok


def check_tarball() -> bool:
    """Confirm the export is not just present but readable as an OLX course.

    Opening it here costs one pass over the archive and turns "course.xml not
    found" - which used to surface mid-run - into a preflight answer.
    """
    try:
        from olx_archive import CourseArchive, CourseArchiveError
    except ImportError:
        print("[WARN] olx_archive.py is missing from this checkout - the export")
        print("       cannot be read. See the branch reported above.")
        return False

    try:
        path = resolve_tar_path()
    except Exception as exc:
        print(f"[WARN] No default edX export detected ({exc})")
        return False

    try:
        archive = CourseArchive(path)
    except CourseArchiveError as exc:
        print(f"[FAIL] {path} is not a readable course archive: {exc}")
        return False

    fingerprint = archive.fingerprint()
    print(f"[OK] edX export readable: {path}")
    print(f"     sha:{fingerprint['sha256_head']}, course root: "
          f"{fingerprint['course_root']}, {fingerprint['files_in_archive']} file(s)")
    return True


def main() -> int:
    print("--- Update Study Buddy preflight ---")
    # Named, not positional: the pass condition used to index into this list, so
    # inserting a check silently changed which ones were required.
    results = {
        "code": check_repo_state(),
        "python": check_python(),
        "playwright": check_playwright(),
        "ffmpeg": check_ffmpeg(),
        "optional": check_optional_deps(),
        "cdp": check_cdp_port(),
        "export": check_tarball(),
    }
    # The CDP port, the export and the per-feature packages are each needed by
    # only some steps, so none fails preflight on its own - all report [WARN].
    required = ("code", "python", "playwright", "ffmpeg")

    print("------------------------------------")
    failed = [name for name in required if not results[name]]
    if not failed:
        print("Preflight passed (core dependencies available).")
        return 0
    print(f"Preflight failed: {', '.join(failed)}. Fix items marked [FAIL] and run again.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
