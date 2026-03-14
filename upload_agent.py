import os
import json
import re
import time
from playwright.sync_api import sync_playwright

from config import CDP_URL, MANIFEST_PATH, MAX_UPLOAD_SIZE_MB, PROJECT_URL, REVIEW_PATH


def get_upload_plan():
    """If comparison_review.json exists, return only files marked for upload (REPLACE / ADD).
    Returns None when no review file is found (caller falls back to full manifest)."""
    if not os.path.exists(REVIEW_PATH):
        return None
    with open(REVIEW_PATH, "r", encoding="utf-8") as f:
        review = json.load(f)

    files = []
    seen = set()

    for pair in review.get("pairs", []):
        if pair.get("action", "").upper() == "REPLACE":
            key = (pair["new_name"], pair.get("new_path", ""))
            if key not in seen:
                seen.add(key)
                files.append({
                    "name": pair["new_name"],
                    "path": pair["new_path"],
                    "type": pair["new_type"],
                    "chapter": pair.get("chapter", ""),
                })

    for item in review.get("new_only", []):
        if item.get("action", "").upper() == "ADD":
            key = (item["name"], item.get("path", ""))
            if key not in seen:
                seen.add(key)
                files.append({
                    "name": item["name"],
                    "path": item["path"],
                    "type": item["type"],
                    "chapter": item.get("chapter", ""),
                })

    return files


def run_upload():
    # Build upload list --------------------------------------------------------
    upload_plan = get_upload_plan()

    if upload_plan is not None:
        upload_items = upload_plan
        print(f"--- Starting NoteBookLM upload ({len(upload_items)} file(s) from comparison review) ---")
    else:
        if not os.path.exists(MANIFEST_PATH):
            print(f"Error: {MANIFEST_PATH} not found. Run organize_content.py first.")
            return
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        upload_items = []
        for ch in manifest:
            for f in ch.get("files", []):
                upload_items.append({
                    "name": f["name"],
                    "path": f.get("path", ""),
                    "type": f.get("type", ""),
                    "chapter": ch["chapter"],
                })
        print(f"--- Starting NoteBookLM upload (all {len(upload_items)} file(s) from manifest) ---")

    if not upload_items:
        print("--- Nothing to upload. ---")
        return

    # Connect to browser -------------------------------------------------------
    with sync_playwright() as p:
        browser = None
        context = None
        page = None

        try:
            print("--- Attempting to connect via CDP (Port 9222) ---")
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.pages[0]
            print("[OK] Connected to existing browser via CDP.")
        except Exception as e:
            print(f"[WAIT] CDP connection failed: {e}")
            print()
            print("--- To use your preferred Google account (recommended) ---")
            print("  1. Close ALL Chrome windows.")
            print("  2. Start Chrome with remote debugging:")
            print('     chrome.exe --remote-debugging-port=9222')
            print("  3. In that Chrome, log in and open NotebookLM.")
            print("  4. Run this script again.")
            print()
            print("--- Attempting to launch a local browser ---")
            user_data = os.path.join(os.environ["LOCALAPPDATA"], "Google", "Chrome", "User Data")
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=user_data,
                    headless=False,
                    channel="chrome",
                    args=["--no-sandbox"]
                )
                page = context.pages[0]
                print("[OK] Launched Chrome with your default profile.")
            except Exception:
                profile_dir = os.path.join(user_data, "PlaywrightProfile")
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=profile_dir,
                        headless=False,
                        channel="chrome",
                        args=["--no-sandbox"]
                    )
                    page = context.pages[0]
                    print("[OK] Launched Chrome (separate profile).")
                except Exception as e2:
                    print(f"[FAIL] Could not launch local browser: {e2}")
                    return

        page.goto(PROJECT_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("load")

        title = page.title()
        print(f"Page title: {title}")

        on_login = "logga in" in title.lower() or "sign in" in title.lower() or "accounts.google.com" in page.url
        if on_login:
            print("[WAIT] You appear to be on the login page. Log in to Google in the browser window.")
            print("[WAIT] Waiting up to 2 minutes for the notebook page to load...")

        add_sources_btn = page.get_by_role("button", name=re.compile(r"(\+\s*)?Add\s+source|Lägg\s+till\s+källa", re.I))
        try:
            add_sources_btn.first.wait_for(state="visible", timeout=120_000)
            print("[OK] NotebookLM notebook is ready (+ Add sources visible).")
        except Exception as e:
            print(f"[FAIL] '+ Add sources' button did not appear: {e}")
            print("       Make sure you are logged in and the notebook URL is correct. Exiting.")
            return

        # Upload loop ----------------------------------------------------------
        current_chapter = None
        for file_info in upload_items:
            ch = file_info.get("chapter", "")
            if ch and ch != current_chapter:
                current_chapter = ch
                print(f"\n[FOLDER] Chapter: {current_chapter}")

            file_name = file_info["name"]
            file_path = os.path.abspath(file_info["path"])
            file_type = file_info["type"]

            print(f"   [WAIT] Uploading {file_name} ({file_type})...")

            try:
                add_sources_btn = page.get_by_role("button", name=re.compile(r"(\+\s*)?Add\s+source|Lägg\s+till\s+källa", re.I)).first
                add_sources_btn.wait_for(state="visible", timeout=25_000)
                add_sources_btn.click(timeout=15_000)
                page.wait_for_timeout(800)

                UPLOAD_FILE_EXTS = {".txt", ".pdf", ".md", ".docx", ".xlsx", ".mp3", ".wav", ".m4a"}
                is_file_path = os.path.exists(file_path) and os.path.splitext(file_name)[1].lower() in UPLOAD_FILE_EXTS
                is_upload_file = file_type in ("text", "audio") or is_file_path

                if is_upload_file:
                    if not os.path.exists(file_path):
                        print(f"   [FAIL] File not found: {file_path}")
                        continue
                    size_mb = os.path.getsize(file_path) / (1024 * 1024)
                    if size_mb > MAX_UPLOAD_SIZE_MB:
                        print(f"   [SKIP] {file_name} ({size_mb:.1f} MB) exceeds {MAX_UPLOAD_SIZE_MB} MB CDP limit. Upload it manually.")
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                        page.keyboard.press("Escape")
                        continue
                    with page.expect_file_chooser() as fc_info:
                        page.get_by_role("button", name=re.compile(r"Upload\s+files|Ladda\s+upp\s+filer", re.I)).first.click(timeout=10_000)
                    file_chooser = fc_info.value
                    file_chooser.set_files(file_path)
                else:
                    raw_path = file_info["path"]
                    if raw_path.startswith(("http://", "https://")):
                        content = raw_path
                        page.get_by_role("button", name=re.compile(r"Websites|Webbplatser", re.I)).first.click(timeout=10_000)
                    else:
                        with open(file_path, "r", encoding="utf-8") as tf:
                            content = tf.read()
                        page.get_by_role("button", name=re.compile(r"Copied\s+text|Kopierad\s+text", re.I)).first.click(timeout=10_000)
                    page.locator("textarea[placeholder*='Klistra in'], textarea[placeholder*='Paste'], textarea").first.fill(content)
                    page.locator("input[placeholder*='Namn'], input[placeholder*='Title']").first.fill(file_name.replace(".txt", ""))
                    page.get_by_role("button", name=re.compile(r"Infoga|Insert|Spara|Save", re.I)).first.click(timeout=10_000)

                page.wait_for_timeout(3000)
                print(f"   [OK] {file_name} uploaded.")

            except Exception as e:
                print(f"   [FAIL] Failed to upload {file_name}: {e}")
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(200)
                except Exception:
                    pass

    print("\n--- Autonomous upload process finished. ---")


if __name__ == "__main__":
    run_upload()
