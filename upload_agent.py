import argparse
import json
import os
import re

import upload_verify as uv
from config import (
    ENFORCE_UPLOAD_SIZE_LIMIT,
    MANIFEST_PATH,
    MAX_UPLOAD_SIZE_MB,
    PROJECT_URL,
    REVIEW_PATH,
    UPLOAD_DEBUG_PATH,
    UPLOAD_RESULTS_PATH,
    normalize_action,
)
from notebooklm_client import BrowserConnectionError, connect, describe_page

ADD_SOURCE = re.compile(r"(\+\s*)?Add\s+source|Lägg\s+till\s+källa", re.I)

# How long to look for an upload's row, and for processing to finish. Module
# constants so the tests can shorten them.
POLL_INTERVAL = 2.0
ARRIVAL_BASE_SECONDS = 60.0
ARRIVAL_PER_MB_SECONDS = 15.0
DEFAULT_WAIT_MINUTES = 15.0


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


def run_upload(only=None, wait_minutes=DEFAULT_WAIT_MINUTES) -> int:
    """Upload the planned files. Returns 0 only if every one is verified in the notebook.

    compare_sources.py --apply deletes the replaced sources only on a 0 here, so
    "verified" means seen in the Sources panel and finished processing - not
    merely handed to the browser. Two phases: each file is uploaded and waited
    for until its row appears; then all of them are waited for together while
    the notebook processes them, which for audio can take minutes.
    """
    from playwright.sync_api import sync_playwright

    upload_plan = get_upload_plan()

    if upload_plan is not None:
        upload_items = upload_plan
        print(f"--- Starting NoteBookLM upload ({len(upload_items)} file(s) from comparison review) ---")
    else:
        if not os.path.exists(MANIFEST_PATH):
            print(f"Error: {MANIFEST_PATH} not found. Run organize_content.py first.")
            return 1
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

    if only:
        wanted = only.lower()
        upload_items = [i for i in upload_items if wanted in i["name"].lower()]
        if not upload_items:
            print(f"[FAIL] No planned upload has {only!r} in its name.")
            return 1
        print(f"--- --only {only!r}: {len(upload_items)} file(s) ---")

    if not upload_items:
        print("--- Nothing to upload. ---")
        return 0

    results = uv.load_results(UPLOAD_RESULTS_PATH)
    verified, unconfirmed, failed = [], [], []
    arrived = []            # uploads whose row appeared; phase B waits on these
    debug = []
    earlier = 0

    def record(key, **fields):
        if key:
            uv.record_result(UPLOAD_RESULTS_PATH, results, key,
                             notebook=PROJECT_URL, **fields)

    with sync_playwright() as p:
        try:
            print("--- Attempting to connect via CDP (Port 9222) ---")
            browser, page = connect(p)
            print(f"[OK] Connected via CDP. Driving tab: {describe_page(page)}")
        except BrowserConnectionError as e:
            print(f"[FAIL] {e}")
            return 1

        page.goto(PROJECT_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("load")

        add_sources_btn = page.get_by_role("button", name=ADD_SOURCE)
        try:
            add_sources_btn.first.wait_for(state="visible", timeout=30_000)
        except Exception as e:
            print(f"[FAIL] '+ Add sources' button did not appear: {e}")
            return 1
        page.wait_for_timeout(2500)     # let the panel render, as the export does

        def read():
            return uv.Panel(page.evaluate(uv.PANEL_JS))

        baseline = uv.stable_read(read, interval=min(POLL_INTERVAL, 1.0))
        print(f"[OK] Sources panel: {len(baseline.rows)} source(s) before uploading")

        current_chapter = None
        for file_info in upload_items:
            ch = file_info.get("chapter", "")
            if ch and ch != current_chapter:
                current_chapter = ch
                print(f"\n[FOLDER] Chapter: {current_chapter}")

            file_name = file_info["name"]
            file_path = os.path.abspath(file_info["path"])
            file_type = file_info["type"]

            key = uv.file_key(file_path) if os.path.exists(file_path) else None
            if key:
                decision, entry = uv.prior_decision(results, key, baseline, PROJECT_URL)
                if decision == "skip":
                    verified.append(file_name)
                    earlier += 1
                    print(f"   [OK] {file_name} is already in the notebook as "
                          f"'{entry['title'][:60]}' (verified {entry.get('at', '?')}); "
                          "not uploaded again.")
                    continue
                if decision == "promote":
                    arrived.append(dict(name=file_name, key=key, title=entry["title"],
                                        copies=entry.get("copies") or 1))
                    print(f"   [OK] {file_name} arrived on an earlier run; checking it "
                          "finished processing, not uploading it again.")
                    continue

            before = read()
            print(f"   [WAIT] Uploading {file_name} ({file_type})...")

            try:
                add_sources_btn = page.get_by_role("button", name=ADD_SOURCE).first
                add_sources_btn.wait_for(state="visible", timeout=25_000)
                add_sources_btn.click(timeout=15_000)
                page.wait_for_timeout(800)

                upload_file_exts = {".txt", ".pdf", ".md", ".docx", ".xlsx", ".mp3", ".wav", ".m4a"}
                is_file_path = os.path.exists(file_path) and os.path.splitext(file_name)[1].lower() in upload_file_exts
                is_upload_file = file_type in ("text", "audio") or is_file_path

                if is_upload_file:
                    if not os.path.exists(file_path):
                        print(f"   [FAIL] File not found: {file_path}")
                        failed.append((file_name, "file not found"))
                        dismiss(page)
                        continue
                    size_mb = os.path.getsize(file_path) / (1024 * 1024)
                    if size_mb > MAX_UPLOAD_SIZE_MB:
                        if ENFORCE_UPLOAD_SIZE_LIMIT:
                            print(
                                f"   [SKIP] {file_name} ({size_mb:.1f} MB) exceeds configured limit "
                                f"{MAX_UPLOAD_SIZE_MB} MB (ENFORCE_UPLOAD_SIZE_LIMIT=true)."
                            )
                            failed.append((file_name, f"exceeds {MAX_UPLOAD_SIZE_MB} MB limit"))
                            dismiss(page)
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

            except Exception as e:
                reason = str(e).splitlines()[0][:120]
                failed.append((file_name, reason))
                record(key, name=file_name, status=uv.FAILED, why=reason)
                print(f"   [FAIL] Failed to upload {file_name}: {e}")
                dismiss(page)
                continue

            # Handing the file to the browser is not arrival. Wait for its row.
            size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            timeout = uv.arrival_timeout(size, base=ARRIVAL_BASE_SECONDS,
                                         per_mb=ARRIVAL_PER_MB_SECONDS)
            outcome, now = uv.poll(read, uv.arrival_check(before, file_name), timeout,
                                   interval=POLL_INTERVAL)
            if outcome is None:
                reason = f"never appeared in the Sources panel within {timeout:.0f}s"
            elif outcome[0] == uv.FAILED:
                reason = outcome[2]
            else:
                reason = ""
            if reason:
                failed.append((file_name, reason))
                record(key, name=file_name, status=uv.FAILED, why=reason)
                debug.append(uv.debug_entry(reason, file_name, before=before, after=now))
                print(f"   [FAIL] {file_name}: {reason}")
                dismiss(page)
                continue

            title = outcome[1]
            copies = len(now.states(title))
            arrived.append(dict(name=file_name, key=key, title=title, copies=copies))
            record(key, name=file_name, status="appeared", title=title, copies=copies)
            named = ("" if uv.canonical(os.path.splitext(file_name)[0]) in uv.canonical(title)
                     else f" as '{title[:60]}'")
            print(f"   [..] {file_name} arrived{named}; the notebook is processing it.")

        # Phase B: wait for every arrival to finish processing, together.
        if arrived:
            print(f"\n--- Waiting for {len(arrived)} upload(s) to finish processing "
                  f"(up to {wait_minutes:g} min) ---")
            outcomes = {}
            _, now = uv.poll(read, uv.settle_check(baseline, arrived, outcomes),
                             wait_minutes * 60, interval=POLL_INTERVAL)
            uv.unsettled(now, arrived, outcomes, wait_minutes)
            final = read()      # one more look: accepted, then dropped, is a failure
            for index, upload in enumerate(arrived):
                status, why = outcomes[index]
                if status == uv.VERIFIED and not uv.still_there(final, upload):
                    status, why = uv.FAILED, "gone from the Sources panel after processing"
                record(upload["key"], name=upload["name"], status=status,
                       title=upload["title"], copies=upload["copies"], why=why)
                if status == uv.VERIFIED:
                    verified.append(upload["name"])
                    print(f"   [OK] {upload['name']} verified in the notebook.")
                    continue
                (unconfirmed if status == uv.UNCONFIRMED else failed).append(
                    (upload["name"], why))
                debug.append(uv.debug_entry(why, upload["name"], before=baseline,
                                            after=final))

    # compare_sources.apply_review deletes the replaced sources only on a clean
    # exit, so anything short of verified must be reported as failure.
    print(f"\n--- Upload finished: {len(verified)} verified"
          + (f" ({earlier} already in the notebook)" if earlier else "")
          + f", {len(unconfirmed)} still processing, {len(failed)} failed ---")
    for name, reason in unconfirmed:
        print(f"   [WAIT] {name}: {reason}")
    for name, reason in failed:
        print(f"   [FAIL] {name}: {reason}")
    if debug and uv.save_debug(UPLOAD_DEBUG_PATH, debug):
        print(f"   What the Sources panel showed: {UPLOAD_DEBUG_PATH}")
    return 1 if (failed or unconfirmed) else 0


def dismiss(page):
    """Close whatever dialog a failed upload left open."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    except Exception:
        pass


def parse_args(argv=None):
    """The parser rejects unknown flags instead of ignoring them."""
    parser = argparse.ArgumentParser(
        description="Upload sources to the notebook per comparison_review.json, and "
                    "verify each one arrived and finished processing.",
        epilog="Falls back to the full processing_manifest.json when no review file "
               "exists. Exits 0 only when every upload is verified; files verified "
               f"on an earlier run ({UPLOAD_RESULTS_PATH}) are not uploaded again.")
    parser.add_argument("--only", metavar="TEXT",
                        help="upload only planned files whose name contains TEXT - "
                             "a supervised first run, one file at a time")
    parser.add_argument("--wait-minutes", type=float, default=DEFAULT_WAIT_MINUTES,
                        help="how long to wait for the notebook to finish processing "
                             f"the uploads (default {DEFAULT_WAIT_MINUTES:g})")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(run_upload(only=args.only, wait_minutes=args.wait_minutes))
