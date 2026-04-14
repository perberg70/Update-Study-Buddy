"""
Upload sources to NotebookLM from ``comparison_review.json`` or ``processing_manifest.json``.

``organize_content.py`` registers **one** merged ``NN_ChapterName.txt`` per chapter (all HTML merged
into that file). That is **one** file-upload source per chapter for body text. Rows under
``url_sources/`` are **separate** link sources (Websites flow), not fragments of the chapter merge.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import CDP_URL, PROJECT_URL
from notebook_ready import ADD_SOURCE_BTN, pick_notebook_page, resolve_sidebar_frame
from notebooklm_sources import manifest_path_is_per_url_split

SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = SCRIPT_DIR / "processing_manifest.json"
REVIEW_PATH = SCRIPT_DIR / "comparison_review.json"
MAX_UPLOAD_SIZE_MB = 50

# Add-sources sheet (may live in main frame or a child frame; not always same as sidebar frame)
UPLOAD_FILES_BTN = re.compile(
    r"Upload\s+files|Upload\s+file|Ladda\s+upp\s+filer|Ladda\s+upp\s+fil\b",
    re.I,
)
# Add sources → "Websites" / link (URLs and YouTube watch links belong here, not file upload)
WEBSITES_LINK_BTN = re.compile(r"Websites|Webbplatser", re.I)


def _upload_files_button_visible_in_frame(frame) -> bool:
    try:
        loc = frame.get_by_role("button", name=UPLOAD_FILES_BTN).first
        return bool(loc.is_visible(timeout=500))
    except Exception:
        return False


def _add_sources_sheet_open(page) -> bool:
    for fr in page.frames:
        if _upload_files_button_visible_in_frame(fr):
            return True
    return False


def _effective_upload_ext(file_name: str, file_path: str) -> str:
    """Extension from on-disk path first.

    ``processing_manifest.json`` may set ``name`` to the raw ``https://...`` string for ``type: url``
    rows; ``splitext(name)`` is then empty and the old logic treated the row as non-file and pasted
    the .txt body as *Copied text*. Using the real file path fixes .mp3 / .txt / .pdf classification.
    """
    if file_path and os.path.exists(file_path):
        e = os.path.splitext(file_path)[1].lower()
        if e:
            return e
    return os.path.splitext(file_name or "")[1].lower()


def _source_url_from_extracted_txt(file_path: str) -> str:
    """Read 'Source URL: https://...' from a split_urls .txt body."""
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s.lower().startswith("source url:"):
                    url = s.split(":", 1)[1].strip()
                    if url.startswith(("http://", "https://")):
                        return url
    except OSError:
        pass
    return ""


def _click_button_name_any_frame(page, pattern: re.Pattern[str], *, timeout_ms: int = 15_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_exc = None
    while time.monotonic() < deadline:
        for fr in page.frames:
            try:
                fr.get_by_role("button", name=pattern).first.click(timeout=3000)
                return
            except Exception as e:
                last_exc = e
        page.wait_for_timeout(200)
    if last_exc:
        raise last_exc
    raise TimeoutError("Button not found in any frame")


def _fill_website_or_paste_dialog(page, content: str, display_title: str, *, use_websites: bool) -> None:
    """Add-sources sheet: Websites / paste URL vs Copied text."""
    if use_websites:
        _click_button_name_any_frame(page, WEBSITES_LINK_BTN)
    else:
        _click_button_name_any_frame(page, re.compile(r"Copied\s+text|Kopierad\s+text", re.I))

    deadline = time.monotonic() + 20.0
    last_exc = None
    while time.monotonic() < deadline:
        for fr in page.frames:
            try:
                ta = fr.locator(
                    "textarea[placeholder*='Klistra in'], textarea[placeholder*='Paste'], textarea"
                ).first
                if ta.is_visible(timeout=600):
                    ta.fill(content)
                    inp = fr.locator("input[placeholder*='Namn'], input[placeholder*='Title']").first
                    if inp.is_visible(timeout=1500):
                        inp.fill(display_title)
                    fr.get_by_role("button", name=re.compile(r"Infoga|Insert|Spara|Save", re.I)).first.click(
                        timeout=10_000
                    )
                    return
            except Exception as e:
                last_exc = e
        page.wait_for_timeout(200)
    if last_exc:
        raise last_exc
    raise TimeoutError("Could not fill add-source dialog (textarea/title/confirm)")


def _click_upload_files_any_frame(page, timeout_ms: int = 15_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_exc = None
    while time.monotonic() < deadline:
        for fr in page.frames:
            try:
                loc = fr.get_by_role("button", name=UPLOAD_FILES_BTN).first
                loc.click(timeout=3000)
                return
            except Exception as e:
                last_exc = e
        page.wait_for_timeout(250)
    if last_exc:
        raise last_exc
    raise TimeoutError("Upload files button not found in any frame")


def _wait_add_sources_ready(page, sidebar, *, timeout_ms: int = 60_000):
    """
    NotebookLM often hides the sidebar '+ Add sources' while the add-sources sheet is open.
    If 'Upload files' is already visible, we skip the first Add click.
    Returns (mode, skip_first_add_click) where mode is 'sidebar', 'panel', or None on failure.
    """
    if _add_sources_sheet_open(page):
        print("[OK] Add-sources sheet already open (Upload files visible).")
        return "panel", True

    for _ in range(2):
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
    page.wait_for_timeout(400)

    try:
        sidebar.get_by_role("button", name=ADD_SOURCE_BTN).first.wait_for(
            state="visible", timeout=timeout_ms
        )
        print("[OK] NotebookLM ready (+ Add sources visible).")
        return "sidebar", False
    except Exception:
        pass

    if _add_sources_sheet_open(page):
        print("[OK] Add-sources sheet visible (Upload files) after closing overlays.")
        return "panel", True

    return None, False


def _action(entry):
    return (entry.get("action") or "").strip().upper()


def resolve_project_path(p):
    """Paths in the manifest/review are relative to the project (script) directory."""
    if not p:
        return ""
    path = Path(p)
    if path.is_absolute():
        return str(path)
    return str(SCRIPT_DIR / path)


def get_upload_plan():
    """If comparison_review.json exists, return only files marked for upload (REPLACE / ADD).
    Returns None when no review file is found (caller falls back to full manifest)."""
    if not REVIEW_PATH.exists():
        return None
    with open(REVIEW_PATH, encoding="utf-8") as f:
        review = json.load(f)

    files = []
    seen = set()

    for pair in review.get("pairs", []):
        if _action(pair) == "REPLACE":
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
        if _action(item) == "ADD":
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


def run_upload(*, include_url_splits: bool = True):
    # Build upload list --------------------------------------------------------
    upload_plan = get_upload_plan()

    if upload_plan is not None:
        upload_items = upload_plan
        if not include_url_splits:
            before = len(upload_items)
            upload_items = [
                x for x in upload_items if not manifest_path_is_per_url_split(x.get("path", ""))
            ]
            dropped = before - len(upload_items)
            if dropped:
                print(
                    f"[INFO] Dropped {dropped} per-URL split file(s) from review "
                    f"(url_sources/ / toolbox_sources/). Omit --exclude-url-split-sources to upload them (default)."
                )
        print(f"--- Starting NoteBookLM upload ({len(upload_items)} file(s) from comparison review) ---")
    else:
        if not MANIFEST_PATH.exists():
            print(f"Error: {MANIFEST_PATH} not found. Run organize_content.py first.")
            return
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            manifest = json.load(f)
        upload_items = []
        for ch in manifest:
            for f in ch.get("files", []):
                pth = f.get("path", "")
                if not include_url_splits and manifest_path_is_per_url_split(pth):
                    continue
                upload_items.append({
                    "name": f["name"],
                    "path": pth,
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
            page = None
            page = pick_notebook_page(browser)
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
        try:
            page.bring_to_front()
        except Exception:
            pass

        title = page.title()
        print(f"Page title: {title}")

        on_login = "logga in" in title.lower() or "sign in" in title.lower() or "accounts.google.com" in page.url
        if on_login:
            print("[WAIT] You appear to be on the login page. Log in to Google in the browser window.")
            print("[WAIT] Waiting up to 2 minutes for the notebook page to load...")

        sidebar = resolve_sidebar_frame(page, iframe_timeout_ms=120_000)
        if sidebar is None:
            print("[FAIL] Add source / Lägg till not found (main frame or iframe). Log in and open Sources.")
            return

        mode, skip_next_add = _wait_add_sources_ready(page, sidebar, timeout_ms=60_000)
        if mode is None:
            print("[FAIL] Neither '+ Add sources' nor 'Upload files' appeared in time.")
            print("       Close any stuck dialogs, show the Sources tab, confirm the notebook URL, then retry.")
            return

        # Upload loop ----------------------------------------------------------
        current_chapter = None
        upload_failures = 0
        for file_info in upload_items:
            ch = file_info.get("chapter", "")
            if ch and ch != current_chapter:
                current_chapter = ch
                print(f"\n[FOLDER] Chapter: {current_chapter}")

            file_name = file_info["name"]
            file_path = resolve_project_path(file_info["path"])
            file_type = file_info["type"]

            print(f"   [WAIT] Uploading {file_name} ({file_type})...")

            try:
                if not skip_next_add:
                    add_sources_btn = sidebar.get_by_role("button", name=ADD_SOURCE_BTN).first
                    add_sources_btn.wait_for(state="visible", timeout=25_000)
                    add_sources_btn.click(timeout=15_000)
                    page.wait_for_timeout(800)
                else:
                    skip_next_add = False
                    page.wait_for_timeout(400)

                UPLOAD_FILE_EXTS = {".txt", ".pdf", ".md", ".docx", ".xlsx", ".mp3", ".wav", ".m4a"}
                ext = _effective_upload_ext(file_name, file_path)
                raw_manifest_path = file_info.get("path") or ""

                # Link / website rows: never upload url_sources/*.txt via file picker as "documents".
                # Manifest often sets ``name`` to the URL and ``type`` to ``url`` — do not use Copied text.
                is_link_row = (file_type or "").lower() == "url" or manifest_path_is_per_url_split(
                    raw_manifest_path
                )
                link_url = ""
                if is_link_row:
                    link_url = _source_url_from_extracted_txt(file_path)
                    if not link_url and isinstance(file_name, str) and file_name.strip().startswith(
                        ("http://", "https://")
                    ):
                        link_url = file_name.strip()

                if link_url:
                    display_title = link_url
                    _fill_website_or_paste_dialog(page, link_url, display_title, use_websites=True)
                elif is_link_row:
                    print(
                        f"   [FAIL] Link source but could not resolve URL "
                        f"(check Source URL: line in file): {file_path}"
                    )
                    upload_failures += 1
                    continue
                elif file_type in ("text", "audio") or (
                    os.path.exists(file_path) and ext in UPLOAD_FILE_EXTS
                ):
                    if not os.path.exists(file_path):
                        print(f"   [FAIL] File not found: {file_path}")
                        upload_failures += 1
                        continue
                    size_mb = os.path.getsize(file_path) / (1024 * 1024)
                    if size_mb > MAX_UPLOAD_SIZE_MB:
                        print(
                            f"   [SKIP] {file_name} ({size_mb:.1f} MB) exceeds "
                            f"{MAX_UPLOAD_SIZE_MB} MB CDP limit — upload manually in NotebookLM."
                        )
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                        page.keyboard.press("Escape")
                        continue
                    with page.expect_file_chooser() as fc_info:
                        _click_upload_files_any_frame(page, timeout_ms=15_000)
                    file_chooser = fc_info.value
                    file_chooser.set_files(file_path)
                else:
                    raw_path = file_info["path"]
                    if raw_path.startswith(("http://", "https://")):
                        content = raw_path
                        _fill_website_or_paste_dialog(
                            page,
                            content,
                            content,
                            use_websites=True,
                        )
                    else:
                        with open(file_path, "r", encoding="utf-8") as tf:
                            content = tf.read()
                        _fill_website_or_paste_dialog(
                            page,
                            content,
                            file_name.replace(".txt", ""),
                            use_websites=False,
                        )

                page.wait_for_timeout(3000)
                print(f"   [OK] {file_name} uploaded.")

            except Exception as e:
                print(f"   [FAIL] Failed to upload {file_name}: {e}")
                upload_failures += 1
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(200)
                except Exception:
                    pass

        print("\n--- Autonomous upload process finished. ---")
        if upload_failures:
            print(f"\n[FAIL] {upload_failures} upload(s) failed. Exiting with error.")
            sys.exit(1)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Upload sources to NotebookLM from review or manifest")
    ap.add_argument(
        "--exclude-url-split-sources",
        action="store_true",
        help="Skip url_sources/ and toolbox_sources/ when building the upload list from review or manifest",
    )
    ap.add_argument(
        "--include-url-split-sources",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    ns = ap.parse_args()
    if ns.include_url_split_sources:
        print(
            "[WARN] --include-url-split-sources is obsolete: per-URL splits are included by default. "
            "Use --exclude-url-split-sources to omit them."
        )
    run_upload(include_url_splits=not ns.exclude_url_split_sources)
