"""
Study Buddy — full course update (this file is the only driver; there is no separate pipeline module).

Run order (fixed — do not reorder):
  1. export_current_sources.py  → current_sources.json
  2. extract_edx.py               → edx_export/ + course_structure.json
  3. organize_content.py          → Organized_Course_Content/ + processing_manifest.json
  4. validate_manifest.py         → structural checks (one merged chapter text row, URL rows sanity)
  5. compare_sources.py           → comparison_review.json
  6. (you edit comparison_review.json)
  7. compare_sources.py --apply   → delete_agent → upload_agent → dedupe

Content rules (enforced in organize_content / split_urls, not here):
  • One merged NN_ChapterName.txt per chapter for body text.
  • URLs → url_sources/ as link sources; merged file stripped of URLs unless you use --keep-urls-in-merged-chapter.
  • One MP3 per edX video where applicable.
"""
import subprocess
import sys


def run_script(name, args=None):
    cmd = [sys.executable, name] + (args or [])
    label = name + (" " + " ".join(args) if args else "")
    print(f"\n--- Running {label} ---")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"[FAIL] {label} failed with return code {result.returncode}")
        return False
    return True


def main():
    print("=" * 60)
    print("  Study Buddy — Full Course Update")
    print("=" * 60)

    if not run_script("export_current_sources.py"):
        return
    if not run_script("extract_edx.py"):
        return
    if not run_script("organize_content.py"):
        return
    if not run_script("validate_manifest.py"):
        return
    if not run_script("compare_sources.py"):
        return

    print()
    print("=" * 60)
    print("  REVIEW PHASE")
    print("=" * 60)
    print()
    print("  comparison_review.json has been generated.")
    print("  Open it in your editor and review / adjust the actions:")
    print()
    print("    PAIRS        → REPLACE | DELETE | KEEP")
    print("    CURRENT_ONLY → DELETE  | KEEP")
    print("    NEW_ONLY     → ADD     | SKIP")
    print()
    print("  Save the file when done, then press Enter here.")
    print("=" * 60)

    try:
        input("\n>>> Press Enter to apply the reviewed plan (Ctrl+C to abort)... ")
    except KeyboardInterrupt:
        print("\nAborted.")
        return

    if not run_script("compare_sources.py", ["--apply"]):
        return

    print("\n--- UPDATE COMPLETE! Check NotebookLM for results. ---")


if __name__ == "__main__":
    main()
