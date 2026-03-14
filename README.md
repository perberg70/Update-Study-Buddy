# Update Study Buddy

Automates updating a **Google NotebookLM** notebook from an **edX course export**: extract content, compare with current sources, let you review proposed changes, then apply deletions and uploads so your study notebook stays in sync with the course.

---

## Purpose

- **Input:** An edX course export (`.tar.gz`) and your existing NotebookLM notebook.
- **Output:** The same notebook with old sources removed and new sources added (by chapter: merged text files, MP3s from videos, optional PDFs from static assets).
- **Use case:** When the course is re-run or content changes, one command refreshes the notebook instead of manually re-uploading and cleaning up.

---

## Workflow (full update)

The pipeline runs in two phases when you execute `run_full_update.py`:

### Phase 1 — Extract & Compare

| Step | Script | What it does |
|------|--------|--------------|
| **0** | `export_current_sources.py` | Connects to Chrome (CDP), opens your NotebookLM notebook, scrapes the **Sources** panel, and writes `current_sources.json`. |
| **1** | `extract_edx.py` | Extracts the edX `.tar.gz` into `edx_export/` and parses course structure into `course_structure.json`. |
| **2** | `organize_content.py` | Builds `Organized_Course_Content/` by chapter: merges HTML into `.txt`, downloads video assets and converts to MP3, writes `processing_manifest.json`. |
| **3** | `compare_sources.py` | Matches new files against current notebook sources by **name similarity** and **content keywords**. Writes `comparison_review.json` with suggested actions. |

### Review pause

The pipeline pauses and asks you to open `comparison_review.json` and review/adjust the actions:

- **`pairs`** — Each new file matched to an existing source.
  - `REPLACE` = delete old + upload new (default for good matches)
  - `DELETE` = delete old, don't upload new
  - `KEEP` = leave both untouched
- **`current_only`** — Existing notebook sources with no new match.
  - `DELETE` = remove from notebook
  - `KEEP` = leave as-is (default)
- **`new_only`** — New files with no existing match.
  - `ADD` = upload to notebook (default)
  - `SKIP` = don't upload

Save the file and press Enter in the terminal to continue.

### Phase 2 — Apply

| Step | Script | What it does |
|------|--------|--------------|
| **4** | `delete_agent.py` | Reads `comparison_review.json`. Deletes **all copies** of each source marked REPLACE or DELETE from the notebook. |
| **5** | `upload_agent.py` | Reads `comparison_review.json`. Uploads only files marked REPLACE or ADD. Falls back to full manifest if no review file exists. |

If any step fails (non-zero exit), the pipeline stops.

---

## Prerequisites

- **Python 3** with packages: `playwright` (and Chromium/Chrome for automation).
- **Chrome** installed (used for NotebookLM via remote debugging).
- **ffmpeg** on `PATH` (used by `organize_content.py` to convert video to MP3).
- **edX course export** `.tar.gz` in the project folder (see "Input files" below).

---

## How to run

### One-time: start Chrome with remote debugging

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

In that Chrome window, sign in to the Google account you use for NotebookLM.

### Full update (recommended)

1. Put your edX course export in the project folder (`course*.tar.gz`), or set `EDX_TAR_PATH`.
2. (Optional but recommended) run preflight checks:

```powershell
python preflight.py
```

3. From the project folder:

```powershell
python run_full_update.py
```

4. The pipeline runs Phase 1, then pauses for you to review `comparison_review.json`.
5. Edit actions as needed, save, press Enter. Phase 2 runs the deletions and uploads.

### Run individual steps

- `python export_current_sources.py` — refresh `current_sources.json` only.
- `python extract_edx.py` — extract and parse the newest `course*.tar.gz` (or pass `--tar <file> --out <dir>`).
- `python organize_content.py` — build organized content and manifest.
- `python compare_sources.py` — generate `comparison_review.json` for review.
- `python compare_sources.py --apply` — apply the reviewed plan (delete + upload).
- `python delete_agent.py` — delete sources per `comparison_review.json`.
- `python upload_agent.py` — upload sources per `comparison_review.json` (or full manifest).

---

## Project structure

```
Update Study Buddy/
├── run_full_update.py            # Main entry: two-phase pipeline with review pause
├── export_current_sources.py     # Step 0: scrape NotebookLM Sources → current_sources.json
├── extract_edx.py                # Step 1: unpack .tar.gz (safe extraction) → edx_export/ + course_structure.json
├── organize_content.py           # Step 2: build Organized_Course_Content/ + processing_manifest.json
├── compare_sources.py            # Step 3: compare & match → comparison_review.json; --apply to execute
├── delete_agent.py               # Step 4: remove sources (REPLACE / DELETE) from NotebookLM
├── upload_agent.py               # Step 5: upload sources (REPLACE / ADD) to NotebookLM
│
├── current_sources.json          # Current source names in the notebook (from export)
├── course_structure.json         # Course structure tree (from extract)
├── processing_manifest.json      # Files per chapter: { name, path, type } (from organize)
├── comparison_review.json        # Review plan: pairs + current_only + new_only with actions
│
├── edx_export/                   # Raw course files (from extract)
├── Organized_Course_Content/     # Chapter folders with .txt and .mp3 (from organize)
│
├── course.*.tar.gz               # Your edX export
├── preflight.py                  # Optional environment/input validation
└── README.md                     # This file
```

**Notebook URL / CDP / limits:** Configurable via `config.py` and environment variables (`NOTEBOOKLM_PROJECT_URL`, `NOTEBOOKLM_CDP_URL`, `MAX_UPLOAD_SIZE_MB`, etc.).

---

## Configuration notes

- **File size:** Upload size checks are configurable. Default threshold is `MAX_UPLOAD_SIZE_MB=200`; by default oversized files are attempted with a warning. Set `ENFORCE_UPLOAD_SIZE_LIMIT=true` to hard-skip oversized files.
- **Account:** Use the Chrome window started with `--remote-debugging-port=9222` and log in with the Google account that has editor access to the notebook.
- **Duplicate cleanup:** `delete_agent.py` loops until all copies of a source name are removed, so accumulated duplicates are cleaned up in one run.

---

## Troubleshooting

- **Merge conflict after pull/rebase** — Keep generated artifacts out of commits and normalize line endings. This repo ships `.gitattributes`; run `git add --renormalize .` once, then commit. Resolve conflicts with `git status`, edit conflicted files, `git add <file>`, then continue with `git rebase --continue` or `git commit` for merge.
- **"CDP connection failed"** — Start Chrome with `--remote-debugging-port=9222` and run the script again.
- **"current_sources.json not found"** — Run `export_current_sources.py` first (with Chrome on 9222).
- **"No edX export found"** — Place `course*.tar.gz` in the project folder, pass `--tar <file>`, or set `EDX_TAR_PATH`.
- **"course_structure.json not found"** — Run `extract_edx.py` before `organize_content.py`.
- **Uploads fail or wrong account** — Use Chrome with remote debugging and the correct Google account.
- **Large files skipped** — Check `MAX_UPLOAD_SIZE_MB` / `ENFORCE_UPLOAD_SIZE_LIMIT` in `config.py` (or env vars).
