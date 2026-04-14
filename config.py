"""Shared paths and env overrides for Update Study Buddy."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent

# NotebookLM + Playwright CDP (export_current_sources, read/delete agents, etc.)
PROJECT_URL = os.getenv(
    "NOTEBOOKLM_PROJECT_URL",
    "https://notebooklm.google.com/notebook/82c34a38-cbc5-47fe-8001-36696f67d7fb",
)
# Use 127.0.0.1 (not "localhost") so Playwright/Node does not prefer IPv6 (::1) while Chrome
# remote debugging typically listens on IPv4 loopback only on Windows.
CDP_URL = os.getenv("NOTEBOOKLM_CDP_URL", "http://127.0.0.1:9222")
CURRENT_SOURCES_FILE = os.getenv("CURRENT_SOURCES_FILE", "current_sources.json")

EXTRACT_DIR = os.getenv("EDX_EXTRACT_DIR", "edx_export")
ORGANIZED_CONTENT_DIR = os.getenv("ORGANIZED_CONTENT_DIR", "Organized_Course_Content")


def resolve_tar_path(explicit_path: Optional[str] = None) -> str:
    """Resolve the edX course `.tar.gz` path.

    Priority:
    1) explicit_path (CLI ``--tar``),
    2) ``EDX_TAR_PATH`` environment variable,
    3) newest file matching ``course*.tar.gz`` under the project directory (next to this file).
    """
    if explicit_path:
        p = Path(explicit_path)
        if not p.is_absolute():
            p = SCRIPT_DIR / p
        if not p.is_file():
            raise FileNotFoundError(f"Archive not found: {p}")
        return str(p)

    from_env = os.getenv("EDX_TAR_PATH")
    if from_env:
        p = Path(from_env)
        if not p.is_absolute():
            p = SCRIPT_DIR / p
        if not p.is_file():
            raise FileNotFoundError(f"EDX_TAR_PATH not found: {p}")
        return str(p)

    candidates = sorted(
        (p for p in SCRIPT_DIR.glob("course*.tar.gz") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            "No edX export found. Use: python extract_edx.py --tar path/to/course.tar.gz, "
            "set EDX_TAR_PATH, or place a file named course*.tar.gz in the project folder."
        )
    return str(candidates[0])
