"""Export the current list of source names from the notebook to current_sources.json.

Requires Chrome started with --remote-debugging-port=9222 and signed in to the
account that owns the notebook.

This file is the single source of truth for "what is currently in the notebook",
so it must never write a result it cannot vouch for: a wrong answer here causes
the compare step to treat existing sources as missing, and the delete step to
treat unrelated sources as duplicates.
"""

import json
import re
import sys

from config import CURRENT_SOURCES_FILE, PROJECT_URL
from notebooklm_client import BrowserConnectionError, connect, describe_page

DEBUG_PATH = "export_sources_debug.json"

PANEL_READY = re.compile(r"(\+\s*)?Add\s+sources?|Lägg\s+till\s+källa", re.I)

# Material icon ligatures and chrome that render as text inside the panel.
NOT_A_SOURCE = {
    "add", "add source", "add sources", "sources", "källor", "lägg till källa",
    "select all", "välj alla källor", "web", "fast research", "search",
    "search spark", "label auto", "sort", "language", "keyboard arrow down",
    "drop files here", "markdown", "description", "video audio call",
    "video youtube", "drive pdf", "more vert", "attach file", "dock to right",
}

# One entry per source row. aria-description carries the full untruncated title;
# .source-title-column is captured too so a disagreement is visible rather than silent.
SCRAPE_JS = """() => {
    const rowSelectors = [
        'div.single-source-container',   // Gemini Notebook (current)
        '[role="listitem"]',             // legacy NotebookLM
        'mat-list-item, .mat-mdc-list-item',
    ];

    let rows = [];
    let usedSelector = null;
    for (const sel of rowSelectors) {
        const found = document.querySelectorAll(sel);
        if (found.length) { rows = Array.from(found); usedSelector = sel; break; }
    }

    const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();

    const entries = rows.map(row => {
        const btn = row.querySelector('button[aria-description]');
        const col = row.querySelector('.source-title-column');
        return {
            aria: clean(btn ? btn.getAttribute('aria-description') : ''),
            title: clean(col ? (col.innerText || col.textContent) : ''),
            rowText: clean(row.innerText || row.textContent).slice(0, 200),
        };
    });

    return {
        usedSelector,
        rowCount: rows.length,
        entries,
        checkboxes: document.querySelectorAll('input[type="checkbox"], [role="checkbox"]').length,
        ariaButtons: document.querySelectorAll('button[aria-description]').length,
    };
}"""


def is_source_name(name: str) -> bool:
    """Reject UI labels, icon ligatures and empty strings."""
    if not name or len(name.strip()) < 2:
        return False
    flat = re.sub(r"[^a-z0-9åäö]+", " ", name.lower()).strip()
    if flat in NOT_A_SOURCE:
        return False
    # "add Add sources" - icon ligature glued to its own button label.
    if re.fullmatch(r"(add\s+)?add\s+sources?", flat):
        return False
    if re.fullmatch(r"[\d\s.]+", name.strip()):
        return False
    return True


def write_debug(report: dict, reason: str) -> None:
    report = dict(report, _reason=reason)
    with open(DEBUG_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"[DEBUG] Wrote {DEBUG_PATH} - send this file to diagnose the selectors.")


def run_export() -> int:
    print(f"--- Exporting current sources to {CURRENT_SOURCES_FILE} ---")

    # Imported here, not at module scope, so the pure helpers above stay testable
    # without a browser stack installed.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser, page = connect(p)
            print(f"[OK] Connected via CDP. Starting from tab: {describe_page(page)}")
        except BrowserConnectionError as exc:
            print(f"[FAIL] {exc}")
            return 1

        page.goto(PROJECT_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("load")
        print(f"[OK] Scraping: {describe_page(page)}")

        # A modal overlay hides the sources panel; dismiss before reading.
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

        try:
            page.get_by_role("button", name=PANEL_READY).first.wait_for(
                state="visible", timeout=30_000
            )
        except Exception as exc:
            print(f"[FAIL] Sources panel never appeared: {exc}")
            return 1
        page.wait_for_timeout(2500)

        report = page.evaluate(SCRAPE_JS)

    if not report["rowCount"]:
        print("[FAIL] Found 0 source rows. The page structure has changed.")
        write_debug(report, "no rows matched any known row selector")
        return 1

    print(f"[OK] Matched {report['rowCount']} row(s) via '{report['usedSelector']}'")

    sources, mismatches, rejected = [], [], []
    for entry in report["entries"]:
        name = entry["aria"] or entry["title"] or entry["rowText"]
        if entry["aria"] and entry["title"] and entry["aria"] != entry["title"]:
            mismatches.append((entry["aria"], entry["title"]))
        if is_source_name(name):
            sources.append(name.strip())
        elif name:
            rejected.append(name)

    seen, unique = set(), []
    for name in sources:
        if name.lower() not in seen:
            seen.add(name.lower())
            unique.append(name)
    sources = unique

    if rejected:
        print(f"[INFO] Ignored {len(rejected)} non-source label(s), e.g. {rejected[:3]}")
    if mismatches:
        print(f"[WARN] {len(mismatches)} row(s) where aria-description and the visible")
        print(f"       title differ. Using aria-description. First: {mismatches[0]}")

    # A scrape that finds rows but no usable names is a failure, not an empty notebook.
    if not sources:
        print(f"[FAIL] {report['rowCount']} row(s) found but no usable source names.")
        write_debug(report, "rows matched but every name was rejected")
        return 1

    # Sanity-check against an independent count (one checkbox per source + 'select all').
    expected = max(report["checkboxes"] - 1, 0)
    if expected and abs(len(sources) - expected) > max(2, expected * 0.1):
        print(f"[WARN] Found {len(sources)} name(s) but {expected} checkbox(es) suggest")
        print("       a different total. Treating this as unreliable.")
        write_debug(report, f"count mismatch: {len(sources)} names vs {expected} checkboxes")
        return 1

    with open(CURRENT_SOURCES_FILE, "w", encoding="utf-8") as fh:
        json.dump(sources, fh, indent=2, ensure_ascii=False)

    print(f"[OK] Wrote {len(sources)} source(s) to {CURRENT_SOURCES_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_export())
