"""Command-line argument handling on the scripts that can destroy things.

These scripts used to test `sys.argv` by substring, so an unrecognised flag was
ignored rather than rejected. The danger was asymmetric: a typo that engages a
safety is harmless, while `delete_agent.py --dryrun` set dry_run = False and
deleted for real, and NotebookLM deletion cannot be undone.

Each script is run as a subprocess, so this exercises the real entry point
rather than an imported function.

Run: python tests/test_cli.py
"""

import io
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPTS = [
    "delete_agent.py",
    "compare_sources.py",
    "upload_agent.py",
    "export_current_sources.py",
    "run_full_update.py",
    "preflight.py",
]

# Misspellings of a flag that turns a safety OFF. Each of these once ran.
DISARMING_TYPOS = [
    ("delete_agent.py", "--dryrun"),
    ("delete_agent.py", "--dry_run"),
    ("delete_agent.py", "--dry-runn"),
    ("compare_sources.py", "--aply"),
    ("compare_sources.py", "--Apply"),
]


def run(script, *args, cwd=None):
    return subprocess.run([sys.executable, os.path.join(ROOT, script), *args],
                          capture_output=True, text=True, timeout=120,
                          cwd=cwd or ROOT)


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


def main():
    failures = []

    for script in SCRIPTS:
        # --help must work and do nothing else.
        result = run(script, "--help")
        check(failures, result.returncode == 0,
              f"{script} --help exited {result.returncode}")
        check(failures, "usage:" in result.stdout,
              f"{script} --help printed no usage")

        # An unrecognised flag must be an error, naming the flag.
        result = run(script, "--not-a-real-flag")
        check(failures, result.returncode != 0,
              f"{script} accepted --not-a-real-flag (exit {result.returncode})")
        check(failures, "not-a-real-flag" in (result.stderr + result.stdout),
              f"{script} did not name the bad flag")

    # The flags whose misspelling used to disarm a safety.
    for script, typo in DISARMING_TYPOS:
        result = run(script, typo)
        check(failures, result.returncode != 0,
              f"{script} {typo} was ACCEPTED - a safety can still be typo'd off")
        combined = result.stdout + result.stderr
        # It must not have reached the work: no browser, no plan, no deletion.
        for marker in ("[PLAN]", "Connected via CDP", "[MODE] LIVE", "Deleting"):
            check(failures, marker not in combined,
                  f"{script} {typo} reached the work: saw {marker!r}")

    # The data-loss path, tested directly rather than inferred from exit code:
    # a hand-edited review file must survive a misspelled --apply.
    with tempfile.TemporaryDirectory() as base:
        review = os.path.join(base, "comparison_review.json")
        original = '{"pairs": [{"action": "KEEP", "note": "edited by hand"}]}'
        io.open(review, "w", encoding="utf-8").write(original)

        env = dict(os.environ, COMPARISON_REVIEW_PATH=review)
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "compare_sources.py"), "--aply"],
            capture_output=True, text=True, timeout=120, cwd=base, env=env)

        check(failures, result.returncode != 0, "compare_sources.py --aply succeeded")
        after = io.open(review, encoding="utf-8").read()
        check(failures, after == original,
              "a misspelled --apply overwrote the hand-edited review file")

    # Correct flags still parse. --dry-run reaches the work and says so; it is
    # run where there is no review file, so it stops before any browser.
    result = run("delete_agent.py", "--dry-run", cwd=tempfile.gettempdir())
    check(failures, "[MODE] dry run" in result.stdout,
          f"--dry-run should announce the mode, got: {result.stdout[:200]!r}")
    check(failures, "[MODE] LIVE" not in result.stdout,
          "--dry-run must never announce LIVE")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print(f"  [PASS] {len(SCRIPTS)} script(s): --help works, unknown flags rejected")
        print(f"  [PASS] {len(DISARMING_TYPOS)} safety-disarming typo(s) refused before "
              "any work")
        print("  [PASS] a hand-edited review file survives a misspelled --apply")
    return not failures


if __name__ == "__main__":
    print("command-line argument handling")
    raise SystemExit(0 if main() else 1)
