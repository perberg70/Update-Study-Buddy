"""Preflight checks for Update Study Buddy.

Usage:
    python preflight.py
"""

import shutil
import socket
import sys

from config import CDP_URL, resolve_tar_path


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
    try:
        path = resolve_tar_path()
        print(f"[OK] edX export detected: {path}")
        return True
    except Exception as exc:
        print(f"[WARN] No default edX export detected ({exc})")
        return False


def main() -> int:
    print("--- Update Study Buddy preflight ---")
    checks = [
        check_python(),
        check_playwright(),
        check_ffmpeg(),
        check_cdp_port(),
        check_tarball(),
    ]
    print("------------------------------------")
    if checks[0] and checks[1] and checks[2]:
        print("Preflight passed (core dependencies available).")
        return 0
    print("Preflight failed. Fix items marked [FAIL] and run again.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
