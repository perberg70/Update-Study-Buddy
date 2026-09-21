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
    print("  Study Buddy - Full Course Update")
    print("=" * 60)

    # ── Phase 1: Export + compare inputs ────────────────────────
    #
    # delete_agent.py --dedupe-current used to run here, before the review gate.
    # It is deliberately no longer automatic: it sweeps the whole notebook with no
    # human confirmation, and duplicates were themselves caused by a broken export
    # (compare then sees no existing sources and re-uploads everything). With the
    # export fixed, uploads stop duplicating and the sweep is not needed.
    # To deduplicate, run it explicitly and review the plan first:
    #     python delete_agent.py --dedupe-current --dry-run
    #     python delete_agent.py --dedupe-current
    if not run_script("export_current_sources.py"):
        return False
    if not run_script("extract_edx.py"):
        return False
    if not run_script("organize_content.py"):
        return False
    if not run_script("compare_sources.py"):
        return False

    # ── Review pause ────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  REVIEW PHASE")
    print("=" * 60)
    print()
    print("  comparison_review.json has been generated.")
    print("  Open it in your editor and review / adjust the actions:")
    print()
    print("    PAIRS        -> REPLACE | DELETE | KEEP")
    print("    CURRENT_ONLY -> DELETE  | KEEP")
    print("    NEW_ONLY     -> ADD     | SKIP")
    print()
    print("  Save the file when done, then press Enter here.")
    print("=" * 60)

    try:
        input("\n>>> Press Enter to apply the reviewed plan (Ctrl+C to abort)... ")
    except KeyboardInterrupt:
        print("\nAborted.")
        return False

    # ── Phase 2: Apply (delete old + upload new) ────────────────
    if not run_script("compare_sources.py", ["--apply"]):
        return False

    print("\n--- UPDATE COMPLETE! Check the notebook for results. ---")
    return True

if __name__ == "__main__":
    # Exit non-zero on failure: export_current_sources.py now reports a failed
    # scrape properly, so the orchestrator must not claim success over it.
    raise SystemExit(0 if main() else 1)
