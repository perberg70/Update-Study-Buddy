"""
Export the current list of source names from the NotebookLM notebook to current_sources.json.
Run with Chrome open: chrome.exe --remote-debugging-port=9222, and the notebook open (or we navigate to it).
This keeps current_sources.json up to date so compare_sources.py can use it.
"""
import json
import os
import re
import sys
from playwright.sync_api import sync_playwright

CURRENT_SOURCES_FILE = "current_sources.json"
PROJECT_URL = "https://notebooklm.google.com/notebook/82c34a38-cbc5-47fe-8001-36696f67d7fb"


def run_export():
    print("--- Exporting current NotebookLM sources to", CURRENT_SOURCES_FILE, "---")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]
            page = context.pages[0]
            print("[OK] Connected via CDP.")
        except Exception as e:
            print(f"[FAIL] CDP connection failed: {e}")
            print("Start Chrome with:  & \"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\" --remote-debugging-port=9222")
            sys.exit(1)

        page.goto(PROJECT_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("load")

        page.get_by_role("button", name=re.compile(r"(\+\s*)?Add\s+source|Lägg\s+till\s+källa", re.I)).first.wait_for(
            state="visible", timeout=30_000
        )
        page.wait_for_timeout(2000)

        # Scroll the Sources panel so all items are in DOM (virtual list)
        try:
            panel = page.locator('section, [role="region"], [class*="sidebar"], [class*="source"]').filter(has=page.get_by_text(re.compile(r"Add\s+source|Sources|Källor", re.I))).first
            for _ in range(8):
                panel.evaluate("el => el.scrollBy(0, 350)")
                page.wait_for_timeout(250)
        except Exception:
            pass

        # Icon/UI labels to exclude (Material icons, controls) – not real source names
        IGNORE_TOKENS = {
            "description", "more_vert", "drive_pdf", "video_audio_call", "video_youtube",
            "more", "mer", "add", "lägg", "sources", "källor", "insert_drive_file",
            "link", "content_copy", "upload", "folder", "image", "article",
            "keyboard_arrow_down", "expand_more", "expand_less", "arrow_drop_down",
        }
        # Full phrases / UI labels that must not appear as source names (exact or contained)
        UI_PHRASES_EXCLUDE = {
            "select all sources", "keyboard arrow down", "keyboard_arrow_down",
            "välj alla källor", "video_audio_call", "video_youtube", "drive_pdf",
        }

        # Scrape only from the Sources panel: find list items and get the actual source title (aria-labelledby or main text minus icons)
        sources = page.evaluate("""(ignoreTokens) => {
            const ignore = new Set((ignoreTokens || []).map(s => s.toLowerCase()));
            const out = [];
            // Find the Sources section: region that contains "Add source" / "Sources" and has the source list
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

            // Fallback 1: rows that contain a More/menu button – the row text is the source name
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

            // Fallback 2: any labelled spans/divs in the panel that look like titles (longer text, not buttons)
            const allLabels = sourcesPanel.querySelectorAll('[id][id*="label"], [aria-label], [class*="title"], [class*="name"]');
            allLabels.forEach(el => {
                const t = (el.textContent || el.getAttribute('aria-label') || '').trim();
                if (t.length > 4 && !ignore.has(t.toLowerCase()) && !/^\\d+$/.test(t)) out.push(t);
            });
            return [...new Set(out)];
        }""", IGNORE_TOKENS)

        if not sources or not isinstance(sources, list):
            sources = []

        # Filter: must look like a real source name (not just an icon word or UI phrase)
        def is_likely_source_name(s):
            if not s or len(s) < 2:
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

        # Python fallback 1: listitem text
        if not sources or all(len(s) < 5 for s in sources):
            try:
                items = page.locator('[role="listitem"]')
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

        # Python fallback 2: get ALL visible text from the Sources panel (via JS from Add source button) and parse lines
        if not sources or all(len(s) < 5 for s in sources):
            try:
                full_text = page.evaluate("""() => {
                    const btn = Array.from(document.querySelectorAll('button, [role="button"]')).find(b => /add\\s+source|sources|källor|lägg/i.test(b.textContent || b.getAttribute('aria-label') || ''));
                    if (!btn) return '';
                    const panel = btn.closest('section') || btn.closest('aside') || btn.closest('[class*="sidebar"]') || btn.closest('[class*="panel"]') || btn.closest('nav') || btn.parentElement?.parentElement?.parentElement;
                    return panel ? panel.innerText : '';
                }""")
                if full_text and isinstance(full_text, str):
                    ui_phrases = {"add source", "add sources", "sources", "källor", "lägg till källa", "upload files", "websites", "drive", "copied text", "ladda upp", "webbplatser", "more", "mer", "select all sources", "keyboard arrow down", "välj alla källor"}
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

        # Python fallback 3: same but with Playwright locator for panel
        if not sources or all(len(s) < 5 for s in sources):
            try:
                add_btn = page.get_by_role("button", name=re.compile(r"(\+\s*)?Add\s+source|Lägg\s+till\s+källa", re.I)).first
                panel = page.locator("section, [role='region'], aside, nav").filter(has=add_btn).first
                full_text = panel.inner_text(timeout=5000)
                ui_phrases = {"add source", "add sources", "sources", "källor", "lägg till källa", "upload files", "websites", "drive", "copied text", "ladda upp", "webbplatser", "more", "mer", "select all sources", "keyboard arrow down", "välj alla källor"}
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

        # Final filter: remove any UI/icon names that slipped in from fallbacks
        sources = [s.strip() for s in sources if isinstance(s, str) and is_likely_source_name(s.strip())]
        sources = list(dict.fromkeys(sources))

        if not sources:
            debug_text = page.evaluate("""() => {
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
