"""
Remove sources from NotebookLM based on comparison_review.json.
Deletes ALL copies of each source name (handles duplicates).
"""
import json
import os
import re
import sys
from playwright.sync_api import sync_playwright

REVIEW_PATH = "comparison_review.json"
PROJECT_URL = "https://notebooklm.google.com/notebook/82c34a38-cbc5-47fe-8001-36696f67d7fb"


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
        action = pair.get("action", "").upper()
        old = pair.get("old_name")
        if old and action in ("REPLACE", "DELETE") and old not in seen:
            seen.add(old)
            names.append(old)

    for item in review.get("current_only", []):
        action = item.get("action", "").upper()
        name = item.get("name")
        if name and action == "DELETE" and name not in seen:
            seen.add(name)
            names.append(name)

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
    """Use JavaScript to find the More button by aria-description.
    Avoids CSS selector escaping issues with special characters."""
    found = page.evaluate("""(name) => {
        document.querySelectorAll('[data-delete-target]').forEach(
            el => el.removeAttribute('data-delete-target')
        );
        const btns = document.querySelectorAll('button[aria-description]');
        for (const btn of btns) {
            const desc = btn.getAttribute('aria-description') || '';
            if (desc === name || desc.includes(name)) {
                btn.setAttribute('data-delete-target', 'true');
                btn.scrollIntoView({block: 'center', behavior: 'instant'});
                return true;
            }
        }
        return false;
    }""", source_name)

    if found:
        return page.locator('button[data-delete-target="true"]').first
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
    more_btn = find_more_button_js(page, source_name)

    if not more_btn:
        # Fallback: locate by visible text → ancestor row → button
        try:
            name_el = page.get_by_text(source_name, exact=False).first
            name_el.wait_for(state="visible", timeout=4_000)
            row = name_el.locator("xpath=ancestor::*[.//button][1]")
            row.scroll_into_view_if_needed()
            row.hover()
            page.wait_for_timeout(400)
            more_btn = row.get_by_role(
                "button", name=re.compile(r"More|Mer|alternativ|options", re.I)
            ).or_(row.locator("button").last).first
        except Exception:
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


def run_delete():
    to_remove = get_sources_to_remove()
    if not to_remove:
        print("--- No sources to remove. ---")
        return

    print(f"--- Removing {len(to_remove)} unique source name(s) from NotebookLM ---")
    for name in to_remove:
        print(f"   [QUEUED] {name}")

    with sync_playwright() as p:
        try:
            print("--- Attempting to connect via CDP (Port 9222) ---")
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
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
            "button", name=re.compile(r"(\+\s*)?Add\s+source|Lägg\s+till\s+källa", re.I)
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
    run_delete()
