#!/usr/bin/env python3
"""
Read the NotebookLM Sources list from the open Chrome session (CDP).

Proof output: writes notebook_sources_proof.json with per-frame facts and the source list.

NotebookLM often lists sources only as lines in the sidebar innerText (with icon labels like
markdown / video_audio_call between rows), not as role=listitem nodes — we parse extensions,
URLs, and long titles from that text when the DOM tree has no list items.

Prerequisite:
  Chrome with remote debugging, e.g.:
  & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222

Usage:
  python read_notebook_sources.py
  python read_notebook_sources.py --url https://notebooklm.google.com/notebook/YOUR_ID
  python read_notebook_sources.py --verify   # no Chrome: test parser on notebook_sources_proof.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Frame, sync_playwright

from config import CDP_URL
from notebooklm_sources import (
    IGNORE_TOKENS,
    extract_sources_from_panel_text,
    get_sidebar_panel_text_js,
    is_eligible_notebook_frame,
    sidebar_scroll_js,
)
# get_sidebar_panel_text_js scrolls the shell first so virtualized rows appear in innerText.

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_PATH = SCRIPT_DIR / "notebook_sources_proof.json"
DEFAULT_NOTEBOOK_URL = (
    "https://notebooklm.google.com/notebook/82c34a38-cbc5-47fe-8001-36696f67d7fb"
)

UI_PHRASES_EXCLUDE = {
    "select all sources",
    "keyboard arrow down",
    "keyboard_arrow_down",
    "välj alla källor",
}

def is_likely_source_name(s: str) -> bool:
    if not s or len(s) < 2:
        return False
    sl = s.lower()
    if sl in IGNORE_TOKENS:
        return False
    if any(p in sl for p in UI_PHRASES_EXCLUDE):
        return False
    if re.match(r"^[\d\s]+$", s):
        return False
    return True


def scrape_frame_facts() -> str:
    """Returns JSON-serializable dict from in-frame JS: counts, names, snippet (facts, not guesses)."""
    return r"""
() => {
  const IGNORE = new Set([
    "description", "more_vert", "drive_pdf", "video_youtube", "video_audio_call",
    "markdown", "more", "mer", "add", "sources", "källor", "insert_drive_file", "link",
    "content_copy", "upload", "folder", "image", "article",
    "keyboard_arrow_down", "expand_more", "expand_less", "arrow_drop_down"
  ]);
  const UI_LINE = new Set([
    "add source", "add sources", "sources", "källor", "lägg till källa", "upload files",
    "websites", "drive", "copied text", "ladda upp", "webbplatser", "select all sources",
    "välj alla källor", "keyboard arrow down", "more", "mer"
  ]);

  function findAddBtn() {
    return Array.from(document.querySelectorAll('button, [role="button"], a[role="button"]')).find(
      b => /add\s+source|lägg\s+till|sources|källor|upload/i.test(
        (b.getAttribute('aria-label') || '') + ' ' + (b.textContent || '')
      )
    );
  }

  function sidebarShellFromAddBtn(addBtn) {
    if (!addBtn) return document.body;
    const bodyLen = (document.body.innerText || '').trim().length || 1;
    let best = addBtn;
    let bestLen = 0;
    let el = addBtn;
    for (let i = 0; i < 36 && el; i++) {
      const len = (el.innerText || '').trim().length;
      if (len > bestLen && len >= 100 && len <= bodyLen * 0.92) {
        bestLen = len;
        best = el;
      }
      el = el.parentElement;
    }
    if (bestLen < 100) {
      el = addBtn;
      bestLen = 0;
      for (let i = 0; i < 36 && el; i++) {
        const len = (el.innerText || '').trim().length;
        if (len > bestLen) {
          bestLen = len;
          best = el;
        }
        el = el.parentElement;
      }
    }
    return best;
  }

  function deepQueryAll(selector) {
    const out = [];
    const seen = new Set();
    function visit(root) {
      if (!root || !root.querySelectorAll) return;
      root.querySelectorAll(selector).forEach(el => {
        if (!seen.has(el)) { seen.add(el); out.push(el); }
      });
      root.querySelectorAll('*').forEach(el => {
        if (el.shadowRoot) visit(el.shadowRoot);
      });
    }
    visit(document.documentElement);
    return out;
  }

  const addBtn = findAddBtn();
  const panel = sidebarShellFromAddBtn(addBtn);
  const rowSel = '[role="listitem"], mat-list-item, mat-list-option, [role="option"], [role="treeitem"], [role="row"], [class*="mat-mdc-list-item"], [class*="mdc-list-item"]';
  const shallow = panel.querySelectorAll(rowSel).length;
  const deepAll = deepQueryAll(rowSel);
  const useLeftBand = (panel === document.body || panel === document.documentElement);
  const deepEls = deepAll.filter(el => {
    try {
      if (panel.contains(el)) return true;
      if (useLeftBand) {
        const r = el.getBoundingClientRect();
        return r.left < window.innerWidth * 0.48 && r.width > 8 && r.height > 6;
      }
      const r = el.getBoundingClientRect();
      return r.left < window.innerWidth * 0.48;
    } catch (e) {
      return false;
    }
  });

  function rowTitle(li) {
    const lid = li.getAttribute('aria-labelledby');
    if (lid) {
      const lab = document.getElementById(lid);
      if (lab) return (lab.textContent || '').trim();
    }
    let t = (li.innerText || li.textContent || '').trim();
    return t.replace(/more_vert|description|drive_pdf|video_youtube|video_audio_call|More|Mer/gi, ' ')
      .replace(/\s+/g, ' ').trim();
  }

  function okName(t) {
    if (!t || t.length < 2) return false;
    const low = t.toLowerCase();
    if (IGNORE.has(low) || UI_LINE.has(low)) return false;
    if (/^https?:\/\//i.test(t)) return false;
    if (/^[\d\s.]+$/i.test(t)) return false;
    return true;
  }

  const fromRows = [];
  const seen = new Set();
  function pushRow(t) {
    if (!okName(t)) return;
    const k = t.toLowerCase();
    if (seen.has(k)) return;
    seen.add(k);
    fromRows.push(t);
  }

  deepEls.forEach(li => pushRow(rowTitle(li)));

  if (fromRows.length === 0) {
    panel.querySelectorAll('[aria-label*="More"], [aria-label*="Mer"]').forEach(btn => {
      const row = btn.closest('[role="listitem"], li, [class*="row"], [class*="item"], [class*="mat-list"]');
      if (!row) return;
      let full = (row.innerText || row.textContent || '').trim();
      full = full.replace(/more_vert|description|drive_pdf|video_youtube|video_audio_call|More|Mer/gi, ' ')
        .replace(/\s+/g, ' ').trim();
      pushRow(full);
    });
  }

  const panelText = (panel.innerText || '');
  const fromLines = [];
  if (fromRows.length === 0 && addBtn) {
    for (const line of panelText.split(/\r?\n/)) {
      const s = line.trim();
      if (s.length < 2) continue;
      const low = s.toLowerCase();
      if (IGNORE.has(low) || UI_LINE.has(low)) continue;
      if (/more_vert|description|drive_pdf|video_youtube/i.test(s)) continue;
      if (okName(s)) fromLines.push(s);
    }
  }

  return {
    hasAddButton: !!addBtn,
    counts: {
      shallowRowsInPanel: shallow,
      deepRowsUnion: deepEls.length,
      deepRowsAllDocument: deepAll.length,
      fromRowsUnique: fromRows.length,
      fromLines: fromLines.length,
      panelTextChars: panelText.length
    },
    namesPrimary: fromRows,
    namesFallbackLines: fromLines,
    panelTextSnippet: panelText.slice(0, 6000),
    panelTextFull: panelText.length > 500000 ? panelText.slice(0, 500000) : panelText
  };
}
"""


def playwright_collect_sources_multi(frame: Frame) -> tuple[list[str], dict]:
    """Try several selectors on the notebook frame; keep the result set with the most plausible titles."""
    diagnostics: dict = {}
    best: list[str] = []
    selectors = [
        '[role="listbox"] [role="option"]',
        '[role="tree"] [role="treeitem"]',
        'mat-list-item',
        '[role="listitem"]',
        '[class*="mat-mdc-list-item"]',
    ]
    for sel in selectors:
        loc = frame.locator(sel)
        try:
            n = min(loc.count(), 400)
        except Exception as e:
            diagnostics[sel] = {"error": str(e)}
            continue
        texts: list[str] = []
        for i in range(n):
            try:
                el = loc.nth(i)
                if not el.is_visible(timeout=400):
                    continue
                t = el.inner_text(timeout=1200).strip()
                t = re.sub(r"\s+", " ", t).strip()
                if len(t) > 1:
                    texts.append(t)
            except Exception:
                continue
        kept = list(dict.fromkeys(t for t in texts if is_likely_source_name(t)))
        diagnostics[sel] = {"dom_count": n, "kept_after_filter": len(kept)}
        if len(kept) > len(best):
            best = kept
    return best, diagnostics


def playwright_listitem_texts(frame: Frame, limit: int = 500) -> tuple[int, list[str]]:
    """Factual: how many [role=listitem] in this frame and first N inner_text values."""
    loc = frame.locator('[role="listitem"]')
    try:
        n = loc.count()
    except Exception:
        return -1, []
    texts: list[str] = []
    cap = min(n, limit)
    for i in range(cap):
        try:
            t = loc.nth(i).inner_text(timeout=1200).strip()
            t = re.sub(r"\s+", " ", t).strip()
            if t:
                texts.append(t)
        except Exception:
            break
    return n, texts


def verify_parser_against_saved_proof() -> int:
    """
    No browser: load notebook_sources_proof.json and assert extract_sources_from_panel_text
    recovers real filenames from frame[0] panel_text_snippet.
    """
    if not OUT_PATH.exists():
        print(f"[FAIL] {OUT_PATH.name} not found. Run this script once with Chrome to generate it.")
        return 1
    with open(OUT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    snippet = (data.get("frames") or [{}])[0].get("panel_text_snippet") or ""
    if len(snippet) < 200:
        print(f"[FAIL] {OUT_PATH.name} has no usable panel_text_snippet in frame[0].")
        return 1
    all_rows = extract_sources_from_panel_text(snippet, unique=False)
    uniq = extract_sources_from_panel_text(snippet, unique=True)
    print(
        f"[VERIFY] Parsed {len(all_rows)} row(s), {len(uniq)} unique title(s) from saved panel_text_snippet."
    )
    checks = [
        ("01_Welcome_What_GenAI_Can_Do_Today.txt", "sample .txt"),
        ("Course Schedule HI Gen AI ss 2026.pdf", "sample .pdf"),
        ("Animals_Speaking_in_Generated_Video.mp3", "sample .mp3"),
    ]
    for needle, label in checks:
        if needle not in uniq:
            print(f"[FAIL] Missing {label}: {needle!r}")
            return 1
    bad = {"markdown", "video_audio_call"}
    got = {n.lower() for n in uniq}
    if bad & got:
        print(f"[FAIL] Icon lines wrongly included: {bad & got}")
        return 1
    assert len(all_rows) >= len(uniq), "duplicate rows expected when UI repeats filenames"
    print("[VERIFY] Assertions passed. Parser can read source titles from panel text.")
    print("         First unique titles:", uniq[:5])
    return 0


def pick_page(p):
    browser = p.chromium.connect_over_cdp(CDP_URL)
    page = None
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if "notebooklm.google.com" in (pg.url or ""):
                page = pg
                break
        if page:
            break
    if page is None:
        page = browser.contexts[0].pages[0]
    return browser, page


def main() -> int:
    ap = argparse.ArgumentParser(description="Read NotebookLM Sources via CDP (proof JSON).")
    ap.add_argument("--url", default=DEFAULT_NOTEBOOK_URL, help="Notebook URL to open")
    ap.add_argument(
        "--verify",
        action="store_true",
        help="Test title parser on saved notebook_sources_proof.json only (no Chrome).",
    )
    args = ap.parse_args()
    if args.verify:
        return verify_parser_against_saved_proof()
    notebook_url = args.url.strip()

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cdp_url": CDP_URL,
        "notebook_url": notebook_url,
        "output_file": str(OUT_PATH),
        "frames": [],
        "chosen_frame_index": None,
        "sources": [],
        "notes": [],
    }

    with sync_playwright() as p:
        try:
            _, page = pick_page(p)
        except Exception as e:
            print(f"[FAIL] CDP connect: {e}")
            print("Start Chrome with: --remote-debugging-port=9222")
            return 1

        print(f"--- CDP {CDP_URL} ---")
        page.goto(notebook_url, wait_until="domcontentloaded")
        page.wait_for_load_state("load")

        try:
            page.get_by_role(
                "button",
                name=re.compile(r"(\+\s*)?Add\s+source|Lägg\s+till\s+källa", re.I),
            ).first.wait_for(state="visible", timeout=45_000)
        except Exception as e:
            print(f"[FAIL] Add source button not visible (wrong tab / not logged in?): {e}")
            return 1

        try:
            tab = page.get_by_role("tab", name=re.compile(r"^Sources\b|^Källor\b", re.I)).first
            if tab.is_visible(timeout=2500):
                tab.click(timeout=5000)
                page.wait_for_timeout(400)
        except Exception:
            pass

        page.wait_for_timeout(1500)

        try:
            scroll_info = page.evaluate(sidebar_scroll_js())
            report["sidebar_scroll"] = scroll_info
        except Exception as e:
            report["sidebar_scroll"] = {"error": str(e)}

        page.wait_for_timeout(600)

        # Scroll likely panel (best-effort)
        try:
            add = page.get_by_role(
                "button",
                name=re.compile(r"(\+\s*)?Add\s+source|Lägg\s+till\s+källa", re.I),
            ).first
            panel = page.locator("section, [role='region'], aside, nav").filter(has=add).first
            for _ in range(10):
                panel.evaluate("el => el.scrollBy(0, 400)")
                page.wait_for_timeout(200)
        except Exception:
            pass

        best_idx = -1
        best_score = -1
        best_names: list[str] = []

        for i, fr in enumerate(page.frames):
            url = ""
            try:
                url = fr.url or ""
            except Exception:
                url = "(unknown)"

            entry: dict = {"index": i, "url": url, "evaluate_error": None, "playwright": {}}

            data = None
            try:
                data = fr.evaluate(scrape_frame_facts())
            except Exception as e:
                entry["evaluate_error"] = str(e)

            if isinstance(data, dict):
                if is_eligible_notebook_frame(url):
                    try:
                        data["panelTextFull"] = fr.evaluate(get_sidebar_panel_text_js())
                    except Exception:
                        pass
                entry["facts"] = data.get("counts", {})
                entry["has_add_button"] = data.get("hasAddButton")
                names = list(data.get("namesPrimary") or [])
                if not names:
                    names = list(data.get("namesFallbackLines") or [])
                eligible = is_eligible_notebook_frame(url)
                entry["eligible_for_sources"] = eligible
                full_panel = data.get("panelTextFull") or ""
                entry["panel_text_full_length"] = len(full_panel)
                entry["sources_from_panel_text_count"] = 0
                if eligible and full_panel.strip():
                    # Include every matching line (NotebookLM repeats the same filename on multiple rows).
                    panel_sources = extract_sources_from_panel_text(full_panel, unique=False)
                    entry["sources_from_panel_text_count"] = len(panel_sources)
                    entry["sources_from_panel_text_unique_count"] = len({x.lower() for x in panel_sources})
                    if panel_sources:
                        names = panel_sources
                        entry["sources_extraction"] = "panel_text_full"

                entry["panel_snippet_chars"] = len(data.get("panelTextSnippet") or "")
                # Keep snippet in report for proof (truncate in file if huge)
                snip = data.get("panelTextSnippet") or ""
                entry["panel_text_snippet"] = snip[:8000]

                pw_n, pw_texts = playwright_listitem_texts(fr)
                entry["playwright"] = {
                    "listitem_count": pw_n,
                    "listitem_sample_texts": pw_texts[:15],
                }

                score = len(names)

                if score == 0 and eligible and pw_texts:
                    cleaned = []
                    seen = set()
                    for t in pw_texts:
                        t = re.sub(
                            r"(?i)more_vert|description|drive_pdf|video_youtube|video_audio_call",
                            " ",
                            t,
                        )
                        t = re.sub(r"\s+", " ", t).strip()
                        if is_likely_source_name(t) and len(t) > 2:
                            k = t.lower()
                            if k not in seen:
                                seen.add(k)
                                cleaned.append(t)
                    names = cleaned
                    entry["names_from_playwright_locator"] = True
                    score = len(names)

                entry["names_raw_count"] = len(names)

                eff_score = len(names) if eligible else -1
                if eff_score > best_score:
                    best_score = eff_score
                    best_idx = i
                    best_names = list(names)
            else:
                entry["facts"] = None

            report["frames"].append(entry)

        for i, fr in enumerate(page.frames):
            if not is_eligible_notebook_frame(fr.url or ""):
                continue
            extra, diag = playwright_collect_sources_multi(fr)
            report["notebook_playwright_multi"] = {"frame_index": i, "diagnostics": diag, "count": len(extra)}
            if len(extra) > len(best_names):
                best_names = extra
                best_idx = i
            break

        # Final filter: keep order and duplicates (same title may appear on multiple rows).
        final: list[str] = []
        for s in best_names:
            s = (s or "").strip()
            if not is_likely_source_name(s):
                continue
            final.append(s)

        report["chosen_frame_index"] = best_idx if best_idx >= 0 else None
        report["sources"] = final
        report["sources_unique"] = list(dict.fromkeys(final))
        report["total_rows"] = len(final)
        report["unique_title_count"] = len(report["sources_unique"])
        if final and any(fe.get("sources_extraction") == "panel_text_full" for fe in report["frames"]):
            report["notes"].append(
                "Sources parsed from sidebar panel text (extensions / https / long titles). "
                "DOM role=listitem count was 0 for this NotebookLM UI. "
                "`sources` lists every row (duplicates allowed); `sources_unique` dedupes by title."
            )

        if best_idx < 0 or not final:
            report["notes"].append(
                "No frame produced source names; check panel_text_snippet per frame and login/notebook URL."
            )

        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        # Console: facts only
        print(f"[OK] Wrote {OUT_PATH.name}")
        print(f"     Notebook: {notebook_url}")
        for fe in report["frames"]:
            idx = fe["index"]
            facts = fe.get("facts") or {}
            pw = fe.get("playwright") or {}
            err = fe.get("evaluate_error")
            line = (
                f"     frame[{idx}] eligible={fe.get('eligible_for_sources')} "
                f"shallow={facts.get('shallowRowsInPanel')} deep={facts.get('deepRowsUnion')} "
                f"deepDoc={facts.get('deepRowsAllDocument')} "
                f"panel_lines={fe.get('sources_from_panel_text_count', 0)} "
                f"names={fe.get('names_raw_count', 0)} pw_listitem={pw.get('listitem_count')}"
            )
            if err:
                line += f" ERR={err[:80]}"
            print(line)
        print(
            f"     chosen_frame_index={report['chosen_frame_index']}  "
            f"rows={len(final)} unique_titles={report['unique_title_count']}"
        )
        if final:
            preview = final[:8]
            for t in preview:
                print(f"       - {t}")
            if len(final) > 8:
                print(f"       ... +{len(final) - 8} more (see JSON)")

        return 0 if final else 2


if __name__ == "__main__":
    sys.exit(main())
