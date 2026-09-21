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

from config import CURRENT_SOURCES_FILE, MANIFEST_PATH, REVIEW_PATH
import config as app_config

STOP_WORDS = {"and", "the", "of", "in", "to", "a", "is", "for", "with", "on", "by", "an", "at", "or", "its"}

# Below MATCH_THRESHOLD a pair is not written at all. Between the two, the pair is
# written but defaults to KEEP - visible as a suggestion, not acted on. At or above
# HIGH_CONFIDENCE it defaults to REPLACE. An exact name match scores ~0.8, so real
# replacements still default correctly while near-misses need a deliberate edit.
MATCH_THRESHOLD = 0.35
HIGH_CONFIDENCE = 0.75

# Sources uploaded by an earlier workflow carry the chapter title as a prefix:
#   "2 Learning with AI - Learning_Mode_Short_Demo.mp3"
# while organize_content.py generates just "Learning_Mode_Short_Demo.mp3". The
# blended score dilutes that to ~0.6, because the prefix contributes words the
# generated name cannot have - measured on the real notebook, 0 of 28 files
# cleared HIGH_CONFIDENCE despite 23 matching correctly. Recognising the
# structure directly is more precise than trying to tune the blend around it.
SUFFIX_MATCH_SCORE = 0.9
MIN_SUFFIX_WORDS = 3

# Unmatched rows carry a pointer to their nearest counterpart, purely as context
# for the human reviewer. Below this the "nearest" is meaningless and would be
# noise across a hundred-odd unmatched web links, so it is omitted. A display
# threshold only: being slightly off costs a missing or surplus hint, never an
# action. Calibrated on real data, where true re-recordings scored 0.29-0.33 and
# the nearest unrelated pair scored 0.22.
CROSS_REF_FLOOR = 0.25


def load_current_sources():
    if not os.path.exists(CURRENT_SOURCES_FILE):
        print(f"Error: {CURRENT_SOURCES_FILE} not found.")
        print("Run export_current_sources.py first.")
        sys.exit(1)
    with open(CURRENT_SOURCES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    sources = data if isinstance(data, list) else data.get("sources", data.get("names", []))

    # De-duplicate early so comparison_review.json has one row per source name.
    unique = []
    seen = set()
    for source in sources:
        if not source or not isinstance(source, str):
            continue
        key = normalize(source)
        if key and key not in seen:
            seen.add(key)
            unique.append(source)
    return unique


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


def is_title_suffix(new_name, old_name):
    """True when *old_name* is *new_name* carrying a prefix.

    Requires at least MIN_SUFFIX_WORDS significant words so a short generic
    name ("Video.mp3") cannot suffix-match many unrelated titles, and requires
    a word boundary so "demo.mp3" does not match "...short_demo.mp3".
    """
    na, nb = normalize(new_name), normalize(old_name)
    if not na or not nb or na == nb:
        return False
    if len(significant_words(na)) < MIN_SUFFIX_WORDS:
        return False
    return nb.endswith(" " + na)


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

    # Weighted score only. This used to be max(weighted, sim), which made the two
    # signals independent triggers rather than one judgement: raw character
    # similarity alone could pair names with no words in common.
    score = sim * 0.5 + w_overlap * 0.3 + ch_overlap * 0.05 + content_boost

    # Unlike the removed max(weighted, sim), this is not a general similarity
    # escape hatch: it fires only when the existing title is exactly this
    # filename plus a prefix.
    if is_title_suffix(new_name, old_name):
        # Keep the chapter signal on top rather than flattening to a constant:
        # the same video can appear in two chapters ("3A Track ..." and
        # "3B Track Academia"), and both suffix-match. Without this the scores
        # tie and the pairing is decided by manifest order rather than by which
        # chapter the file actually came from.
        score = max(score, SUFFIX_MATCH_SCORE + ch_overlap * 0.05)

    return round(min(score, 1.0), 3)


def match_reason(new_name, old_name, score):
    na, nb = normalize(new_name), normalize(old_name)
    if na == nb:
        return "exact name match"
    if is_title_suffix(new_name, old_name):
        return "filename matches title suffix (chapter prefix)"
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

    # Compute all candidate matches, then greedily assign one-to-one pairs
    # so neither side appears duplicated in comparison_review.json.
    candidates = []
    for nf in new_files:
        for cs in current_sources:
            score = compute_match_score(nf, cs)
            if score >= MATCH_THRESHOLD:
                candidates.append((score, nf, cs))

    candidates.sort(key=lambda c: c[0], reverse=True)

    pairs = []
    matched_old = set()
    matched_new = set()
    for score, nf, cs in candidates:
        new_key = (nf["name"], nf["path"])
        if new_key in matched_new or cs in matched_old:
            continue
        pairs.append({
            "new_name": nf["name"],
            "new_path": nf["path"],
            "new_type": nf["type"],
            "chapter": nf["chapter"],
            "old_name": cs,
            "match_score": score,
            "match_reason": match_reason(nf["name"], cs, score),
            "action": "REPLACE" if score >= HIGH_CONFIDENCE else "KEEP",
        })
        matched_new.add(new_key)
        matched_old.add(cs)

    # New files that didn't match anything.
    #
    # Each row records its closest existing source even though the score was too
    # low to pair. A re-recording under a new naming scheme scores near zero
    # against its predecessor - "HI_gen_AI_VT26_Webinar_5.mp3" versus
    # "Webinar_5_Driving_Change.mp3" shares almost no vocabulary - so no safe
    # automatic rule can find it. Naming the nearest candidate puts the
    # information where the decision is made, without acting on it.
    paired_keys = {(p["new_name"], p["new_path"]) for p in pairs}
    new_only = []
    unmatched_old = [c for c in current_sources if c not in matched_old]
    for nf in new_files:
        if (nf["name"], nf["path"]) in paired_keys:
            continue
        best_name, best_score = None, 0.0
        for cs in unmatched_old:
            score = compute_match_score(nf, cs)
            if score > best_score:
                best_name, best_score = cs, score
        row = {
            "name": nf["name"],
            "path": nf["path"],
            "type": nf["type"],
            "chapter": nf["chapter"],
            "action": "ADD",
        }
        if best_name and best_score >= CROSS_REF_FLOOR:
            row["closest_existing"] = best_name
            row["closest_score"] = best_score
            row["_hint"] = (
                "Too different to pair automatically. If this replaces "
                f"{best_name!r}, set that row in current_only to DELETE."
            )
        new_only.append(row)

    # Old sources not matched to any new file, with the same cross-reference
    # from the other direction.
    unmatched_new = [nf for nf in new_files
                     if (nf["name"], nf["path"]) not in paired_keys]
    current_only = []
    for cs in current_sources:
        if cs in matched_old:
            continue
        best_name, best_score = None, 0.0
        for nf in unmatched_new:
            score = compute_match_score(nf, cs)
            if score > best_score:
                best_name, best_score = nf["name"], score
        row = {"name": cs, "action": "KEEP"}
        if best_name and best_score >= CROSS_REF_FLOOR:
            row["closest_new_file"] = best_name
            row["closest_score"] = best_score
        current_only.append(row)

    pairs.sort(key=lambda p: -p["match_score"])
    current_only.sort(key=lambda c: c["name"].lower())

    review = {
        "_instructions": (
            "Review the matches below and set 'action' for each entry, then run:\n"
            "  python compare_sources.py --apply\n"
            "\n"
            f"PAIRS defaulting to KEEP scored below {HIGH_CONFIDENCE}: check them and change to\n"
            "REPLACE if the match is right. A wrong REPLACE deletes a source permanently.\n"
            "\n"
            "PAIRS        -> REPLACE = delete old + upload new | DELETE = delete old only | KEEP = no change\n"
            "CURRENT_ONLY -> DELETE = remove from notebook | KEEP = leave as-is\n"
            "NEW_ONLY     -> ADD = upload to notebook | SKIP = don't upload"
        ),
        "pairs": pairs,
        "current_only": current_only,
        "new_only": new_only,
    }

    with open(REVIEW_PATH, "w", encoding="utf-8") as f:
        json.dump(review, f, indent=4, ensure_ascii=False)

    high = sum(1 for p in pairs if p["match_score"] >= HIGH_CONFIDENCE)
    print(f"[OK] Comparison review saved to {REVIEW_PATH}")
    print(f"     {len(pairs)} matched pair(s): {high} default REPLACE (score >= {HIGH_CONFIDENCE}),")
    print(f"       {len(pairs) - high} default KEEP (lower confidence - review and promote)")
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

    allowed_actions = {
        "pairs": {"REPLACE", "DELETE", "KEEP"},
        "current_only": {"DELETE", "KEEP"},
        "new_only": {"ADD", "SKIP"},
    }

    invalid_rows = []
    normalized = {"pairs": [], "current_only": [], "new_only": []}
    for section, allowed in allowed_actions.items():
        for idx, row in enumerate(review.get(section, []), start=1):
            action = app_config.normalize_action(row.get("action", ""))
            normalized[section].append(action)
            if action not in allowed:
                invalid_rows.append((section, idx, row.get("action", ""), sorted(allowed)))

    if invalid_rows:
        print("[FAIL] Invalid action(s) found in comparison_review.json:")
        for section, idx, raw, allowed in invalid_rows:
            print(f"  - {section}[{idx}]: {raw!r} (allowed: {', '.join(allowed)})")
        print("[FAIL] Aborting apply step. Fix actions in comparison_review.json and rerun.")
        sys.exit(1)

    replace_n = sum(1 for a in normalized["pairs"] if a == "REPLACE")
    delete_pair = sum(1 for a in normalized["pairs"] if a == "DELETE")
    keep_pair = sum(1 for a in normalized["pairs"] if a == "KEEP")
    delete_only = sum(1 for a in normalized["current_only"] if a == "DELETE")
    keep_only = sum(1 for a in normalized["current_only"] if a == "KEEP")
    add_n = sum(1 for a in normalized["new_only"] if a == "ADD")
    skip_n = sum(1 for a in normalized["new_only"] if a == "SKIP")

    total_delete = replace_n + delete_pair + delete_only
    total_upload = replace_n + add_n

    # Every file must exist before anything is deleted: a missing file discovered
    # mid-run means the old source is already gone and its replacement never arrives.
    missing = []
    for row, action in zip(review.get("pairs", []), normalized["pairs"]):
        if action == "REPLACE":
            path = row.get("new_path", "")
            if not path or not os.path.exists(path):
                missing.append((row.get("new_name", "?"), path))
    for row, action in zip(review.get("new_only", []), normalized["new_only"]):
        if action == "ADD":
            path = row.get("path", "")
            if not path or not os.path.exists(path):
                missing.append((row.get("name", "?"), path))

    if missing:
        print(f"[FAIL] {len(missing)} file(s) to upload are missing from disk:")
        for name, path in missing[:10]:
            print(f"  - {name}  ({path or 'no path'})")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")
        print("[FAIL] Nothing deleted. Re-run organize_content.py, or set those rows")
        print("       to KEEP/SKIP in comparison_review.json.")
        sys.exit(1)

    print("--- Applying reviewed comparison plan ---")
    print(f"  Pairs:        {replace_n} REPLACE, {delete_pair} DELETE, {keep_pair} KEEP")
    print(f"  Current-only: {delete_only} DELETE, {keep_only} KEEP")
    print(f"  New-only:     {add_n} ADD, {skip_n} SKIP")
    print(f"  -> {total_upload} file(s) to upload, then {total_delete} source(s) to delete")
    print()

    # Upload before delete. A transient duplicate is recoverable by deleting it;
    # a source deleted before its replacement arrives is not recoverable at all.
    if total_upload > 0:
        print("--- Running upload_agent.py ---")
        result = subprocess.run([sys.executable, "upload_agent.py"], check=False)
        if result.returncode != 0:
            print(f"\n[FAIL] upload_agent.py exited with code {result.returncode}.")
            print("[FAIL] Skipping all deletions: the sources marked for deletion are")
            print("       still the only copies. Fix the uploads and re-run --apply;")
            print("       rows that already uploaded can be set to SKIP first.")
            sys.exit(1)
    else:
        print("--- No files to upload. ---")

    if total_delete > 0:
        print("\n--- Running delete_agent.py ---")
        result = subprocess.run([sys.executable, "delete_agent.py"], check=False)
        if result.returncode != 0:
            print(f"[WARN] delete_agent.py exited with code {result.returncode}")
            print("\n--- Apply finished with errors. ---")
            sys.exit(1)
    else:
        print("--- No sources to delete. ---")

    print("\n--- Apply complete. ---")


if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply_review()
    else:
        generate_review()
