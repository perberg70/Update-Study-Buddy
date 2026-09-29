"""Module grouping and PDF content checks.

Run: python tests/test_module_pdf.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from build_module_pdf import (group_modules, module_arg,  # noqa: E402
                              module_filename, module_label, select_modules)


def test_grouping():
    """3., 3.A, 3.B collapse into one module; unnumbered chapters stand alone."""
    chapters = [{"title": t} for t in [
        "1. Welcome & What GenAI Can Do Today",
        "2. Learning with AI",
        "3. Profession Specific Use Cases",
        "3.A Track: Business/Industry",
        "3.B Track: Academia",
        "3.C Track: Public Sector",
        "4. What AI is (+Ethics of generative AI)",
        "5. Driving Change",
        "Final seminar - April 1st",
        "Toolbox & Additional Resources",
        "Share your AI Experiences",
    ]]
    modules = group_modules(chapters)
    failures = []

    if len(modules) != 8:
        failures.append(f"expected 8 modules (5 numbered + 3 unnumbered), got {len(modules)}")

    by_number = {m["number"]: m for m in modules if m["number"]}
    if len(by_number.get("3", {}).get("chapters", [])) != 4:
        failures.append("module 3 should hold 3., 3.A, 3.B and 3.C")
    if module_filename(by_number.get("1", {"number": "1"})) != "Module_1.pdf":
        failures.append("numbered module filename should be Module_<n>.pdf")

    unnumbered = [m for m in modules if not m["number"]]
    if len(unnumbered) != 3:
        failures.append(f"expected 3 unnumbered modules, got {len(unnumbered)}")

    label = module_label(by_number.get("1", {"number": "1", "title": "Welcome & What GenAI Can Do Today"}))
    if not label.startswith("Module 1:"):
        failures.append(f"unexpected label {label!r}")

    # --module means the same in every tool: a number, else a title fragment.
    # The final seminar has no number, so a number-only rule - which four tools
    # had - left its audio unreachable by anything but the PDF builder.
    picked = lambda arg: [module_label(m) for m in select_modules(modules, arg)]
    if picked("3") != [module_label(by_number["3"])]:
        failures.append(f"--module 3 should pick module 3, got {picked('3')}")
    if picked("final seminar") != ["Final seminar - April 1st"]:
        failures.append(f"a title fragment should pick the seminar, got "
                        f"{picked('final seminar')}")
    # "Final seminar - April 1st" contains a 1: the number must still win.
    if picked("1") != [module_label(by_number["1"])]:
        failures.append(f"--module 1 must not also pick the April 1st seminar, "
                        f"got {picked('1')}")
    if len(select_modules(modules, "")) != len(modules) or picked("nothing like it"):
        failures.append("no argument selects all; an unmatched one selects none")
    seminar = select_modules(modules, "final seminar")[0]
    if module_arg(seminar) != '"Final seminar - April 1st"' or module_arg(by_number["1"]) != "1":
        failures.append(f"module_arg should quote a title: {module_arg(seminar)!r}")
    if [module_label(m) for m in select_modules(modules, module_arg(seminar).strip('"'))] \
            != ["Final seminar - April 1st"]:
        failures.append("what module_arg prints must select the same module back")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] --module selects by number, else by title; module_arg round-trips")
        print(f"  [PASS] grouping: {len(modules)} modules, module 3 spans "
              f"{len(by_number['3']['chapters'])} chapters")
    return not failures


if __name__ == "__main__":
    print("module grouping")
    raise SystemExit(0 if test_grouping() else 1)
