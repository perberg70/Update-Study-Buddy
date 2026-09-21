"""Shared configuration for Update Study Buddy scripts.

Environment variables can override defaults so operators don't need to edit code.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import Optional

# NotebookLM was rebranded to "Gemini Notebook" and moved to notebook.google.com.
# notebooklm.google.com still redirects, but pointing here directly avoids relying on it.
PROJECT_URL = os.getenv(
    "NOTEBOOKLM_PROJECT_URL",
    "https://notebook.google.com/notebook/82c34a38-cbc5-47fe-8001-36696f67d7fb",
)

# Hosts that count as "the notebook" when choosing which browser tab to drive.
NOTEBOOK_HOSTS = ("notebook.google.com", "notebooklm.google.com")
CDP_URL = os.getenv("NOTEBOOKLM_CDP_URL", "http://localhost:9222")

CURRENT_SOURCES_FILE = os.getenv("CURRENT_SOURCES_FILE", "current_sources.json")
MANIFEST_PATH = os.getenv("PROCESSING_MANIFEST_PATH", "processing_manifest.json")
REVIEW_PATH = os.getenv("COMPARISON_REVIEW_PATH", "comparison_review.json")

# Transcripts arrive from several places - exported from Teams by hand, produced
# by local speech-to-text, or shipped inside the OLX - so they share one store
# keyed by the video's url_name. Anything dropped here is picked up by
# tools/build_module_pdf.py without further configuration.
TRANSCRIPTS_DIR = os.getenv("TRANSCRIPTS_DIR", "transcripts")
ORGANIZED_CONTENT_DIR = os.getenv("ORGANIZED_CONTENT_DIR", "Organized_Course_Content")
COURSE_STRUCTURE_PATH = os.getenv("COURSE_STRUCTURE_PATH", "course_structure.json")

# Video downloads. A course export can hold 10+ hours of webinar recordings, so
# a stalled connection must fail rather than hang the whole run unattended.
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "60"))
DOWNLOAD_RETRIES = int(os.getenv("DOWNLOAD_RETRIES", "3"))

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "200"))
ENFORCE_UPLOAD_SIZE_LIMIT = os.getenv("ENFORCE_UPLOAD_SIZE_LIMIT", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


# A negated action is ambiguous, and the token scan below would resolve it to the
# opposite of what was written: "DO NOT DELETE" once returned DELETE. Anything
# matching this is handed back unchanged so validation rejects the row by name.
NEGATION_RE = re.compile(r"\b(NOT|NEVER|NO|DONT|DOESNT)\b|N['\u2019]T", re.I)


def normalize_action(value: str) -> str:
    """Normalize human-edited action values from review JSON.

    Accepts loose inputs such as:
    - "delete", "DELETE ", "DELETE (old)", "remove"
    - "add", "upload"

    Refuses to guess at negations ("do not delete", "never remove"): those come
    back unchanged and fail validation, so the operator is asked rather than
    silently given the opposite of what they wrote.
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

    # Direct match first: an unambiguous bare action word always wins.
    if raw in aliases:
        return aliases[raw]

    # Never guess past a negation - returning the input unchanged makes it an
    # invalid action, which apply_review reports with the row number.
    if NEGATION_RE.search(raw):
        return str(value).strip()

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
