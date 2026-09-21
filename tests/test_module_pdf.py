"""Module grouping and PDF content checks.

Run: python tests/test_module_pdf.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from build_module_pdf import group_modules, module_filename, module_label  # noqa: E402


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

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print(f"  [PASS] grouping: {len(modules)} modules, module 3 spans "
              f"{len(by_number['3']['chapters'])} chapters")
    return not failures


if __name__ == "__main__":
    print("module grouping")
    raise SystemExit(0 if test_grouping() else 1)
