"""
NotebookLM — delete sources.

  python delete_agent.py              Delete names listed in comparison_review.json (one row per name by default).
  python delete_agent.py --all-dupes  Same, but remove every duplicate row for each name (up to a safety cap).
  python delete_agent.py --dedupe     Scroll the Sources list, record every row to sources_panel_catalog.json, then remove duplicate titles (keep one row each).

Only the per-row overflow (⋮) menu is used — never row/title clicks.
Chrome must be running with:  --remote-debugging-port=9222
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, Union

from playwright.sync_api import Frame, Page, sync_playwright

from config import CDP_URL, PROJECT_URL
from notebook_ready import ADD_SOURCE_BTN, pick_notebook_page, resolve_sidebar_frame
from notebooklm_sources import (
    extract_sources_from_panel_text,
    get_sidebar_panel_text_js,
    is_eligible_notebook_frame,
    is_notebook_placeholder_title,
    sidebar_scroll_js,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REVIEW_PATH = SCRIPT_DIR / "comparison_review.json"
CATALOG_PATH = SCRIPT_DIR / "sources_panel_catalog.json"
DOM_DEBUG_PATH = SCRIPT_DIR / "delete_agent_dom_debug.txt"

MAX_PER_TITLE = 20  # safety cap when deleting copies of the same title (--all-dupes / dedupe)
# Plan mode deletes at most one row per review name unless --all-dupes is set.
PLAN_DELETES_PER_NAME_DEFAULT = 1
# Align with compare_sources: map plan old_name → actual panel string (strict to avoid wrong row).
_MATCH_MIN = 0.55


def normalize_title(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\.[a-zA-Z0-9]{1,5}$", "", s)
    return re.sub(r"[_\s\-]+", " ", s.lower()).strip()


def best_panel_title(plan: str, panel_titles: list[str]) -> Optional[str]:
    """Pick the on-screen title that best matches the review file name.

    Never returns on the first loose substring hit — that mapped webinar MP3s to .txt files.
    """
    np = normalize_title(plan)
    if not np:
        return None
    for c in panel_titles:
        if c and normalize_title(c) == np:
            return c

    substr: list[tuple[str, float]] = []
    for c in panel_titles:
        if not c:
            continue
        nc = normalize_title(c)
        if not nc:
            continue
        if len(np) >= 12 and np in nc:
            substr.append((c, SequenceMatcher(None, np, nc).ratio()))
        elif len(nc) >= 12 and nc in np:
            substr.append((c, SequenceMatcher(None, np, nc).ratio()))
    if substr:
        substr.sort(key=lambda x: (-x[1], -len(x[0])))
        best_c, best_r = substr[0]
        if best_r >= 0.42:
            return best_c

    best: Optional[str] = None
    best_r = 0.0
    for c in panel_titles:
        if not c:
            continue
        nc = normalize_title(c)
        if not nc:
            continue
        r = SequenceMatcher(None, np, nc).ratio()
        if r > best_r:
            best_r = r
            best = c
    if best is not None and best_r >= _MATCH_MIN:
        return best
    return None


# ---------------------------------------------------------------------------
# comparison_review.json
# ---------------------------------------------------------------------------


def _action(entry: dict) -> str:
    return (entry.get("action") or "").strip().upper()


def names_from_review() -> list[str]:
    if not REVIEW_PATH.exists():
        print(f"[WARN] {REVIEW_PATH.name} not found.")
        return []
    with open(REVIEW_PATH, encoding="utf-8") as f:
        review = json.load(f)
    out: list[str] = []
    seen: set[str] = set()
    for pair in review.get("pairs", []):
        old = pair.get("old_name")
        if old and is_notebook_placeholder_title(old):
            print(f"[SKIP] Not a real source name (empty-state UI): {old!r}", flush=True)
            continue
        if old and _action(pair) in ("REPLACE", "DELETE") and old not in seen:
            seen.add(old)
            out.append(old)
    for item in review.get("current_only", []):
        name = item.get("name")
        if name and is_notebook_placeholder_title(name):
            print(f"[SKIP] Not a real source name (empty-state UI): {name!r}", flush=True)
            continue
        if name and _action(item) == "DELETE" and name not in seen:
            seen.add(name)
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------


def connect_notebook_page(p) -> Page:
    print(f"--- CDP {CDP_URL} ---")
    browser = p.chromium.connect_over_cdp(CDP_URL)
    page = pick_notebook_page(browser)
    print("[OK] Connected.")
    page.goto(PROJECT_URL, wait_until="domcontentloaded")
    page.wait_for_load_state("load")
    return page


def ready_notebook(page: Page) -> None:
    try:
        page.bring_to_front()
    except Exception:
        pass
    if resolve_sidebar_frame(page) is None:
        raise RuntimeError(
            "Add source / Lägg till not found (main frame or iframe). "
            "Open the notebook in Chrome (9222), sign in, Sources visible, then retry."
        )
    page.wait_for_timeout(2000)
    try:
        page.evaluate(sidebar_scroll_js())
    except Exception:
        pass
    page.wait_for_timeout(600)


def find_best_sources_frame(page: Page) -> Frame:
    """
    page.evaluate() only runs in one frame. NotebookLM often has 0 role=listitem nodes; the
    reader script picks the frame whose sidebar panel text parses to the most rows — same idea
    here so deletes and catalog use the real Sources list.
    """
    js_probe = r"""() => {
      const addBtn = Array.from(document.querySelectorAll('button, [role="button"], a[role="button"]')).find(
        b => /add\s+source|lägg\s+till|sources|källor|upload/i.test(
          (b.getAttribute('aria-label') || '') + ' ' + (b.textContent || '')
        )
      );
      const sel = '.single-source-container, [class*="single-source"], [role="listitem"], mat-list-item, mat-list-option, [class*="mdc-list-item"], [class*="mat-mdc-list-item"]';
      const seen = new Set();
      function visit(root) {
        if (!root || !root.querySelectorAll) return;
        root.querySelectorAll(sel).forEach(el => { seen.add(el); });
        root.querySelectorAll('*').forEach(el => {
          if (el.shadowRoot) visit(el.shadowRoot);
        });
      }
      visit(document.documentElement);
      return { hasAdd: !!addBtn, n: seen.size };
    }"""

    def probe(fr: Frame) -> Optional[tuple[Frame, int, bool]]:
        try:
            r = fr.evaluate(js_probe)
            if isinstance(r, dict):
                return (fr, int(r.get("n") or 0), bool(r.get("hasAdd")))
        except Exception:
            return None
        return None

    scored: list[tuple[Frame, int, bool]] = []
    for fr in page.frames:
        try:
            url = fr.url or ""
        except Exception:
            url = ""
        if not is_eligible_notebook_frame(url):
            continue
        row = probe(fr)
        if row:
            scored.append(row)
    with_add = [t for t in scored if t[2]]
    if not with_add:
        scored = []
        for fr in page.frames:
            row = probe(fr)
            if row:
                scored.append(row)
        with_add = [t for t in scored if t[2]]
    if not with_add:
        try:
            return page.main_frame
        except Exception:
            return page.frames[0]

    if len(with_add) == 1:
        return with_add[0][0]

    best_fr: Optional[Frame] = None
    best_n = -1
    for fr, _, _ in with_add:
        try:
            full = str(fr.evaluate(get_sidebar_panel_text_js()) or "")
            n = len(extract_sources_from_panel_text(full, unique=False))
        except Exception:
            n = 0
        if n > best_n:
            best_n = n
            best_fr = fr
    return best_fr if best_fr is not None else with_add[0][0]


def write_dom_debug(page: Page) -> None:
    """When scraping finds nothing, dump per-frame hints so selectors can be fixed against a real DOM."""
    lines: list[str] = []
    js_counts = r"""() => {
      const addBtn = Array.from(document.querySelectorAll('button, [role="button"], a[role="button"]')).find(
        b => /add\s+source|lägg\s+till|sources|källor|upload/i.test(
          (b.getAttribute('aria-label') || '') + ' ' + (b.textContent || '')
        )
      );
      const sel = '[role="listitem"], mat-list-item, mat-list-option';
      const shallow = document.querySelectorAll(sel).length;
      const seen = new Set();
      function visit(root) {
        if (!root || !root.querySelectorAll) return;
        root.querySelectorAll(sel).forEach(el => { seen.add(el); });
        root.querySelectorAll('*').forEach(el => {
          if (el.shadowRoot) visit(el.shadowRoot);
        });
      }
      visit(document.documentElement);
      const panel = addBtn
        ? (addBtn.closest('section, aside, nav, [role="region"], [class*="sidebar"], [class*="panel"]') || document.body)
        : null;
      const snippet = (panel && panel.innerText) ? String(panel.innerText).slice(0, 12000) : String(document.body.innerText || '').slice(0, 12000);
      return { hasAdd: !!addBtn, shallow, deep: seen.size, snippet };
    }"""
    for i, fr in enumerate(page.frames):
        try:
            u = fr.url or ""
            c = fr.evaluate(js_counts)
            lines.append(f"--- frame[{i}] url={u!r} ---")
            if isinstance(c, dict):
                lines.append(
                    f"  hasAdd={c.get('hasAdd')} shallow_list={c.get('shallow')} deep_list={c.get('deep')}"
                )
                sn = c.get("snippet") or ""
                if isinstance(sn, str) and sn.strip():
                    lines.append("  --- panel/body text (first 12k) ---")
                    lines.append(sn)
        except Exception as e:
            lines.append(f"--- frame[{i}] ERROR: {e} ---")
    try:
        with open(DOM_DEBUG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"[DEBUG] Wrote {DOM_DEBUG_PATH.name} — open it to see what each frame exposes.")
    except OSError as e:
        print(f"[WARN] Could not write debug file: {e}")


def sources_panel(page: Page):
    try:
        add = page.get_by_role(
            "button", name=ADD_SOURCE_BTN,
        ).first
        return page.locator("section, [role='region'], aside, nav").filter(has=add).first
    except Exception:
        return None


def _scroll_sources_shell(
    frame: Frame, *, to_top: bool = False, dy: int = 0, scroll_fraction: float | None = None
) -> dict | None:
    """Scroll the virtualized Sources list inside ``frame`` (same shell as export/catalog)."""
    return frame.evaluate(
        r"""
    ({ toTop, dy, frac }) => {
      const addBtn = Array.from(document.querySelectorAll('button, [role="button"], a[role="button"]')).find(
        b => /add\s+source|lägg\s+till|sources|källor|upload/i.test(
          (b.getAttribute('aria-label') || '') + ' ' + (b.textContent || '')
        )
      );
      if (!addBtn) return null;
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
          if (len > bestLen) { bestLen = len; best = el; }
          el = el.parentElement;
        }
      }
      const shell = best;
      let scroller = shell;
      let maxRange = shell.scrollHeight - shell.clientHeight;
      for (const node of shell.querySelectorAll('*')) {
        const r = node.scrollHeight - node.clientHeight;
        if (r > maxRange + 8) {
          maxRange = r;
          scroller = node;
        }
      }
      if (toTop) scroller.scrollTop = 0;
      else if (typeof frac === 'number' && frac >= 0) {
        const range = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
        scroller.scrollTop = Math.floor(range * frac);
      } else if (dy) scroller.scrollBy(0, dy);
      return {
        scrollTop: scroller.scrollTop,
        scrollHeight: scroller.scrollHeight,
        clientHeight: scroller.clientHeight,
      };
    }
    """,
        {"toTop": to_top, "dy": dy, "frac": scroll_fraction},
    )


def scroll_panel(page: Page, dy: int, *, sources_frame: Frame | None = None) -> None:
    if sources_frame is not None:
        _scroll_sources_shell(sources_frame, dy=dy)
    else:
        panel = sources_panel(page)
        if panel:
            try:
                panel.evaluate("(el, d) => el.scrollBy(0, d)", dy)
            except Exception:
                pass
    page.wait_for_timeout(120)


# ---------------------------------------------------------------------------
# Read titles from the Sources list (one entry per row; duplicates allowed)
# ---------------------------------------------------------------------------


def _snapshot_row_titles_js() -> str:
    """Same strategy as export_current_sources.py: light DOM + shadow roots + menu-row + innerText lines."""
    return r"""
() => {
  const IGNORE = new Set([
    "description", "more_vert", "drive_pdf", "video_youtube", "video_audio_call",
    "more", "mer", "add", "sources", "källor", "insert_drive_file", "link",
    "content_copy", "upload", "folder", "image", "article",
    "keyboard_arrow_down", "expand_more", "expand_less", "arrow_drop_down"
  ]);
  const UI_LINE = new Set([
    "add source", "add sources", "sources", "källor", "lägg till källa", "upload files",
    "websites", "drive", "copied text", "ladda upp", "webbplatser", "select all sources",
    "välj alla källor", "keyboard arrow down"
  ]);

  function findAddBtn() {
    return Array.from(document.querySelectorAll('button, [role="button"], a[role="button"]')).find(
      b => /add\s+source|lägg\s+till|sources|källor|upload/i.test(
        (b.getAttribute('aria-label') || '') + ' ' + (b.textContent || '')
      )
    );
  }

  function deepQuerySelectorAll(selector) {
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
  let sourcesPanel = addBtn
    ? (addBtn.closest('section, [role="region"], aside, nav, [class*="sidebar"], [class*="panel"], [class*="source"]') || document.body)
    : document.body;
  if (addBtn && sourcesPanel) {
    let p = addBtn.parentElement;
    for (let i = 0; i < 14 && p; i++) {
      if (p.scrollHeight > p.clientHeight + 60) {
        const testSel = '.single-source-container, [class*="single-source"], [role="listitem"], mat-list-item, mat-list-option, [class*="list-item"]';
        if (p.querySelector(testSel) || deepQuerySelectorAll(testSel).some(el => p.contains(el))) {
          sourcesPanel = p;
          break;
        }
      }
      p = p.parentElement;
    }
  }

  function titleFrom(el) {
    const cb = el.querySelector ? el.querySelector('input[type="checkbox"]') : null;
    if (cb) {
      const al = (cb.getAttribute('aria-label') || '').trim();
      if (al.length >= 2) return al;
    }
    const lid = el.getAttribute('aria-labelledby');
    if (lid) {
      const lab = document.getElementById(lid);
      if (lab) return (lab.textContent || '').trim();
    }
    let t = (el.innerText || el.textContent || '').trim();
    t = t.replace(/more_vert|description|drive_pdf|video_youtube|video_audio_call|More|Mer/gi, ' ').replace(/\s+/g, ' ').trim();
    return t;
  }

  function looksLikeSourceTitle(t) {
    if (!t || t.length < 2) return false;
    const low = t.toLowerCase();
    if (IGNORE.has(low)) return false;
    if (UI_LINE.has(low)) return false;
    if (/^https?:\/\//i.test(t)) return false;
    if (/^[\d\s.]+$/i.test(t)) return false;
    if (/select all sources|välj alla källor/i.test(t)) return false;
    return true;
  }

  const rowSel = '.single-source-container, [class*="single-source"], [role="listitem"], mat-list-item, mat-list-option, a[role="listitem"], [class*="mat-mdc-list-item"], [class*="mdc-list-item"], [class*="list-item"], div[role="listitem"]';
  const seenEl = new Set();
  const rows = [];
  let idx = 0;

  function pushEl(el) {
    if (seenEl.has(el)) return;
    const t = titleFrom(el);
    if (!looksLikeSourceTitle(t)) return;
    seenEl.add(el);
    const id = el.getAttribute('aria-labelledby') || el.id || ('anon_' + (idx++) + '_' + t.slice(0, 48));
    rows.push({ row_id: id, title: t });
  }

  sourcesPanel.querySelectorAll(rowSel).forEach(el => pushEl(el));
  deepQuerySelectorAll(rowSel).forEach(el => {
    if (sourcesPanel.contains(el) || el.getBoundingClientRect().left < window.innerWidth * 0.52) pushEl(el);
  });

  if (rows.length === 0) {
    const withMenu = sourcesPanel.querySelectorAll(
      '[aria-label*="More"], [aria-label*="Mer"], button[aria-label], [class*="more"]'
    );
    const seenRow = new Set();
    withMenu.forEach(btn => {
      const row = btn.closest('[role="listitem"], li, [class*="row"], [class*="item"], [class*="source"], [class*="mat-list"]');
      if (!row || seenRow.has(row)) return;
      seenRow.add(row);
      let full = (row.innerText || row.textContent || '').trim();
      full = full.replace(/more_vert|description|drive_pdf|video_youtube|video_audio_call|More|Mer/gi, '').replace(/\s+/g, ' ').trim();
      if (looksLikeSourceTitle(full)) {
        const id = row.getAttribute('aria-labelledby') || row.id || ('menu_' + (idx++) + '_' + full.slice(0, 40));
        rows.push({ row_id: id, title: full });
      }
    });
  }

  if (rows.length === 0 && addBtn) {
    const panel = addBtn.closest('section, aside, nav, [role="region"], [class*="sidebar"], [class*="panel"]') || sourcesPanel;
    const raw = (panel.innerText || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    let li = 0;
    for (const line of raw) {
      if (line.length < 2) continue;
      const low = line.toLowerCase();
      if (IGNORE.has(low) || UI_LINE.has(low)) continue;
      if (/^https?:\/\//i.test(line)) continue;
      if (/more_vert|description|drive_pdf|video_youtube|video_audio_call/i.test(line)) continue;
      if (!looksLikeSourceTitle(line)) continue;
      rows.push({ row_id: 'line_' + (li++) + '_' + line.slice(0, 60), title: line });
    }
  }

  return rows;
}
"""


def _snapshot_rows_playwright(scope: Union[Frame, Page]) -> list[dict]:
    """If DOM evaluate finds nothing, try Playwright locators (different visibility rules)."""
    out: list[dict] = []
    seen_t: set[str] = set()
    for sel in (".single-source-container", "mat-list-item", "[role='listitem']", "mat-list-option"):
        loc = scope.locator(sel)
        try:
            n = min(loc.count(), 250)
        except Exception:
            continue
        for i in range(n):
            try:
                el = loc.nth(i)
                if not el.is_visible(timeout=400):
                    continue
                t = el.inner_text(timeout=800).strip()
                t = re.sub(
                    r"(?i)more_vert|description|drive_pdf|video_youtube|video_audio_call|More|Mer",
                    " ",
                    t,
                )
                t = re.sub(r"\s+", " ", t).strip()
                if len(t) < 2 or t.lower() in (
                    "description",
                    "more_vert",
                    "sources",
                    "källor",
                ):
                    continue
                if t in seen_t:
                    continue
                seen_t.add(t)
                rid = f"pw_{sel}_{i}"
                out.append({"row_id": rid, "title": t})
            except Exception:
                continue
        if len(out) > 0:
            break
    return out


def _collect_row_catalog_dom_fallback(page: Page, fr: Frame) -> list[dict]:
    """Older path: listitem / mat-list mat rows when present."""
    seen: set[str] = set()
    catalog: list[dict] = []
    panel = sources_panel(page)
    if panel:
        try:
            panel.evaluate("el => { el.scrollTop = 0; }")
        except Exception:
            pass
    page.wait_for_timeout(250)

    stagnant = 0
    prev = -1
    for _ in range(55):
        try:
            batch = fr.evaluate(_snapshot_row_titles_js())
        except Exception:
            batch = []
        if not isinstance(batch, list):
            batch = []
        if not batch and not catalog:
            batch = _snapshot_rows_playwright(fr)
        for row in batch:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("row_id") or "")
            title = (row.get("title") or "").strip()
            if not title:
                continue
            if rid not in seen:
                seen.add(rid)
                catalog.append({"row_id": rid, "title": title})
        if len(catalog) == prev:
            stagnant += 1
            if stagnant >= 6:
                break
        else:
            stagnant = 0
        prev = len(catalog)
        scroll_panel(page, 440)

    if panel:
        try:
            panel.evaluate("el => { el.scrollTop = 0; }")
        except Exception:
            pass
    page.wait_for_timeout(200)
    return catalog


def collect_row_catalog(page: Page, frame: Optional[Frame] = None) -> list[dict]:
    """
    Primary: sidebar shell innerText + same line parser as read_notebook_sources / notebooklm_sources.
    NotebookLM often has 0 role=listitem nodes; panel text still lists every file/URL.
    """
    fr = frame or find_best_sources_frame(page)
    full_text = ""
    try:
        for _ in range(4):
            try:
                fr.evaluate(sidebar_scroll_js())
            except Exception:
                pass
            page.wait_for_timeout(400)
        full_text = str(fr.evaluate(get_sidebar_panel_text_js()) or "")
    except Exception:
        full_text = ""

    titles = extract_sources_from_panel_text(full_text, unique=False) if full_text.strip() else []
    if titles:
        out: list[dict] = []
        for i, t in enumerate(titles):
            h = hashlib.md5(t.encode("utf-8")).hexdigest()[:12]
            out.append({"row_id": f"pt_{i}_{h}", "title": t.strip()})
        return out

    catalog = _collect_row_catalog_dom_fallback(page, fr)
    if not catalog:
        write_dom_debug(page)
    return catalog


def save_catalog(catalog: list[dict]) -> None:
    counts = Counter(r["title"] for r in catalog)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": len(catalog),
        "counts_by_title": dict(sorted(counts.items(), key=lambda x: (-x[1], x[0].lower()))),
        "rows": catalog,
    }
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[OK] Wrote {CATALOG_PATH.name} ({len(catalog)} row(s)).")


# ---------------------------------------------------------------------------
# Delete one row by title (overflow menu only)
# ---------------------------------------------------------------------------


def _find_and_tag_overflow(frame: Frame, title: str) -> bool:
    return frame.evaluate(
        r"""
    (name) => {
      document.querySelectorAll('[data-nlm-overflow]').forEach(el => el.removeAttribute('data-nlm-overflow'));
      const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
      const fold = (s) => norm(s).replace(/[_\-]+/g, ' ');
      const strip = (s) => (s || '').replace(/\.(mp3|m4a|wav|txt|pdf|docx?)$/i, '');
      const nFold = fold(name);
      const nStrip = fold(strip(name));

      function rowText(li) {
        // Current NotebookLM: row title is the checkbox aria-label (most accurate).
        const cb = li.querySelector('input[type="checkbox"]');
        if (cb) {
          const al = (cb.getAttribute('aria-label') || '').trim();
          if (al.length >= 2) return al;
        }
        const lid = li.getAttribute('aria-labelledby');
        if (lid) {
          const el = document.getElementById(lid);
          if (el) return (el.textContent || '').trim();
        }
        return (li.innerText || li.textContent || '').trim();
      }
      function commonPrefixLen(a, b) {
        const n = Math.min(a.length, b.length);
        let i = 0;
        while (i < n && a[i] === b[i]) i++;
        return i;
      }
      function match(raw) {
        let t = raw.replace(/more_vert|description|drive_pdf|video_youtube|video_audio_call/gi, ' ').replace(/\s+/g, ' ').trim();
        const tFold = fold(t);
        const tStrip = fold(strip(t));
        if (tFold === nFold || tStrip === nStrip) return true;
        if (nFold.length >= 6 && tFold.includes(nFold)) return true;
        if (nStrip.length >= 6 && tStrip.includes(nStrip)) return true;
        // Long slug titles can be truncated in row text; allow robust prefix evidence.
        const prefA = commonPrefixLen(nFold, tFold);
        const prefB = commonPrefixLen(nStrip, tStrip);
        if (prefA >= 18 || prefB >= 18) return true;
        if (nFold.length >= 24 && tFold.includes(nFold.slice(0, 24))) return true;
        if (nStrip.length >= 24 && tStrip.includes(nStrip.slice(0, 24))) return true;
        if (nFold.length >= 10 && tFold.length >= 8 && nFold.startsWith(tFold) && tFold.length >= Math.floor(nFold.length * 0.78))
          return true;
        return false;
      }
      function overflowBtn(li) {
        // Current NotebookLM: dedicated class, present in DOM without hover.
        const direct = li.querySelector('.source-item-more-button, button[class*="more-button"], button[class*="more_button"]');
        if (direct) return direct;
        for (const btn of li.querySelectorAll('button')) {
          const icon = (btn.querySelector('mat-icon, .mat-icon')?.textContent || '').trim().toLowerCase();
          if (icon === 'more_vert' || icon === 'more_horiz') return btn;
          const al = (btn.getAttribute('aria-label') || '').toLowerCase();
          if (!/open|öppna|visa|play|spela|add|upload|lägg/i.test(al) && /more|mer|menu|options|alternativ/.test(al))
            return btn;
        }
        return null;
      }

      function deepQuerySelectorAll(selector) {
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

      function sidebarShellFromAddBtn(btn) {
        if (!btn) return document.body;
        const bodyLen = (document.body.innerText || '').trim().length || 1;
        let best = btn;
        let bestLen = 0;
        let el = btn;
        for (let i = 0; i < 36 && el; i++) {
          const len = (el.innerText || '').trim().length;
          if (len > bestLen && len >= 100 && len <= bodyLen * 0.92) {
            bestLen = len;
            best = el;
          }
          el = el.parentElement;
        }
        if (bestLen < 100) {
          el = btn;
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

      const addBtn = Array.from(document.querySelectorAll('button, [role="button"], a[role="button"]')).find(
        b => /add\s+source|lägg\s+till|sources|källor|upload/i.test(
          (b.getAttribute('aria-label') || '') + ' ' + (b.textContent || '')
        )
      );
      const shell = sidebarShellFromAddBtn(addBtn);
      const rowSel = '.single-source-container, [class*="single-source"], [role="listitem"], mat-list-item, mat-list-option, a[role="listitem"], [class*="mat-mdc-list-item"], [class*="mdc-list-item"], [class*="list-item"], div[role="listitem"]';
      const seen = new Set();
      const items = [];
      function add(li) {
        if (seen.has(li)) return;
        seen.add(li);
        items.push(li);
      }
      shell.querySelectorAll(rowSel).forEach(add);
      deepQuerySelectorAll(rowSel).forEach(el => {
        if (shell.contains(el) || el.getBoundingClientRect().left < window.innerWidth * 0.52) add(el);
      });
      if (items.length === 0) {
        deepQuerySelectorAll(rowSel).forEach(add);
      }

      for (const li of items) {
        const raw = rowText(li);
        if (!match(raw)) continue;
        const btn = overflowBtn(li);
        if (!btn) continue;
        btn.setAttribute('data-nlm-overflow', '1');
        btn.scrollIntoView({ block: 'center', behavior: 'instant' });
        return true;
      }
      try {
        const tw = document.createTreeWalker(shell, NodeFilter.SHOW_TEXT, null);
        let textNode;
        let twSteps = 0;
        while (textNode = tw.nextNode()) {
          if (++twSteps > 12000) break;
          const raw = (textNode.textContent || '').replace(/\s+/g, ' ').trim();
          if (!raw || raw.length < 2) continue;
          if (!match(raw)) continue;
          let el = textNode.parentElement;
          for (let d = 0; d < 30 && el; d++) {
            const btn = overflowBtn(el);
            if (btn) {
              btn.setAttribute('data-nlm-overflow', '1');
              btn.scrollIntoView({ block: 'center', behavior: 'instant' });
              return true;
            }
            el = el.parentElement;
          }
        }
      } catch (e) {}
      return false;
    }
    """,
        title,
    )


def _click_tagged_overflow(page: Page, sources_frame: Frame) -> None:
    last_exc: Exception | None = None
    for root in (sources_frame, page):
        try:
            loc = root.locator("[data-nlm-overflow='1']").first
            loc.wait_for(state="attached", timeout=4_000)
            loc.scroll_into_view_if_needed(timeout=4_000)
            loc.click(timeout=8_000)
            return
        except Exception as e:
            last_exc = e
    # Kebab is CSS-hidden until hover; Playwright actionability refuses it.
    # Plain DOM .click() works fine on it (Angular handles untrusted events).
    try:
        clicked = sources_frame.evaluate(
            """() => {
              const b = document.querySelector("[data-nlm-overflow='1']");
              if (!b) return false;
              b.click();
              return true;
            }"""
        )
        if clicked:
            return
    except Exception as e:
        last_exc = e
    if last_exc:
        raise last_exc
    raise TimeoutError("overflow button not found")


def _recover_sources_ui(page: Page) -> None:
    """
    Close transient UI that blocks row overflow menus (details panes/dialogs/menus).
    Safe to call repeatedly before each delete attempt.
    """
    # Escape is the most reliable way to close menus/dialogs/description panes in NotebookLM.
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(140)
        except Exception:
            pass
    # Re-focus Sources tab if details pane stole focus (try all notebook frames).
    for fr in page.frames:
        try:
            fr.evaluate(
                r"""() => {
                  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
                  const src = tabs.find(t => /^(Sources|Källor)\b/i.test((t.textContent || '').trim()));
                  if (!src) return false;
                  const selected = src.getAttribute('aria-selected') === 'true'
                    || src.getAttribute('data-state') === 'active'
                    || /\bselected\b|\bactive\b/i.test(src.className || '');
                  if (!selected) src.click();
                  return true;
                }"""
            )
        except Exception:
            pass
    # If a close button is visible in overlays/side panels, click it as backup.
    for pat in (
        re.compile(r"Back|Tillbaka|Source|Källa|Guide", re.I),
        re.compile(r"Close|Stäng|Dismiss|Done", re.I),
        re.compile(r"×|✕|x$", re.I),
    ):
        try:
            page.get_by_role("button", name=pat).first.click(timeout=350)
            page.wait_for_timeout(120)
        except Exception:
            pass
    # Final sweep: close again in case first close switched view but left overlay open.
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(120)
    except Exception:
        pass


def _confirm_delete_dialog(page: Page) -> bool:
    page.wait_for_timeout(500)
    overlay = page.locator(".cdk-overlay-container")
    try:
        overlay.get_by_role("button", name=re.compile(r"Delete|Remove|Ta bort|Radera", re.I)).first.click(
            timeout=4000
        )
        return True
    except Exception:
        pass
    try:
        page.locator(
            ".cdk-overlay-container mat-dialog-actions button, .cdk-overlay-container .mat-mdc-dialog-actions button"
        ).last.click(timeout=4000)
        return True
    except Exception:
        return False


def _probe_delete_dom(frame: Frame) -> str:
    """Return delete strategy: listitem | overflow | panel_text."""
    try:
        info = frame.evaluate(
            r"""
        () => {
          const addBtn = Array.from(document.querySelectorAll('button, [role="button"], a[role="button"]')).find(
            b => /add\s+source|lägg\s+till|sources|källor|upload/i.test(
              (b.getAttribute('aria-label') || '') + ' ' + (b.textContent || '')
            )
          );
          if (!addBtn) return { listitems: 0, moreVert: 0 };
          const bodyLen = (document.body.innerText || '').trim().length || 1;
          let shell = addBtn;
          let bestLen = 0;
          let el = addBtn;
          for (let i = 0; i < 36 && el; i++) {
            const len = (el.innerText || '').trim().length;
            if (len > bestLen && len >= 100 && len <= bodyLen * 0.92) {
              bestLen = len;
              shell = el;
            }
            el = el.parentElement;
          }
          const rowSel = '.single-source-container, [class*="single-source"], [role="listitem"], mat-list-item, mat-list-option';
          let listitems = 0;
          try { listitems = shell.querySelectorAll(rowSel).length; } catch (e) {}
          if (!listitems) {
            try { listitems = document.querySelectorAll('.single-source-container, [class*="single-source"]').length; } catch (e) {}
          }
          let moreVert = 0;
          try { moreVert = document.querySelectorAll('.source-item-more-button, button[class*="more-button"]').length; } catch (e) {}
          shell.querySelectorAll('button, [role="button"]').forEach(btn => {
            const icon = (btn.querySelector('mat-icon, .mat-icon')?.textContent || '').trim().toLowerCase();
            if (icon === 'more_vert' || icon === 'more_horiz') moreVert++;
          });
          return { listitems, moreVert };
        }
        """
        )
    except Exception:
        return "panel_text"
    if not isinstance(info, dict):
        return "panel_text"
    if int(info.get("listitems") or 0) > 0:
        return "listitem"
    if int(info.get("moreVert") or 0) > 2:
        return "overflow"
    return "panel_text"


def _title_search_fragment(title: str) -> str:
    title = title.strip()
    if len(title) <= 72:
        return title
    return title[:60]


def _click_remove_after_source_open(page: Page, frame: Frame) -> bool:
    """After opening a source row, click Remove / Delete in pane, menu, or overlay."""
    scopes: list[Union[Page, Frame]] = [page, frame]
    menu_patterns = [
        re.compile(r"Remove source", re.I),
        re.compile(r"Delete source", re.I),
        re.compile(r"Remove|Ta bort|Radera", re.I),
    ]
    for scope in scopes:
        for pat in menu_patterns:
            for role in ("menuitem", "button"):
                try:
                    scope.get_by_role(role, name=pat).first.click(timeout=2500)
                    page.wait_for_timeout(400)
                    if _confirm_delete_dialog(page):
                        return True
                except Exception:
                    continue
    try:
        clicked = frame.evaluate(
            r"""
        () => {
          for (const b of document.querySelectorAll('button, [role="button"], [role="menuitem"]')) {
            const t = ((b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')).replace(/\s+/g, ' ').trim();
            if (!/remove source|delete source|ta bort|remove|delete|radera/i.test(t)) continue;
            const r = b.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) continue;
            b.click();
            return true;
          }
          return false;
        }
        """
        )
        if clicked:
            page.wait_for_timeout(400)
            return _confirm_delete_dialog(page)
    except Exception:
        pass
    return False


def _delete_via_detail_pane(page: Page, frame: Frame, title: str) -> bool:
    """NotebookLM text-only Sources panel: click the title row, then Remove source."""
    fragment = _title_search_fragment(title)
    try:
        loc = frame.get_by_text(fragment, exact=False).first
        loc.wait_for(state="visible", timeout=5000)
        loc.scroll_into_view_if_needed(timeout=5000)
        page.wait_for_timeout(150)
        loc.click(timeout=5000)
    except Exception:
        return False
    page.wait_for_timeout(800)
    if _click_remove_after_source_open(page, frame):
        page.wait_for_timeout(1500)
        _recover_sources_ui(page)
        return True
    _recover_sources_ui(page)
    return False


def _hover_row_by_text(frame: Frame, title: str) -> bool:
    """Hover a row containing the title to reveal inline controls (⋮)."""
    fragment = _title_search_fragment(title)
    try:
        loc = frame.get_by_text(fragment, exact=False).first
        loc.hover(timeout=2500)
        return True
    except Exception:
        return False


def delete_row_by_title(
    page: Page,
    title: str,
    *,
    sources_frame: Frame,
    catalog_index: int | None = None,
    catalog_total: int = 0,
    delete_mode: str = "listitem",
) -> bool:
    """Remove → confirm. Returns True if a delete sequence completed."""
    per_row_deadline = 45.0 if delete_mode == "panel_text" else 120.0
    max_attempts = 35 if delete_mode == "panel_text" else 80
    deadline = time.time() + per_row_deadline
    stale_bottom = 0
    prev_scroll_top: int | None = None

    if catalog_index is not None and catalog_total > 1:
        frac = max(0.0, min(1.0, catalog_index / max(catalog_total - 1, 1)))
        _scroll_sources_shell(sources_frame, scroll_fraction=frac)
        page.wait_for_timeout(250)
    else:
        _scroll_sources_shell(sources_frame, to_top=True)
        page.wait_for_timeout(200)

    for attempt in range(max_attempts):
        _recover_sources_ui(page)
        if time.time() > deadline:
            print(f"    [timeout] delete gave up after {int(per_row_deadline)}s ({title!r})", flush=True)
            return False
        if attempt % 10 == 0:
            print(f"    attempt {attempt + 1}/{max_attempts}…", flush=True)

        if delete_mode == "panel_text":
            if _delete_via_detail_pane(page, sources_frame, title):
                return True
        else:
            # Hover first; some UIs only show ⋮ on hover.
            _hover_row_by_text(sources_frame, title)
            if _find_and_tag_overflow(sources_frame, title):
                try:
                    _click_tagged_overflow(page, sources_frame)
                except Exception:
                    _recover_sources_ui(page)
                    scroll_panel(page, 220, sources_frame=sources_frame)
                    continue
                page.wait_for_timeout(500)
                try:
                    has_menu = page.locator(
                        ".cdk-overlay-container [role='menuitem'], .cdk-overlay-container .mat-mdc-menu-item"
                    ).count() > 0
                except Exception:
                    has_menu = False
                if not has_menu:
                    _recover_sources_ui(page)
                    scroll_panel(page, 220, sources_frame=sources_frame)
                    continue
                try:
                    page.locator(".cdk-overlay-container").get_by_role(
                        "menuitem", name=re.compile(r"Remove|Ta bort", re.I)
                    ).first.click(timeout=6_000)
                except Exception:
                    try:
                        items = page.locator(".cdk-overlay-container .mat-mdc-menu-item")
                        n = min(items.count(), 40)
                        clicked = False
                        for i in range(n):
                            txt = (items.nth(i).inner_text() or "").lower()
                            if any(x in txt for x in ("remove", "ta bort", "delete")):
                                items.nth(i).click(timeout=3_000)
                                clicked = True
                                break
                        if not clicked:
                            _recover_sources_ui(page)
                            scroll_panel(page, 220, sources_frame=sources_frame)
                            continue
                    except Exception:
                        _recover_sources_ui(page)
                        scroll_panel(page, 220, sources_frame=sources_frame)
                        continue
                page.wait_for_timeout(500)
                if _confirm_delete_dialog(page):
                    page.wait_for_timeout(2500)
                    _recover_sources_ui(page)
                    return True
                _recover_sources_ui(page)
                continue

        if delete_mode == "panel_text":
            scroll_state = _scroll_sources_shell(sources_frame, dy=260)
        else:
            scroll_state = _scroll_sources_shell(sources_frame, dy=260)
        scroll_top = int((scroll_state or {}).get("scrollTop") or 0)
        if prev_scroll_top is not None and scroll_top == prev_scroll_top:
            stale_bottom += 1
            if stale_bottom >= 2:
                _scroll_sources_shell(sources_frame, to_top=True)
                stale_bottom = 0
            else:
                break
        else:
            stale_bottom = 0
        prev_scroll_top = scroll_top
    print(f"    [fail] could not delete {title!r} (mode={delete_mode})", flush=True)
    return False


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def run_dedupe(page: Page) -> None:
    print("--- Catalog: reading every Sources row ---")
    sources_frame = find_best_sources_frame(page)
    catalog = collect_row_catalog(page, sources_frame)
    save_catalog(catalog)
    counts = Counter(r["title"] for r in catalog)
    dups = {t: n for t, n in counts.items() if n > 1}
    if not dups:
        print("No duplicate titles in catalog.")
        return
    print(f"--- Removing extras ({len(dups)} title(s) with duplicates) ---")
    delete_mode = _probe_delete_dom(sources_frame)
    removed = 0
    for title, n in sorted(dups.items(), key=lambda x: (-x[1], x[0].lower())):
        extra = min(n - 1, MAX_PER_TITLE)
        for k in range(extra):
            print(f"  [{title!r}] remove extra {k + 1}/{extra}")
            if delete_row_by_title(page, title, sources_frame=sources_frame, delete_mode=delete_mode):
                removed += 1
            else:
                print(f"  [SKIP] {title!r}")
                break
    print(f"--- Done. Removed {removed} row(s). ---")


def run_plan(page: Page, *, max_per_name: int) -> int:
    names = names_from_review()
    if not names:
        print("Nothing to delete from review file.")
        return 0
    print("--- Scanning Sources panel (live titles) ---", flush=True)
    print("    (large notebooks: this can take 1–3 minutes)", flush=True)
    sources_frame = find_best_sources_frame(page)
    catalog = collect_row_catalog(page, sources_frame)
    save_catalog(catalog)
    if not catalog:
        print("[FAIL] No rows read from the panel. Open Sources, correct notebook, retry.")
        print(f"        Inspect {DOM_DEBUG_PATH.name} in this folder for frame URLs and panel text.")
        return 1
    panel_titles = list(dict.fromkeys(r["title"] for r in catalog))
    title_index = {r["title"]: i for i, r in enumerate(catalog)}
    mode = f"up to {max_per_name} row(s) per name" if max_per_name > 1 else "one row per name"
    print(f"--- Plan delete: {len(names)} name(s) ({len(panel_titles)} distinct title(s) on screen) [{mode}] ---")

    delete_mode = _probe_delete_dom(sources_frame)
    print(f"[INFO] NotebookLM delete UI mode: {delete_mode}", flush=True)
    if delete_mode == "panel_text":
        print(
            "[WARN] Your notebook Sources panel is text-only (no list rows with ⋮ menus).\n"
            "       Trying click-title → Remove source. This is slow/unreliable on large notebooks.\n"
            "       Recommended instead:\n"
            "         python compare_sources.py --apply --skip-deletes\n"
            "       Or clear sources manually in NotebookLM / use a fresh notebook.",
            flush=True,
        )

    def _delete_sort_key(plan_name: str) -> int:
        resolved = best_panel_title(plan_name, panel_titles) or plan_name
        return title_index.get(resolved, 0)

    # Delete from bottom of the list first — less scrolling in a virtualized panel.
    names = sorted(names, key=_delete_sort_key, reverse=True)

    total = 0
    failed: list[str] = []
    skipped_not_visible: list[str] = []
    for name in names:
        resolved = best_panel_title(name, panel_titles)
        if not resolved:
            print(f"  [SKIP] Not visible in current Sources panel: {name!r}", flush=True)
            skipped_not_visible.append(name)
            failed.append(name)
            continue
        target = resolved
        if resolved and resolved != name:
            print(f"  [MATCH] {name!r} -> panel {target!r}", flush=True)
        print(f"  Deleting {target!r}…", flush=True)
        n = 0
        while n < max_per_name:
            if delete_row_by_title(
                page,
                target,
                sources_frame=sources_frame,
                catalog_index=title_index.get(target),
                catalog_total=len(catalog),
                delete_mode=delete_mode,
            ):
                total += 1
                n += 1
                if target in title_index:
                    del title_index[target]
                continue
            break
        if n == 0:
            failed.append(name)
        else:
            print(f"  [OK] {name} ({n} row(s))")
    if failed:
        print(f"[WARN] Not found: {len(failed)} name(s)")
        for x in failed:
            print(f"       - {x}")
        if skipped_not_visible:
            print(
                f"[INFO] {len(skipped_not_visible)} name(s) were skipped immediately because they were not "
                f"in the current panel catalog (no 10-attempt retry loop)."
            )
        if total == 0:
            print("[FAIL] No rows removed.")
            print(
                "[HINT] Automated delete is unreliable on current NotebookLM. Use:\n"
                "         python compare_sources.py --apply --skip-deletes"
            )
            return 1
    return 0


def main() -> None:
    dedupe = "--dedupe" in sys.argv
    all_dupes = "--all-dupes" in sys.argv
    max_per = MAX_PER_TITLE if all_dupes else PLAN_DELETES_PER_NAME_DEFAULT
    with sync_playwright() as p:
        try:
            page = connect_notebook_page(p)
            ready_notebook(page)
        except Exception as e:
            print(f"[FAIL] {e}")
            sys.exit(1)
        if dedupe:
            run_dedupe(page)
        else:
            sys.exit(run_plan(page, max_per_name=max_per))


if __name__ == "__main__":
    main()
