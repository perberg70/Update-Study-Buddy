"""Shared paths and env overrides for Update Study Buddy."""

from __future__ import annotations

import os
import re
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

# edX course component dirs that must never live under Organized_Course_Content/
EDX_COMPONENT_DIR_NAMES = frozenset(
    {
        "1T2026",
        "about",
        "assets",
        "chapter",
        "course",
        "drafts",
        "html",
        "info",
        "policies",
        "problem",
        "sequential",
        "static",
        "tabs",
        "vertical",
        "video",
    }
)


def _slugify_title_text(text: str) -> str:
    """Turn display title fragment into a safe folder segment."""
    text = text.strip()
    text = re.sub(r"\(\+", "(", text)  # (+Ethics…) → (Ethics…)
    text = re.sub(r"[()]", " ", text)
    clean = re.sub(r"[^a-zA-Z0-9]", "_", text)
    return re.sub(r"_+", "_", clean).strip("_")


def chapter_dir_slug(chapter_index: int, display_title: str) -> str:
    """Stable chapter folder name aligned with the edX course home page.

    Examples (match course outline numbering):

    - ``1. Welcome & What GenAI Can Do Today`` → ``01_Welcome_What_GenAI_Can_Do_Today``
    - ``3.A Track: Business/Industry`` → ``03A_Track_Business_Industry``
    - ``4. What AI is (+Ethics of generative AI)`` → ``04_What_AI_is_Ethics_of_generative_AI``
    - ``Final seminar - Dec 10`` → ``09_Final_seminar_Dec_10`` (fallback index when no ``N.`` prefix)
    """
    title = display_title.strip()

    track = re.match(r"^(\d+)\.([A-Za-z])\s*Track:\s*(.+)$", title, re.IGNORECASE)
    if track:
        num = int(track.group(1))
        letter = track.group(2).upper()
        return f"{num:02d}{letter}_Track_{_slugify_title_text(track.group(3))}"

    numbered = re.match(r"^(\d+)\.\s*(.+)$", title)
    if numbered:
        num = int(numbered.group(1))
        return f"{num:02d}_{_slugify_title_text(numbered.group(2))}"

    return f"{chapter_index:02d}_{_slugify_title_text(title)}"


def expected_chapter_dir_names(chapters: list[dict]) -> set[str]:
    """Folder names ``organize_content.py`` will create for the given course chapters."""
    return {chapter_dir_slug(i + 1, ch["title"]) for i, ch in enumerate(chapters)}


def manifest_relpath(path: os.PathLike[str] | str) -> str:
    """Project-relative manifest path using forward slashes (portable JSON)."""
    p = Path(path)
    if not p.is_absolute():
        p = SCRIPT_DIR / p
    return p.relative_to(SCRIPT_DIR).as_posix()


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


def chapter_heading_prefix(chapter_display: str) -> str:
    """NotebookLM source prefix from edX chapter title.

    Examples:
        ``1. Welcome & What GenAI Can Do Today`` → ``1 Welcome & What GenAI Can Do Today``
        ``3.A Track: Business/Industry`` → ``3A Track Business/Industry``
    """
    title = (chapter_display or "").strip()
    track = re.match(r"^(\d+)\.([A-Za-z])\s*Track:\s*(.+)$", title, re.IGNORECASE)
    if track:
        num, letter, rest = track.group(1), track.group(2).upper(), track.group(3).strip()
        return f"{num}{letter} Track {rest}"
    numbered = re.match(r"^(\d+)\.\s*(.+)$", title)
    if numbered:
        return f"{numbered.group(1)} {numbered.group(2).strip()}"
    return title


def _short_source_label(source_name: str, file_path: str = "") -> str:
    """Filename or URL row → short label for the suffix after the chapter prefix."""
    name = (source_name or "").strip()
    path = Path(file_path) if file_path else None

    if path and path.suffix:
        if path.name.lower().endswith(".txt") and "url_sources" in str(path).replace("\\", "/").lower():
            stem = re.sub(r"^ch\d+_\d+_", "", path.stem, flags=re.I)
            return stem[:120]
        return path.name

    if name.startswith(("http://", "https://")):
        if path and path.stem:
            return re.sub(r"^ch\d+_\d+_", "", path.stem, flags=re.I)[:120]
        return name[:120]

    return name[:120]


def notebook_source_display_name(chapter_display: str, source_name: str, *, file_path: str = "") -> str:
    """Full NotebookLM source title: ``<chapter #> <title> - <source label>``."""
    prefix = chapter_heading_prefix(chapter_display)
    label = _short_source_label(source_name, file_path)
    full = f"{prefix} - {label}"
    if label.startswith(prefix + " - ") or label == prefix:
        return label
    return full


def safe_upload_filename(display_name: str, *, fallback_ext: str = "") -> str:
    """Sanitize a display name for a temporary upload file on Windows."""
    name = re.sub(r'[<>:"/\\|?*]', "_", display_name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = "source"
    if fallback_ext and not Path(name).suffix:
        name = name + fallback_ext
    return name[:200]
