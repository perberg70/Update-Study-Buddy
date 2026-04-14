"""
Export the current list of source names from the NotebookLM notebook to current_sources.json.

Chrome: --remote-debugging-port=9222. Config: config.py (PROJECT_URL, CDP_URL, CURRENT_SOURCES_FILE).

Flow (GitHub simplicity + reliability):
1) Connect, prefer a tab already on notebooklm.google.com.
2) Try the fast path: wait for Add source on the main Playwright page (same as GitHub).
3) Click Sources / Källor tab and retry (sidebar often hidden until then).
4) If still missing, poll every frame — the sidebar sometimes lives in an iframe.
"""
from __future__ import annotations

import json
import re
import sys

from playwright.sync_api import sync_playwright

from config import CDP_URL, CURRENT_SOURCES_FILE, PROJECT_URL
from delete_agent import find_best_sources_frame
from notebook_ready import ADD_SOURCE_BTN, pick_notebook_page, resolve_sidebar_frame
from notebooklm_sources import (
    extract_sources_from_panel_text,
    get_sidebar_panel_text_js,
    is_notebook_placeholder_title,
)


def run_export() -> None:
    print("--- Exporting current NotebookLM sources to", CURRENT_SOURCES_FILE, "---")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            page = pick_notebook_page(browser)
            print("[OK] Connected via CDP.")
        except Exception as e:
            print(f"[FAIL] CDP connection failed: {e}")
            try:
                _p = CDP_URL.rsplit(":", 1)[-1].split("/")[0]
            except IndexError:
                _p = "9222"
            print(
                'Start Chrome with:  & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                f"--remote-debugging-port={_p}"
            )
            sys.exit(1)

        page.goto(PROJECT_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("load")
        try:
            page.bring_to_front()
        except Exception:
            pass

        export_frame = resolve_sidebar_frame(page)
        if export_frame is None:
            print(
                "[FAIL] Could not find Add source / Lägg till (main frame or iframe).\n"
                "       Chrome (9222): stay logged in, open this notebook, then retry.\n"
                f"       URL: {PROJECT_URL}"
            )
            sys.exit(1)
        # Prefer the frame whose sidebar text parses to the most rows (matches delete_agent).
        try:
            export_frame = find_best_sources_frame(page)
        except Exception:
            pass
        page.wait_for_timeout(1500)

        try:
            panel = export_frame.locator(
                'section, [role="region"], [class*="sidebar"], [class*="source"], aside, nav'
            ).filter(
                has=export_frame.get_by_text(re.compile(r"Add\s+sources?|Sources|Källor|Lägg", re.I))
            ).first
            for _ in range(8):
                panel.evaluate("el => el.scrollBy(0, 350)")
                page.wait_for_timeout(250)
        except Exception:
            pass

        IGNORE_TOKENS = {
            "description", "more_vert", "drive_pdf", "video_audio_call", "video_youtube",
            "more", "mer", "add", "lägg", "sources", "källor", "insert_drive_file",
            "link", "content_copy", "upload", "folder", "image", "article",
            "keyboard_arrow_down", "expand_more", "expand_less", "arrow_drop_down",
        }
        UI_PHRASES_EXCLUDE = {
            "select all sources", "keyboard arrow down", "keyboard_arrow_down",
            "välj alla källor", "video_audio_call", "video_youtube", "drive_pdf",
        }

        sources = export_frame.evaluate(
            """(ignoreTokens) => {
            const ignore = new Set((ignoreTokens || []).map(s => s.toLowerCase()));
            const out = [];
            const addBtn = Array.from(document.querySelectorAll('button, [role="button"]')).find(b => /add\\s+source|sources|källor|lägg\\s+till/i.test(b.textContent || b.getAttribute('aria-label') || ''));
            const sourcesPanel = addBtn ? addBtn.closest('section, [role="region"], aside, nav, [class*="sidebar"], [class*="panel"], [class*="source"]') || document : document;

            const listItems = sourcesPanel.querySelectorAll('[role="listitem"]');
            listItems.forEach(li => {
                let name = '';
                const labelledId = li.getAttribute('aria-labelledby');
                if (labelledId) {
                    const labelEl = document.getElementById(labelledId);
                    if (labelEl) name = (labelEl.textContent || '').trim();
                }
                if (!name) {
                    const full = (li.innerText || li.textContent || '').trim();
                    const parts = full.split(/\\s+/).filter(p => p.length > 0 && !ignore.has(p.toLowerCase()) && !/^\\d+$/.test(p));
                    name = parts.join(' ').trim();
                }
                if (name && name.length > 1 && !ignore.has(name.toLowerCase())) out.push(name);
            });

            if (out.length) return [...new Set(out)];

            const withMenu = sourcesPanel.querySelectorAll('[aria-label*="More"], [aria-label*="Mer"], button[aria-label], [class*="more"]');
            const seen = new Set();
            withMenu.forEach(btn => {
                const row = btn.closest('[role="listitem"], li, [class*="row"], [class*="item"], [class*="source"], [class*="mat-list"]');
                if (!row) return;
                const key = row.getBoundingClientRect?.()?.top + row.innerText?.slice(0,50) || row;
                if (seen.has(key)) return;
                seen.add(key);
                let full = (row.innerText || row.textContent || '').trim();
                full = full.replace(/more_vert|description|drive_pdf|video_youtube|video_audio_call|More|Mer/gi, '').replace(/\\s+/g, ' ').trim();
                const parts = full.split(' ').filter(p => p.length > 0 && !ignore.has(p.toLowerCase()) && !/^\\d+$/.test(p));
                const name = parts.join(' ').trim();
                if (name.length > 3) out.push(name);
            });
            if (out.length) return [...new Set(out)];

            const allLabels = sourcesPanel.querySelectorAll('[id][id*="label"], [aria-label], [class*="title"], [class*="name"]');
            allLabels.forEach(el => {
                const t = (el.textContent || el.getAttribute('aria-label') || '').trim();
                if (t.length > 4 && !ignore.has(t.toLowerCase()) && !/^\\d+$/.test(t)) out.push(t);
            });
            return [...new Set(out)];
        }""",
            IGNORE_TOKENS,
        )

        if not sources or not isinstance(sources, list):
            sources = []

        def is_likely_source_name(s):
            if not s or len(s) < 2:
                return False
            if is_notebook_placeholder_title(s):
                return False
            s_lower = s.lower()
            if s_lower in IGNORE_TOKENS:
                return False
            if any(phrase in s_lower for phrase in UI_PHRASES_EXCLUDE):
                return False
            if re.match(r"^[\d\s]+$", s):
                return False
            return True

        sources = [s.strip() for s in sources if isinstance(s, str) and is_likely_source_name(s.strip())]
        sources = list(dict.fromkeys(sources))

        if not sources or all(len(s) < 5 for s in sources):
            try:
                items = export_frame.locator('[role="listitem"]')
                n = items.count()
                for i in range(n):
                    el = items.nth(i)
                    t = el.inner_text(timeout=1000).strip()
                    for token in IGNORE_TOKENS:
                        t = re.sub(re.escape(token), "", t, flags=re.I)
                    t = re.sub(r"\s+", " ", t).strip()
                    if is_likely_source_name(t) and len(t) > 3:
                        sources.append(t)
                sources = list(dict.fromkeys(sources))
            except Exception:
                pass

        if not sources or all(len(s) < 5 for s in sources):
            try:
                full_text = export_frame.evaluate("""() => {
                    const btn = Array.from(document.querySelectorAll('button, [role="button"]')).find(b => /add\\s+source|sources|källor|lägg/i.test(b.textContent || b.getAttribute('aria-label') || ''));
                    if (!btn) return '';
                    const panel = btn.closest('section') || btn.closest('aside') || btn.closest('[class*="sidebar"]') || btn.closest('[class*="panel"]') || btn.closest('nav') || btn.parentElement?.parentElement?.parentElement;
                    return panel ? panel.innerText : '';
                }""")
                if full_text and isinstance(full_text, str):
                    ui_phrases = {
                        "add source", "add sources", "sources", "källor", "lägg till källa", "upload files",
                        "websites", "drive", "copied text", "ladda upp", "webbplatser", "more", "mer",
                        "select all sources", "keyboard arrow down", "välj alla källor",
                    }
                    for line in full_text.splitlines():
                        line = line.strip()
                        if not line or len(line) < 3:
                            continue
                        if line.lower() in ui_phrases:
                            continue
                        if re.match(r"^[\d\s\.]+$", line):
                            continue
                        if any(icon in line.lower() for icon in ("more_vert", "description", "drive_pdf", "video_youtube")):
                            continue
                        if re.search(r"\.(pdf|mp3|txt|docx|md|wav|m4a)(\s|$)", line, re.I) or line.startswith("http") or len(line) > 15:
                            sources.append(line)
                    sources = list(dict.fromkeys(sources))
            except Exception:
                pass

        if not sources or all(len(s) < 5 for s in sources):
            try:
                add_btn = export_frame.get_by_role("button", name=ADD_SOURCE_BTN).first
                panel = export_frame.locator("section, [role='region'], aside, nav").filter(has=add_btn).first
                full_text = panel.inner_text(timeout=5000)
                ui_phrases = {
                    "add source", "add sources", "sources", "källor", "lägg till källa", "upload files",
                    "websites", "drive", "copied text", "ladda upp", "webbplatser", "more", "mer",
                    "select all sources", "keyboard arrow down", "välj alla källor",
                }
                for line in full_text.splitlines():
                    line = line.strip()
                    if not line or len(line) < 3 or line.lower() in ui_phrases:
                        continue
                    if re.match(r"^[\d\s\.]+$", line) or any(icon in line.lower() for icon in ("more_vert", "description", "drive_pdf", "video_youtube")):
                        continue
                    if re.search(r"\.(pdf|mp3|txt|docx|md|wav|m4a)(\s|$)", line, re.I) or line.startswith("http") or len(line) > 10:
                        sources.append(line)
                sources = list(dict.fromkeys(sources))
            except Exception:
                pass

        sources = [s.strip() for s in sources if isinstance(s, str) and is_likely_source_name(s.strip())]
        sources = list(dict.fromkeys(sources))

        # Virtualized / non–list-item sidebars: scroll + merge innerText (same strategy as delete_agent /
        # read_notebook_sources). Prefer this when it yields more titles than role=listitem scraping.
        merged: list[str] = []
        try:
            panel_text = str(export_frame.evaluate(get_sidebar_panel_text_js()) or "")
            merged = extract_sources_from_panel_text(panel_text, unique=True)
            merged = [s.strip() for s in merged if isinstance(s, str) and is_likely_source_name(s.strip())]
            merged = list(dict.fromkeys(merged))
        except Exception as exc:
            print(f"[WARN] Scroll-merge sidebar scrape failed: {exc}")

        if len(merged) > len(sources):
            sources = merged
            print(
                f"[INFO] Using scroll-merged sidebar text ({len(sources)} title(s)); "
                "listitem / plain innerText path had fewer."
            )

        if not sources:
            debug_text = export_frame.evaluate("""() => {
                const btn = Array.from(document.querySelectorAll('button, [role="button"]')).find(b => /add\\s+source|sources|källor|lägg/i.test(b.textContent || b.getAttribute('aria-label') || ''));
                const panel = btn ? (btn.closest('section') || btn.closest('aside') || btn.closest('[class*="sidebar"]') || btn.parentElement?.parentElement) : null;
                return panel ? panel.innerText : (document.body?.innerText || '').slice(0, 8000);
            }""")
            debug_path = "export_sources_debug.txt"
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(debug_text if isinstance(debug_text, str) else str(debug_text))
            print(f"[DEBUG] 0 sources found. Wrote panel text to {debug_path} – check it to adjust selectors.")

        with open(CURRENT_SOURCES_FILE, "w", encoding="utf-8") as f:
            json.dump(sources, f, indent=2, ensure_ascii=False)

        print(f"[OK] Wrote {len(sources)} source(s) to {CURRENT_SOURCES_FILE}")


if __name__ == "__main__":
    run_export()
