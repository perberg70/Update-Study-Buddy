import json
import os
import re

from playwright.sync_api import sync_playwright

from config import (
    CDP_URL,
    ENFORCE_UPLOAD_SIZE_LIMIT,
    MANIFEST_PATH,
    MAX_UPLOAD_SIZE_MB,
    PROJECT_URL,
    REVIEW_PATH,
    normalize_action,
)


def get_upload_plan():
    """Return files marked for upload from comparison_review.json.

    Uploadable actions:
    - pairs: REPLACE
    - new_only: ADD

    Returns None when no review file is found (caller falls back to full manifest).
    """
    if not os.path.exists(REVIEW_PATH):
        return None

    with open(REVIEW_PATH, "r", encoding="utf-8") as f:
        review = json.load(f)

    files = []
    seen = set()

    for pair in review.get("pairs", []):
        if normalize_action(pair.get("action", "")) == "REPLACE":
            key = (pair["new_name"], pair.get("new_path", ""))
            if key not in seen:
                seen.add(key)
                files.append(
                    {
                        "name": pair["new_name"],
                        "path": pair["new_path"],
                        "type": pair["new_type"],
                        "chapter": pair.get("chapter", ""),
                    }
                )

    for item in review.get("new_only", []):
        if normalize_action(item.get("action", "")) == "ADD":
            key = (item["name"], item.get("path", ""))
            if key not in seen:
                seen.add(key)
                files.append(
                    {
                        "name": item["name"],
                        "path": item["path"],
                        "type": item["type"],
                        "chapter": item.get("chapter", ""),
                    }
                )

    return files


def run_upload():
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
            for item in ch.get("files", []):
                upload_items.append(
                    {
                        "name": item["name"],
                        "path": item.get("path", ""),
                        "type": item.get("type", ""),
                        "chapter": ch["chapter"],
                    }
                )
        print(f"--- Starting NoteBookLM upload (all {len(upload_items)} file(s) from manifest) ---")

    if not upload_items:
        print("--- Nothing to upload. ---")
        return

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
            return

        page.goto(PROJECT_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("load")

        add_sources_btn = page.get_by_role(
            "button", name=re.compile(r"(\+\s*)?Add\s+source|Lägg\s+till\s+källa", re.I)
        )
        try:
            add_sources_btn.first.wait_for(state="visible", timeout=30_000)
        except Exception as e:
            print(f"[FAIL] '+ Add sources' button did not appear: {e}")
            return

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
                add_sources_btn = page.get_by_role(
                    "button", name=re.compile(r"(\+\s*)?Add\s+source|Lägg\s+till\s+källa", re.I)
                ).first
                add_sources_btn.wait_for(state="visible", timeout=25_000)
                add_sources_btn.click(timeout=15_000)
                page.wait_for_timeout(800)

                upload_file_exts = {".txt", ".pdf", ".md", ".docx", ".xlsx", ".mp3", ".wav", ".m4a"}
                is_file_path = os.path.exists(file_path) and os.path.splitext(file_name)[1].lower() in upload_file_exts
                is_upload_file = file_type in ("text", "audio") or is_file_path

                if is_upload_file:
                    if not os.path.exists(file_path):
                        print(f"   [FAIL] File not found: {file_path}")
                        continue
                    size_mb = os.path.getsize(file_path) / (1024 * 1024)
                    if size_mb > MAX_UPLOAD_SIZE_MB:
                        if ENFORCE_UPLOAD_SIZE_LIMIT:
                            print(
                                f"   [SKIP] {file_name} ({size_mb:.1f} MB) exceeds configured limit "
                                f"{MAX_UPLOAD_SIZE_MB} MB (ENFORCE_UPLOAD_SIZE_LIMIT=true)."
                            )
                            page.keyboard.press("Escape")
                            page.wait_for_timeout(300)
                            page.keyboard.press("Escape")
                            continue
                        print(
                            f"   [WARN] {file_name} ({size_mb:.1f} MB) exceeds {MAX_UPLOAD_SIZE_MB} MB; "
                            "attempting upload anyway."
                        )

                    with page.expect_file_chooser() as fc_info:
                        page.get_by_role(
                            "button", name=re.compile(r"Upload\s+files|Ladda\s+upp\s+filer", re.I)
                        ).first.click(timeout=10_000)
                    file_chooser = fc_info.value
                    file_chooser.set_files(file_path)
                else:
                    raw_path = file_info["path"]
                    if raw_path.startswith(("http://", "https://")):
                        content = raw_path
                        page.get_by_role("button", name=re.compile(r"Websites|Webbplatser", re.I)).first.click(
                            timeout=10_000
                        )
                    else:
                        with open(file_path, "r", encoding="utf-8") as tf:
                            content = tf.read()
                        page.get_by_role(
                            "button", name=re.compile(r"Copied\s+text|Kopierad\s+text", re.I)
                        ).first.click(timeout=10_000)
                    page.locator("textarea[placeholder*='Klistra in'], textarea[placeholder*='Paste'], textarea").first.fill(
                        content
                    )
                    page.locator("input[placeholder*='Namn'], input[placeholder*='Title']").first.fill(
                        file_name.replace(".txt", "")
                    )
                    page.get_by_role("button", name=re.compile(r"Infoga|Insert|Spara|Save", re.I)).first.click(
                        timeout=10_000
                    )

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
