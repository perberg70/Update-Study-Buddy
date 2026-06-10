"""Compare current NotebookLM sources with newly extracted course content.

**Content model (important):**

- **Chapter text** — All HTML for a chapter is merged into **one** ``NN_ChapterName.txt`` per chapter.
  That is a single “text source” for the chapter body (not several competing chapter .txt files).

- **URLs** — Discovered in that merged text and stored under ``url_sources/*.txt`` as **link sources**
  (each file carries ``Source URL: https://…``). They are **not** extra chapter text files; they are
  separate NotebookLM **website/link** rows, one per URL.

- **Audio** — Each edX video becomes its own ``.mp3`` manifest row as before.

By default this script includes **merged chapter .txt + url_sources + MP3s** in the plan. Use
``--exclude-url-split-sources`` only if you want compare/upload to ignore ``url_sources/`` rows
(merged chapter + media only).

Usage:
    python compare_sources.py
    python compare_sources.py --apply
"""
import argparse
import json
import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from notebooklm_sources import is_notebook_placeholder_title, manifest_path_is_per_url_split

SCRIPT_DIR = Path(__file__).resolve().parent
CURRENT_SOURCES_FILE = SCRIPT_DIR / "current_sources.json"
MANIFEST_PATH = SCRIPT_DIR / "processing_manifest.json"
REVIEW_PATH = SCRIPT_DIR / "comparison_review.json"

STOP_WORDS = {"and", "the", "of", "in", "to", "a", "is", "for", "with", "on", "by", "an", "at", "or", "its"}
MATCH_THRESHOLD = 0.35


def load_current_sources(*, allow_empty: bool = False):
    """
    Notebook side of the compare comes only from export_current_sources.py → current_sources.json.

    If the export captured empty-state copy (\"Saved sources will appear here\"…) or the wrong
    panel, every placeholder is stripped here and the list becomes empty — then there is nothing
    to pair against **even if the notebook actually has many sources**. Re-export with scroll-merge
    (export script uses get_sidebar_panel_text_js) and Sources visible.
    """
    if not CURRENT_SOURCES_FILE.exists():
        print(f"Error: {CURRENT_SOURCES_FILE} not found.")
        print("Run export_current_sources.py first.")
        sys.exit(1)
    with open(CURRENT_SOURCES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    sources = data if isinstance(data, list) else data.get("sources", data.get("names", []))
    raw = [s for s in sources if s and isinstance(s, str)]
    out = [s for s in raw if not is_notebook_placeholder_title(s)]
    if not out and not allow_empty:
        print("Error: No real source titles in current_sources.json (only empty-state UI text or empty list).")
        print("compare_sources needs the exported NotebookLM sidebar list. If your notebook has sources,")
        print("open it in Chrome (CDP), show the Sources list, then run:  python export_current_sources.py")
        print("Or pass --allow-empty-notebook to treat everything as new-only (truly empty notebook).")
        sys.exit(1)
    return out


def load_manifest():
    if not MANIFEST_PATH.exists():
        print(f"Error: {MANIFEST_PATH} not found. Run organize_content.py first.")
        sys.exit(1)
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def normalize(s):
    """Lowercase, collapse separators, strip file extension."""
    if not s:
        return ""
    s = re.sub(r"\.[a-zA-Z0-9]{1,5}$", "", s)
    return re.sub(r"[_\s\-]+", " ", s.lower()).strip()


def significant_words(text):
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    return words - STOP_WORDS


def name_similarity(a, b):
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def word_overlap_score(words_a, words_b):
    if not words_a or not words_b:
        return 0.0
    overlap = len(words_a & words_b)
    return overlap / max(len(words_a), len(words_b))


def read_text_keywords(file_path, max_chars=4000):
    """Extract significant words from the beginning of a text file."""
    if not file_path:
        return set()
    p = Path(file_path)
    if not p.is_absolute():
        p = SCRIPT_DIR / p
    if not p.exists():
        return set()
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            return significant_words(f.read(max_chars))
    except Exception:
        return set()


def compute_match_score(new_file, old_name):
    """
    Score how well a new file matches an existing source (0.0 - 1.0).
    Combines name similarity, word overlap, chapter context, and content keywords.
    """
    new_name = new_file["name"]
    chapter = new_file.get("chapter", "")

    sim = name_similarity(new_name, old_name)

    new_words = significant_words(normalize(new_name))
    old_words = significant_words(normalize(old_name))
    w_overlap = word_overlap_score(new_words, old_words)

    ch_words = significant_words(chapter) if chapter else set()
    ch_overlap = word_overlap_score(ch_words, old_words) if ch_words else 0.0

    content_boost = 0.0
    if new_file.get("type") == "text" and new_file.get("path"):
        content_kw = read_text_keywords(new_file["path"])
        if content_kw and old_words:
            hits = len(old_words & content_kw)
            content_boost = min(hits / max(len(old_words), 1) * 0.15, 0.15)

    score = max(sim * 0.5 + w_overlap * 0.3 + ch_overlap * 0.05 + content_boost, sim)
    return round(min(score, 1.0), 3)


def match_reason(new_name, old_name, score):
    na, nb = normalize(new_name), normalize(old_name)
    if na == nb:
        return "exact name match"
    if na in nb or nb in na:
        return "name containment"
    if score >= 0.6:
        return "strong name/word similarity"
    if score >= MATCH_THRESHOLD:
        return "partial word overlap"
    return "weak match"


def _skipped_split_title_lookup(skipped_entries: list) -> dict:
    """Map manifest filename (with or without .txt) → chapter title for skipped url_sources rows."""
    m = {}
    for e in skipped_entries:
        n = e["name"]
        ch = e["chapter"]
        m[n] = ch
        if n.lower().endswith(".txt"):
            m[n[:-4]] = ch
    return m


def _chapter_for_panel_title(cs: str, lookup: dict) -> Optional[str]:
    if not lookup:
        return None
    if cs in lookup:
        return lookup[cs]
    ncs = normalize(cs)
    for k, ch in lookup.items():
        if normalize(k) == ncs:
            return ch
    return None


def _chapter_has_merged_text(new_files: list, chapter: str) -> bool:
    """True if manifest still has a non–url_sources text file for this chapter (merged chapter .txt)."""
    for nf in new_files:
        if nf.get("chapter") != chapter:
            continue
        if nf.get("type") != "text":
            continue
        if manifest_path_is_per_url_split(nf.get("path", "")):
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Generate review
# ---------------------------------------------------------------------------

def generate_review(*, include_url_splits: bool = True, allow_empty_notebook: bool = False):
    current_sources = load_current_sources(allow_empty=allow_empty_notebook)
    manifest = load_manifest()

    # Flat, deduplicated list of new files
    new_files = []
    seen = set()
    skipped_splits = 0
    skipped_entries: list = []
    for ch in manifest:
        for f in ch.get("files", []):
            path = f.get("path", "")
            if not include_url_splits and manifest_path_is_per_url_split(path):
                skipped_splits += 1
                skipped_entries.append(
                    {"chapter": ch["chapter"], "name": f["name"], "path": path}
                )
                continue
            key = (f["name"], path)
            if key not in seen:
                seen.add(key)
                new_files.append({
                    "name": f["name"],
                    "path": path,
                    "type": f.get("type", "unknown"),
                    "chapter": ch["chapter"],
                })
    if skipped_splits:
        print(
            f"[INFO] Skipped {skipped_splits} url_sources/ link row(s) "
            "(per-URL link sources). Merged chapter .txt + MP3s etc. only. "
            "Re-run without --exclude-url-split-sources to include those link sources."
        )

    # Match each new file to its best old source
    pairs = []
    matched_old = set()

    for nf in new_files:
        best_score = 0.0
        best_old = None
        for cs in current_sources:
            score = compute_match_score(nf, cs)
            if score > best_score:
                best_score = score
                best_old = cs

        if best_score >= MATCH_THRESHOLD and best_old:
            pairs.append({
                "new_name": nf["name"],
                "new_path": nf["path"],
                "new_type": nf["type"],
                "chapter": nf["chapter"],
                "old_name": best_old,
                "match_score": best_score,
                "match_reason": match_reason(nf["name"], best_old, best_score),
                "action": "REPLACE",
            })
            matched_old.add(best_old)

    # New files that didn't match anything
    paired_keys = {(p["new_name"], p["new_path"]) for p in pairs}
    new_only = []
    for nf in new_files:
        if (nf["name"], nf["path"]) not in paired_keys:
            new_only.append({
                "name": nf["name"],
                "path": nf["path"],
                "type": nf["type"],
                "chapter": nf["chapter"],
                "action": "ADD",
            })

    # Old sources not matched to any new file
    split_lookup = _skipped_split_title_lookup(skipped_entries) if skipped_entries else {}
    current_only = []
    superseded_delete = 0
    for cs in current_sources:
        if cs in matched_old:
            continue
        ch = _chapter_for_panel_title(cs, split_lookup) if split_lookup else None
        if (
            ch
            and _chapter_has_merged_text(new_files, ch)
        ):
            current_only.append(
                {
                    "name": cs,
                    "action": "DELETE",
                    "reason": (
                        "Per-URL link source; merged chapter .txt for this chapter is in the plan — "
                        "remove duplicate link rows (set to KEEP if you want to keep this link)."
                    ),
                }
            )
            superseded_delete += 1
        else:
            current_only.append({"name": cs, "action": "KEEP"})

    pairs.sort(key=lambda p: -p["match_score"])
    current_only.sort(key=lambda c: c["name"].lower())

    review = {
        "_instructions": (
            "Review the matches below and set 'action' for each entry, then run:\n"
            "  python compare_sources.py --apply\n"
            "\n"
            "The notebook side is current_sources.json from export_current_sources.py — not the manifest.\n"
            "If pairs/current_only are empty but the notebook has sources, re-run export (scroll-merge).\n"
            "\n"
            "PAIRS  → REPLACE = delete old + upload new | DELETE = delete old only | KEEP = no change\n"
            "CURRENT_ONLY → DELETE = remove from notebook | KEEP = leave as-is\n"
            "NEW_ONLY     → ADD = upload to notebook | SKIP = don't upload\n"
            "\n"
            "Chapter body = one merged .txt per chapter. URL rows = separate link sources (url_sources/), not\n"
            "multiple chapter texts. If you ran compare with --exclude-url-split-sources, see\n"
            "excluded_from_compare_manifest_entries for URL paths left out of pairs/new_only; notebook titles\n"
            "matching those may default to DELETE when a merged chapter .txt exists for that chapter."
        ),
        "pairs": pairs,
        "current_only": current_only,
        "new_only": new_only,
        "excluded_from_compare_manifest_entries": (
            [
                {
                    "chapter": e["chapter"],
                    "name": e["name"],
                    "path": e["path"],
                }
                for e in skipped_entries
            ]
            if (skipped_entries and not include_url_splits)
            else []
        ),
    }
    if skipped_entries and not include_url_splits:
        review["_note_excluded_manifest_entries"] = (
            "These url_sources/ link rows were excluded from pairs/new_only (--exclude-url-split-sources). "
            "Re-run compare without that flag to plan per-URL link sources alongside the merged chapter file."
        )
    if not current_sources:
        review["_notebook_export_warning"] = (
            "No notebook titles loaded (--allow-empty-notebook). Everything is new-only; this is not a "
            "full compare unless the notebook is truly empty."
        )

    with open(REVIEW_PATH, "w", encoding="utf-8") as f:
        json.dump(review, f, indent=4, ensure_ascii=False)

    print(f"[OK] Comparison review saved to {REVIEW_PATH.name}")
    print(f"     {len(pairs)} matched pair(s)  (default: REPLACE)")
    print(f"     {len(new_only)} new-only source(s)  (default: ADD)")
    print(
        f"     {len(current_only)} existing-only source(s)  "
        f"(default: KEEP, or DELETE for {superseded_delete} superseded per-URL split(s) when merged chapter exists)"
    )
    if skipped_entries and not include_url_splits:
        print(
            f"     Also: {len(skipped_entries)} manifest row(s) in excluded_from_compare_manifest_entries "
            f"(url_sources/ — re-run compare without --exclude-url-split-sources to add them)."
        )
    print()
    print(">>> Edit actions in comparison_review.json, then run:")
    print(">>>   python compare_sources.py --apply")


# ---------------------------------------------------------------------------
# Apply reviewed plan
# ---------------------------------------------------------------------------

def _action(item):
    return (item.get("action") or "").strip().upper()


def apply_review(*, include_url_splits: bool = True, skip_deletes: bool = False, skip_dedupe: bool = False):
    if not REVIEW_PATH.exists():
        print(f"Error: {REVIEW_PATH} not found. Run 'python compare_sources.py' first.")
        sys.exit(1)

    with open(REVIEW_PATH, encoding="utf-8") as f:
        review = json.load(f)

    replace_n = sum(1 for p in review.get("pairs", []) if _action(p) == "REPLACE")
    delete_pair = sum(1 for p in review.get("pairs", []) if _action(p) == "DELETE")
    keep_pair = sum(1 for p in review.get("pairs", []) if _action(p) == "KEEP")
    delete_only = sum(1 for c in review.get("current_only", []) if _action(c) == "DELETE")
    keep_only = sum(1 for c in review.get("current_only", []) if _action(c) == "KEEP")
    add_n = sum(1 for n in review.get("new_only", []) if _action(n) == "ADD")
    skip_n = sum(1 for n in review.get("new_only", []) if _action(n) == "SKIP")

    total_delete = replace_n + delete_pair + delete_only
    total_upload = replace_n + add_n

    print("--- Applying reviewed comparison plan ---")
    print(f"  Pairs:        {replace_n} REPLACE, {delete_pair} DELETE, {keep_pair} KEEP")
    print(f"  Current-only: {delete_only} DELETE, {keep_only} KEEP")
    print(f"  New-only:     {add_n} ADD, {skip_n} SKIP")
    print(f"  → {total_delete} source(s) to delete, {total_upload} file(s) to upload")
    print(f"  (using review file: {REVIEW_PATH})")
    print()
    if skip_deletes:
        print(
            "  Workflow: SKIP deletes  →  uploads only"
            + ("" if skip_dedupe else "  →  dedupe (optional)")
        )
    else:
        print(
            "  Workflow: (1) plan-driven deletes  →  (2) uploads  →  "
            "(3) dedupe panel (one row per title)"
        )
    print()

    apply_cwd = str(SCRIPT_DIR)

    needs_apply_steps = total_delete > 0 or total_upload > 0
    if skip_deletes and total_delete > 0:
        print(
            "--- Step 1: skip delete_agent (--skip-deletes) ---"
            f"\n       ({total_delete} planned delete(s) ignored; old sources stay in the notebook)"
        )
    elif needs_apply_steps and total_delete > 0:
        print(
            "--- Step 1: delete_agent.py (comparison_review — REPLACE / DELETE / current-only DELETE) ---"
        )
        result = subprocess.run(
            [sys.executable, "delete_agent.py"], cwd=apply_cwd, check=False
        )
        if result.returncode != 0:
            print(f"[FAIL] delete_agent.py exited with code {result.returncode}")
            print(
                "[HINT] Automated deletes often fail on current NotebookLM (text-only Sources panel).\n"
                "       Retry with uploads only:\n"
                "         python compare_sources.py --apply --skip-deletes"
            )
            sys.exit(result.returncode or 1)
    else:
        print("--- Step 1: skip delete_agent (no deletes in review) ---")

    if total_upload > 0:
        print("\n--- Step 2: upload_agent.py (add / replace uploads) ---")
        upload_cmd = [sys.executable, "upload_agent.py"]
        if not include_url_splits:
            upload_cmd.append("--exclude-url-split-sources")
        result = subprocess.run(upload_cmd, cwd=apply_cwd, check=False)
        if result.returncode != 0:
            print(f"[FAIL] upload_agent.py exited with code {result.returncode}")
            sys.exit(result.returncode or 1)
    else:
        print("\n--- Step 2: no files to upload. ---")

    if skip_dedupe:
        print("\n--- Step 3: skip dedupe (--skip-dedupe or --skip-deletes) ---")
    elif needs_apply_steps:
        print(
            "\n--- Step 3: delete_agent.py --dedupe ---"
            "\n       (remove extra rows so each source title appears once; e.g. 4 copies → keep 1)"
        )
        result = subprocess.run(
            [sys.executable, "delete_agent.py", "--dedupe"], cwd=apply_cwd, check=False
        )
        if result.returncode != 0:
            print(f"[WARN] delete_agent.py --dedupe exited with code {result.returncode}")
            print("[HINT] Dedupe also needs row menus; skip it when using --skip-deletes.")

    print("\n--- Apply complete. ---")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare NotebookLM to processing_manifest.json")
    parser.add_argument("--apply", action="store_true", help="Run delete → upload → dedupe from review file")
    parser.add_argument(
        "--exclude-url-split-sources",
        action="store_true",
        help="Omit url_sources/ link rows from compare and upload (keep merged chapter .txt + MP3s etc. only)",
    )
    parser.add_argument(
        "--include-url-split-sources",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--allow-empty-notebook",
        action="store_true",
        help="Allow compare when current_sources.json has no real titles after filtering placeholders",
    )
    parser.add_argument(
        "--skip-deletes",
        action="store_true",
        help="Skip delete_agent (upload new sources only; old notebook rows are left in place)",
    )
    parser.add_argument(
        "--skip-dedupe",
        action="store_true",
        help="Skip final delete_agent --dedupe pass",
    )
    args = parser.parse_args()
    if args.include_url_split_sources:
        print(
            "[WARN] --include-url-split-sources is unnecessary: url_sources/ link rows are included by default. "
            "Use --exclude-url-split-sources to omit per-URL link sources."
        )
    include_url_splits = not args.exclude_url_split_sources
    if args.apply:
        skip_dedupe = args.skip_dedupe or args.skip_deletes
        apply_review(
            include_url_splits=include_url_splits,
            skip_deletes=args.skip_deletes,
            skip_dedupe=skip_dedupe,
        )
    else:
        generate_review(
            include_url_splits=include_url_splits,
            allow_empty_notebook=args.allow_empty_notebook,
        )
