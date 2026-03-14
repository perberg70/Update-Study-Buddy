import subprocess
import os
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

    # ── Phase 1: Extract & Compare ──────────────────────────────
    if not run_script("export_current_sources.py"):
        return
    if not run_script("extract_edx.py"):
        return
    if not run_script("organize_content.py"):
        return
    if not run_script("compare_sources.py"):
        return

    # ── Review pause ────────────────────────────────────────────
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

    # ── Phase 2: Apply (delete old + upload new) ────────────────
    if not run_script("compare_sources.py", ["--apply"]):
        return

    print("\n--- UPDATE COMPLETE! Check NotebookLM for results. ---")

if __name__ == "__main__":
    main()
