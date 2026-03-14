"""Compare current NotebookLM sources with newly extracted course content.

Usage:
    python compare_sources.py            # Generate comparison_review.json
    python compare_sources.py --apply    # Apply reviewed plan (delete + upload)
"""
import json
import os
import re
import subprocess
import sys
from difflib import SequenceMatcher

CURRENT_SOURCES_FILE = "current_sources.json"
MANIFEST_PATH = "processing_manifest.json"
REVIEW_PATH = "comparison_review.json"

STOP_WORDS = {"and", "the", "of", "in", "to", "a", "is", "for", "with", "on", "by", "an", "at", "or", "its"}
MATCH_THRESHOLD = 0.35


def load_current_sources():
    if not os.path.exists(CURRENT_SOURCES_FILE):
        print(f"Error: {CURRENT_SOURCES_FILE} not found.")
        print("Run export_current_sources.py first.")
        sys.exit(1)
    with open(CURRENT_SOURCES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    sources = data if isinstance(data, list) else data.get("sources", data.get("names", []))
    return [s for s in sources if s and isinstance(s, str)]


def load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        print(f"Error: {MANIFEST_PATH} not found. Run organize_content.py first.")
        sys.exit(1)
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
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
    if not file_path or not os.path.exists(file_path):
        return set()
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
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


# ---------------------------------------------------------------------------
# Generate review
# ---------------------------------------------------------------------------

def generate_review():
    current_sources = load_current_sources()
    manifest = load_manifest()

    # Flat, deduplicated list of new files
    new_files = []
    seen = set()
    for ch in manifest:
        for f in ch.get("files", []):
            key = (f["name"], f.get("path", ""))
            if key not in seen:
                seen.add(key)
                new_files.append({
                    "name": f["name"],
                    "path": f.get("path", ""),
                    "type": f.get("type", "unknown"),
                    "chapter": ch["chapter"],
                })

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
    current_only = []
    for cs in current_sources:
        if cs not in matched_old:
            current_only.append({"name": cs, "action": "KEEP"})

    pairs.sort(key=lambda p: -p["match_score"])
    current_only.sort(key=lambda c: c["name"].lower())

    review = {
        "_instructions": (
            "Review the matches below and set 'action' for each entry, then run:\n"
            "  python compare_sources.py --apply\n"
            "\n"
            "PAIRS  → REPLACE = delete old + upload new | DELETE = delete old only | KEEP = no change\n"
            "CURRENT_ONLY → DELETE = remove from notebook | KEEP = leave as-is\n"
            "NEW_ONLY     → ADD = upload to notebook | SKIP = don't upload"
        ),
        "pairs": pairs,
        "current_only": current_only,
        "new_only": new_only,
    }

    with open(REVIEW_PATH, "w", encoding="utf-8") as f:
        json.dump(review, f, indent=4, ensure_ascii=False)

    print(f"[OK] Comparison review saved to {REVIEW_PATH}")
    print(f"     {len(pairs)} matched pair(s)  (default: REPLACE)")
    print(f"     {len(new_only)} new-only source(s)  (default: ADD)")
    print(f"     {len(current_only)} existing-only source(s)  (default: KEEP)")
    print()
    print(">>> Edit actions in comparison_review.json, then run:")
    print(">>>   python compare_sources.py --apply")


# ---------------------------------------------------------------------------
# Apply reviewed plan
# ---------------------------------------------------------------------------

def apply_review():
    if not os.path.exists(REVIEW_PATH):
        print(f"Error: {REVIEW_PATH} not found. Run 'python compare_sources.py' first.")
        sys.exit(1)

    with open(REVIEW_PATH, "r", encoding="utf-8") as f:
        review = json.load(f)

    replace_n = sum(1 for p in review.get("pairs", []) if p.get("action", "").upper() == "REPLACE")
    delete_pair = sum(1 for p in review.get("pairs", []) if p.get("action", "").upper() == "DELETE")
    keep_pair = sum(1 for p in review.get("pairs", []) if p.get("action", "").upper() == "KEEP")
    delete_only = sum(1 for c in review.get("current_only", []) if c.get("action", "").upper() == "DELETE")
    keep_only = sum(1 for c in review.get("current_only", []) if c.get("action", "").upper() == "KEEP")
    add_n = sum(1 for n in review.get("new_only", []) if n.get("action", "").upper() == "ADD")
    skip_n = sum(1 for n in review.get("new_only", []) if n.get("action", "").upper() == "SKIP")

    total_delete = replace_n + delete_pair + delete_only
    total_upload = replace_n + add_n

    print("--- Applying reviewed comparison plan ---")
    print(f"  Pairs:        {replace_n} REPLACE, {delete_pair} DELETE, {keep_pair} KEEP")
    print(f"  Current-only: {delete_only} DELETE, {keep_only} KEEP")
    print(f"  New-only:     {add_n} ADD, {skip_n} SKIP")
    print(f"  → {total_delete} source(s) to delete, {total_upload} file(s) to upload")
    print()

    if total_delete > 0:
        print("--- Running delete_agent.py ---")
        result = subprocess.run([sys.executable, "delete_agent.py"], check=False)
        if result.returncode != 0:
            print(f"[WARN] delete_agent.py exited with code {result.returncode}")
    else:
        print("--- No sources to delete. ---")

    if total_upload > 0:
        print("\n--- Running upload_agent.py ---")
        result = subprocess.run([sys.executable, "upload_agent.py"], check=False)
        if result.returncode != 0:
            print(f"[WARN] upload_agent.py exited with code {result.returncode}")
    else:
        print("--- No files to upload. ---")

    print("\n--- Apply complete. ---")


if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply_review()
    else:
        generate_review()
