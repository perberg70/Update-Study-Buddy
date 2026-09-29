#!/usr/bin/env python3
"""Read-only diagnostic: record what the NotebookLM sources panel actually contains.

Run this when export_current_sources.py returns too few sources. It navigates to
the notebook and reports what each selector strategy the scraper relies on can
see, then writes panel_dump.json for offline analysis.

It navigates, presses Escape to dismiss any open dialog, scrolls the sidebar, and
reads. It never opens a menu, clicks a source, or deletes anything.

Usage:
    python tools/dump_panel.py
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import PROJECT_URL  # noqa: E402
from notebooklm_client import BrowserConnectionError, connect, describe_page  # noqa: E402

OUT_PATH = "panel_dump.json"

# Selectors the current scraper and delete matcher depend on.
PROBES = {
    "role_listitem": '[role="listitem"]',
    "button_aria_description": "button[aria-description]",
    "mat_list_item": "mat-list-item, .mat-mdc-list-item",
    "role_list": '[role="list"]',
    "role_row": '[role="row"]',
    "role_option": '[role="option"]',
    "checkbox": 'input[type="checkbox"], [role="checkbox"]',
    "source_class": '[class*="source"]',
    "button_total": "button",
}

COUNT_JS = """(probes) => {
    const out = {};
    for (const [name, sel] of Object.entries(probes)) {
        try { out[name] = document.querySelectorAll(sel).length; }
        catch (e) { out[name] = 'INVALID SELECTOR'; }
    }
    return out;
}"""

# Structural skeleton of the sidebar: tag.class[role] per element, depth-limited.
SKELETON_JS = """() => {
    const btn = Array.from(document.querySelectorAll('button, [role="button"]'))
        .find(b => /add\\s+source|l\\u00e4gg\\s+till\\s+k\\u00e4lla/i.test(
            (b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')));
    if (!btn) return {found: false};
    const panel = btn.closest('section, aside, nav, [role="region"], [class*="sidebar"], [class*="panel"]')
        || btn.parentElement;
    if (!panel) return {found: false};

    const lines = [];
    const walk = (el, depth) => {
        if (depth > 6 || lines.length > 220) return;
        for (const child of el.children) {
            const cls = (child.className && typeof child.className === 'string')
                ? '.' + child.className.trim().split(/\\s+/).slice(0, 3).join('.')
                : '';
            const role = child.getAttribute('role') ? `[role=${child.getAttribute('role')}]` : '';
            const ad = child.hasAttribute('aria-description') ? '[aria-description]' : '';
            const txt = (child.childElementCount === 0 && child.textContent)
                ? '  "' + child.textContent.trim().slice(0, 60) + '"' : '';
            lines.push('  '.repeat(depth) + child.tagName.toLowerCase() + cls + role + ad + txt);
            walk(child, depth + 1);
        }
    };
    walk(panel, 0);
    return {
        found: true,
        panelTag: panel.tagName.toLowerCase(),
        panelClass: String(panel.className || '').slice(0, 200),
        panelText: (panel.innerText || '').slice(0, 6000),
        skeleton: lines,
    };
}"""


def main() -> int:
    with sync_playwright() as p:
        try:
            browser, page = connect(p)
        except BrowserConnectionError as exc:
            print(f"[FAIL] {exc}")
            return 1

        print(f"[OK] Connected. Starting from tab: {describe_page(page)}")
        page.goto(PROJECT_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("load")
        print(f"[OK] Navigated to: {describe_page(page)}")

        # A modal overlay hides the sidebar; dismiss it before measuring.
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        page.wait_for_timeout(6000)

        report = {
            "url": page.url,
            "title": page.title(),
            "counts_before_scroll": page.evaluate(COUNT_JS, PROBES),
        }

        # A virtualized list only materializes rows near the viewport. If counts
        # climb while scrolling, virtualization is why the scraper under-reports.
        progression = []
        try:
            panel_label = re.compile(r"Add\s+source|Sources|Källor", re.I)
            panel = page.locator(
                "section, [role='region'], aside, nav, [class*='sidebar'], [class*='panel']"
            ).filter(has=page.get_by_text(panel_label)).first
            for i in range(12):
                panel.evaluate("el => el.scrollBy(0, 600)")
                page.wait_for_timeout(350)
                progression.append(page.evaluate(COUNT_JS, PROBES)["role_listitem"])
        except Exception as exc:
            progression.append(f"scroll failed: {exc}")
        report["role_listitem_while_scrolling"] = progression

        report["counts_after_scroll"] = page.evaluate(COUNT_JS, PROBES)
        report["panel"] = page.evaluate(SKELETON_JS)
        report["body_text_head"] = (page.inner_text("body") or "")[:4000]

        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)

    print()
    print(f"  page title : {report['title']}")
    print(f"  final url  : {report['url']}")
    print(f"  panel found: {report['panel'].get('found')}")
    print()
    print("  selector counts (before -> after scrolling):")
    before, after = report["counts_before_scroll"], report["counts_after_scroll"]
    for name in PROBES:
        print(f"    {name:26} {str(before.get(name)):>6} -> {after.get(name)}")
    print()
    print(f"  [OK] wrote {OUT_PATH} - send this file back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
