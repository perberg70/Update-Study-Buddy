#!/usr/bin/env python3
"""Print the course hierarchy: modules (chapters), units, subunits, components.

Read-only. Use it to see the real display_name titles - the filenames elsewhere
in this project are slugified and lose punctuation and numbering - and to decide
how chapters group into modules.

Usage:
    python tools/list_structure.py           # modules with counts
    python tools/list_structure.py --full    # every unit and subunit
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import COURSE_STRUCTURE_PATH  # noqa: E402


def component_counts(chapter):
    counts = Counter()
    for seq in chapter.get("sequentials", []):
        for vert in seq.get("verticals", []):
            for comp in vert.get("components", []):
                counts[comp.get("type", "?")] += 1
    return counts


def main() -> int:
    full = "--full" in sys.argv

    if not os.path.exists(COURSE_STRUCTURE_PATH):
        print(f"[FAIL] {COURSE_STRUCTURE_PATH} not found. Run extract_edx.py first.")
        return 1

    with open(COURSE_STRUCTURE_PATH, "r", encoding="utf-8") as fh:
        structure = json.load(fh)

    chapters = structure.get("chapters", [])
    totals = Counter()

    for index, chapter in enumerate(chapters, start=1):
        seqs = chapter.get("sequentials", [])
        verts = [v for s in seqs for v in s.get("verticals", [])]
        counts = component_counts(chapter)
        totals.update(counts)

        summary = ", ".join(f"{n} {t}" for t, n in sorted(counts.items())) or "no components"
        print(f"\n{index:2}. {chapter.get('title', '(untitled)')}")
        print(f"      {len(seqs)} unit(s), {len(verts)} subunit(s)  [{summary}]")

        if not full:
            continue
        for seq in seqs:
            print(f"      UNIT: {seq.get('title', '(untitled)')}")
            for vert in seq.get("verticals", []):
                types = Counter(c.get("type", "?") for c in vert.get("components", []))
                detail = " ".join(f"{t}x{n}" for t, n in sorted(types.items()))
                print(f"        SUBUNIT: {vert.get('title', '(untitled)')}   {detail}")

    print()
    print("=" * 66)
    print(f"  {len(chapters)} chapter(s) total")
    for comp_type, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"    {comp_type:16} {n}")
    if not full:
        print("\n  Re-run with --full to list every unit and subunit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
