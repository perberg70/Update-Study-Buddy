"""
Remove sources from NotebookLM based on comparison_review.json.
Deletes ALL copies of each source name (handles duplicates).
"""
import json
import os
import re
import sys
from config import CDP_URL, PROJECT_URL, REVIEW_PATH
import config as app_config

def _get_name(item: dict) -> str:
    """Best-effort source name extraction from review rows."""
    return (
        item.get("name")
        or item.get("old_name")
        or item.get("source_name")
        or item.get("title")
        or ""
    )


def get_sources_to_remove():
    """Collect source names to delete from comparison_review.json (deduplicated)."""
    if not os.path.exists(REVIEW_PATH):
        print(f"[WARN] {REVIEW_PATH} not found. Nothing to delete.")
        return []
    with open(REVIEW_PATH, "r", encoding="utf-8") as f:
        review = json.load(f)

    names = []
    seen = set()

    for pair in review.get("pairs", []):
        action = app_config.normalize_action(pair.get("action", ""))
        old = _get_name(pair)
        if old and action in ("REPLACE", "DELETE") and old not in seen:
            seen.add(old)
            names.append(old)

    delete_pairs = 0
    delete_current_only = 0

    for item in review.get("current_only", []):
        action = app_config.normalize_action(item.get("action", ""))
        name = _get_name(item)
        if name and action == "DELETE" and name not in seen:
            seen.add(name)
            names.append(name)
            delete_current_only += 1

    for pair in review.get("pairs", []):
        action = app_config.normalize_action(pair.get("action", ""))
        if action in ("REPLACE", "DELETE"):
            delete_pairs += 1

    print(
        f"[PLAN] Review delete actions: pairs={delete_pairs}, current_only={delete_current_only}, unique_sources={len(names)}"
    )
    return names


def dismiss_overlays(page):
    """Aggressively dismiss any open dialogs, menus, or overlay backdrops."""
    for _ in range(3):
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    try:
        backdrop = page.locator(".cdk-overlay-backdrop")
        if backdrop.first.is_visible(timeout=300):
            backdrop.first.click(force=True)
            page.wait_for_timeout(300)
    except Exception:
        pass
    page.wait_for_timeout(200)


def find_more_button_js(page, source_name):
    """Find source row 'More' button using robust JS matching.

    Handles truncation/annotation differences by normalizing and token matching.
    """
    found = page.evaluate("""(name) => {
        const norm = (s) => (s || '').toLowerCase().replace(/[^a-z0-9\u00e5\u00e4\u00f6]+/g, ' ').replace(/\s+/g, ' ').trim();
        const hasTokenOverlap = (a, b) => {
            const ta = new Set(norm(a).split(' ').filter(x => x.length > 2));
            const tb = new Set(norm(b).split(' ').filter(x => x.length > 2));
            if (!ta.size || !tb.size) return false;
            let overlap = 0;
            for (const t of ta) if (tb.has(t)) overlap += 1;
            return overlap >= Math.min(2, Math.max(1, Math.floor(tb.size / 2)));
        };

        const target = norm(name);
        document.querySelectorAll('[data-delete-target]').forEach(
            el => el.removeAttribute('data-delete-target')
        );

        // Strategy 1: aria-description on the 3-dots button
        const btns = document.querySelectorAll('button[aria-description]');
        for (const btn of btns) {
            const desc = btn.getAttribute('aria-description') || '';
            const nd = norm(desc);
            if (nd === target || nd.includes(target) || target.includes(nd) || hasTokenOverlap(nd, target)) {
                btn.setAttribute('data-delete-target', 'true');
                btn.scrollIntoView({block: 'center', behavior: 'instant'});
                return true;
            }
        }

        // Strategy 2: row text + local button fallback
        const rows = document.querySelectorAll('mat-list-item, [role="listitem"], .source-item, .mat-mdc-list-item, li, div');
        for (const row of rows) {
            const text = norm(row.innerText || row.textContent || '');
            if (!text) continue;
            if (text.includes(target) || target.includes(text) || hasTokenOverlap(text, target)) {
                const rowBtn = row.querySelector('button[aria-description], button[aria-label*="More" i], button[aria-label*="Mer" i], button');
                if (rowBtn) {
                    rowBtn.setAttribute('data-delete-target', 'true');
                    rowBtn.scrollIntoView({block: 'center', behavior: 'instant'});
                    return true;
                }
            }
        }

        return false;
    }""", source_name)

    if found:
        return page.locator('button[data-delete-target="true"]').first
    return None


def find_sources_panel(page):
    """Best-effort locator for the sources sidebar/panel used for scrolling."""
    try:
        add_btn = page.get_by_role(
            "button", name=re.compile(r"(\+\s*)?Add\s+source|L\u00e4gg\s+till\s+k\u00e4lla", re.I)
        ).first
        panel = page.locator("section, [role='region'], aside, nav, [class*='sidebar'], [class*='panel']").filter(has=add_btn).first
        panel.wait_for(state="visible", timeout=2_000)
        return panel
    except Exception:
        return None


def find_more_button_with_scroll(page, source_name, attempts=10):
    """Try to locate a source's more button while scrolling a virtualized list."""
    panel = find_sources_panel(page)

    # First try without scrolling
    btn = find_more_button_js(page, source_name)
    if btn:
        return btn

    if not panel:
        return None

    # Sweep down
    for _ in range(attempts):
        btn = find_more_button_js(page, source_name)
        if btn:
            return btn
        try:
            panel.evaluate("el => el.scrollBy(0, 450)")
        except Exception:
            break
        page.wait_for_timeout(250)

    # Sweep up and retry
    try:
        panel.evaluate("el => { el.scrollTop = 0; }")
        page.wait_for_timeout(250)
    except Exception:
        pass

    for _ in range(max(3, attempts // 2)):
        btn = find_more_button_js(page, source_name)
        if btn:
            return btn
        try:
            panel.evaluate("el => el.scrollBy(0, 350)")
        except Exception:
            break
        page.wait_for_timeout(250)

    return None


def click_confirm_delete(page):
    """Find and click the confirm button in the delete dialog.
    Tries multiple strategies to handle English/Swedish variants."""
    page.wait_for_timeout(600)

    # Strategy 1: role-based with broad regex
    try:
        btn = page.get_by_role(
            "button", name=re.compile(r"Delete|Remove|Ta bort|Radera", re.I)
        ).first
        btn.wait_for(state="visible", timeout=3_000)
        btn.click(timeout=3_000)
        return True
    except Exception:
        pass

    # Strategy 2: find buttons inside the dialog overlay
    try:
        dialog_btns = page.locator(
            ".cdk-overlay-container mat-dialog-actions button, "
            ".cdk-overlay-container [mat-dialog-actions] button, "
            ".cdk-overlay-container .mat-mdc-dialog-actions button"
        )
        count = dialog_btns.count()
        if count > 0:
            dialog_btns.last.click(timeout=3_000)
            return True
    except Exception:
        pass

    # Strategy 3: any button with delete-ish text anywhere in the overlay
    try:
        overlay_btn = page.locator(".cdk-overlay-container").get_by_role(
            "button", name=re.compile(r"Delete|Remove|Ta bort|Radera|OK|Confirm", re.I)
        ).first
        overlay_btn.click(timeout=3_000)
        return True
    except Exception:
        pass

    return False


def delete_one_source(page, source_name):
    """Delete a single copy of *source_name* from the notebook. Returns True on success."""
    dismiss_overlays(page)

    # Find the More (three-dots) button for this source
    more_btn = find_more_button_with_scroll(page, source_name)

    if not more_btn:
        # Fallback: locate by visible text/token(s) -> ancestor row -> local button
        queries = [source_name]
        parts = [p for p in re.split(r"[^A-Za-z0-9\u00c5\u00c4\u00d6\u00e5\u00e4\u00f6]+", source_name) if len(p) >= 4]
        if parts:
            queries.append(" ".join(parts[:3]))
            queries.extend(parts[:2])

        for q in queries:
            try:
                name_el = page.get_by_text(q, exact=False).first
                name_el.wait_for(state="visible", timeout=2_000)
                row = name_el.locator("xpath=ancestor::*[.//button][1]")
                row.scroll_into_view_if_needed()
                row.hover()
                page.wait_for_timeout(250)
                more_btn = row.get_by_role(
                    "button", name=re.compile(r"More|Mer|alternativ|options", re.I)
                ).or_(row.locator("button").last).first
                break
            except Exception:
                more_btn = None

        if not more_btn:
            return False

    try:
        more_btn.wait_for(state="visible", timeout=3_000)
    except Exception:
        return False

    more_btn.click(timeout=5_000)
    page.wait_for_timeout(600)

    # Click "Remove source" in the context menu
    try:
        remove_item = page.get_by_role(
            "menuitem", name=re.compile(r"Remove|Ta bort", re.I)
        ).first
        remove_item.wait_for(state="visible", timeout=3_000)
        remove_item.click(timeout=5_000)
    except Exception:
        # Fallback: any menu item in the overlay
        try:
            menu_items = page.locator(".cdk-overlay-container .mat-mdc-menu-item, .cdk-overlay-container [mat-menu-item]")
            for i in range(menu_items.count()):
                text = (menu_items.nth(i).inner_text() or "").lower()
                if "remove" in text or "ta bort" in text or "delete" in text:
                    menu_items.nth(i).click(timeout=3_000)
                    break
        except Exception:
            dismiss_overlays(page)
            return False

    page.wait_for_timeout(600)

    # Confirm the delete in the dialog
    if not click_confirm_delete(page):
        dismiss_overlays(page)
        return False

    page.wait_for_timeout(2_500)
    return True


def run_delete(dry_run: bool = False):
    to_remove = get_sources_to_remove()
    if not to_remove:
        print("--- No sources to remove. ---")
        return

    print(f"--- Removing {len(to_remove)} unique source name(s) from NotebookLM ---")

    if dry_run:
        print("[DRY-RUN] Parsed delete plan only. No browser actions executed.")
        for name in to_remove:
            print(f"   [PLAN] {name}")
        return

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"[FAIL] Playwright is required for deletion automation: {exc}")
        return
    for name in to_remove:
        print(f"   [QUEUED] {name}")

    with sync_playwright() as p:
        try:
            print("--- Attempting to connect via CDP (Port 9222) ---")
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.pages[0]
            print("[OK] Connected to existing browser via CDP.")
        except Exception as e:
            print(f"[FAIL] CDP connection failed: {e}")
            print("Start Chrome with: chrome.exe --remote-debugging-port=9222")
            sys.exit(1)

        page.goto(PROJECT_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("load")

        page.get_by_role(
            "button", name=re.compile(r"(\+\s*)?Add\s+source|L\u00e4gg\s+till\s+k\u00e4lla", re.I)
        ).first.wait_for(state="visible", timeout=30_000)

        total_removed = 0
        for source_name in to_remove:
            copies = 0
            while copies < 10:
                try:
                    if delete_one_source(page, source_name):
                        copies += 1
                    else:
                        break
                except Exception:
                    dismiss_overlays(page)
                    break
            total_removed += copies
            if copies > 0:
                extra = f" ({copies} copies)" if copies > 1 else ""
                print(f"   [OK] Removed: {source_name}{extra}")
            else:
                print(f"   [SKIP] Not found in notebook: {source_name}")

        print(f"\n--- Removed {total_removed} source(s) total. ---")


if __name__ == "__main__":
    run_delete(dry_run="--dry-run" in sys.argv)
