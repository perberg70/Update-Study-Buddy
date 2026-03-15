"""Shared configuration for Update Study Buddy scripts.

Environment variables can override defaults so operators don't need to edit code.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import Optional

PROJECT_URL = os.getenv(
    "NOTEBOOKLM_PROJECT_URL",
    "https://notebooklm.google.com/notebook/82c34a38-cbc5-47fe-8001-36696f67d7fb",
)
CDP_URL = os.getenv("NOTEBOOKLM_CDP_URL", "http://localhost:9222")

CURRENT_SOURCES_FILE = os.getenv("CURRENT_SOURCES_FILE", "current_sources.json")
MANIFEST_PATH = os.getenv("PROCESSING_MANIFEST_PATH", "processing_manifest.json")
REVIEW_PATH = os.getenv("COMPARISON_REVIEW_PATH", "comparison_review.json")

EXTRACT_DIR = os.getenv("EDX_EXTRACT_DIR", "edx_export")
ORGANIZED_CONTENT_DIR = os.getenv("ORGANIZED_CONTENT_DIR", "Organized_Course_Content")
COURSE_STRUCTURE_PATH = os.getenv("COURSE_STRUCTURE_PATH", "course_structure.json")

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "200"))
ENFORCE_UPLOAD_SIZE_LIMIT = os.getenv("ENFORCE_UPLOAD_SIZE_LIMIT", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def normalize_action(value: str) -> str:
    """Normalize human-edited action values from review JSON.

    Accepts loose inputs such as:
    - "delete", "DELETE ", "DELETE (old)", "remove"
    - "add", "upload"

    Supports whitespace, punctuation and common aliases so manual edits are
    interpreted reliably across scripts.
    """
    if value is None:
        return ""

    raw = str(value).strip().upper()
    aliases = {
        "REPLACE": "REPLACE",
        "REPLACED": "REPLACE",
        "DELETE": "DELETE",
        "REMOVE": "DELETE",
        "DEL": "DELETE",
        "KEEP": "KEEP",
        "ADD": "ADD",
        "UPLOAD": "ADD",
        "SKIP": "SKIP",
    }

    # Direct match first
    if raw in aliases:
        return aliases[raw]

    # Token-based match handles annotations/comments like
    # "DELETE (old source)" or "add - new".
    for token in re.findall(r"[A-Z_]+", raw):
        if token in aliases:
            return aliases[token]

    # Backward-compatible fallback
    cleaned = "".join(ch for ch in raw if ch.isalpha() or ch == "_")
    return aliases.get(cleaned, cleaned)


def resolve_tar_path(explicit_path: Optional[str] = None) -> str:
    """Resolve the course tarball path.

    Priority:
    1) explicit_path argument,
    2) EDX_TAR_PATH env var,
    3) newest file matching course*.tar.gz in cwd.
    """
    if explicit_path:
        return explicit_path

    from_env = os.getenv("EDX_TAR_PATH")
    if from_env:
        return from_env

    candidates = [Path(p) for p in glob.glob("course*.tar.gz") if Path(p).is_file()]
    if not candidates:
        raise FileNotFoundError(
            "No edX export found. Provide --tar, set EDX_TAR_PATH, or place course*.tar.gz in the project folder."
        )

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0])
