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
| **4** | `delete_agent.py` | Deletes sources per `comparison_review.json` (REPLACE / DELETE / current-only DELETE). **By default removes one row per planned name**; use `--all-dupes` only if you intend to strip every duplicate row for each name (see script help). |
| **5** | `upload_agent.py` | Uploads files marked REPLACE or ADD. Falls back to full manifest if no review file exists. |
| **6** | `delete_agent.py --dedupe` | **After uploads:** scrolls Sources, writes `sources_panel_catalog.json`, then deletes duplicate titles (one row left per title). |

`python compare_sources.py --apply` runs **4 → 5 → 6** when the review includes any deletes or uploads. Steps 4–6 are skipped if the review has no deletes and no uploads.

If any **critical** step fails (non-zero exit), the pipeline stops before later steps. Step 6 logs a warning if dedupe exits non-zero but does not abort the whole apply.

---

## Prerequisites

- **Python 3.10+**
- Install dependencies:

```powershell
pip install -r requirements.txt
```

- **Chrome** installed (used for NotebookLM via remote debugging).
- **ffmpeg** on `PATH` (used by `organize_content.py` to convert video to MP3).
- **edX course export** `.tar.gz` in the project folder (see "Input files" below).

Generated content (`edx_export/`, `Organized_Course_Content/`, JSON manifests, media) is listed in `.gitignore` and rebuilt by the pipeline — only scripts and docs belong in git.

---

## How to run

### One-time: start Chrome with remote debugging

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

In that Chrome window, sign in to the Google account you use for NotebookLM.

### Full update (recommended)

1. Put your edX course export in the project folder (name it `course*.tar.gz`, or set `EDX_TAR_PATH`, or pass `--tar` to `extract_edx.py`).
2. From the project folder:

```powershell
python run_full_update.py
```

3. The pipeline runs Phase 1, then pauses for you to review `comparison_review.json`.
4. Edit actions as needed, save, press Enter. Phase 2 runs plan deletes, uploads, then a final `--dedupe` pass so duplicate rows are collapsed.

### Run individual steps

- `python export_current_sources.py` — refresh `current_sources.json` only.
- `python extract_edx.py` — extract and parse the `.tar.gz`.
- `python organize_content.py` — build organized content and manifest.
- `python compare_sources.py` — generate `comparison_review.json` for review.
- `python compare_sources.py --apply` — apply the reviewed plan: **plan deletes** → **upload** → **`delete_agent.py --dedupe`** (one row per title).
- `python delete_agent.py` — plan deletes only (`comparison_review.json`); one row per name unless `--all-dupes`.
- `python delete_agent.py --dedupe` — catalog all rows, then remove duplicate titles (used at end of `--apply`; run alone anytime).
- `python upload_agent.py` — upload sources per `comparison_review.json` (or full manifest).

---

## Project structure

```
Update Study Buddy/
├── run_full_update.py            # Main entry: two-phase pipeline with review pause
├── config.py                     # Paths, env overrides, chapter_dir_slug() naming
├── requirements.txt              # Python dependencies (playwright)
├── .gitignore                    # Ignores exports, organized content, generated JSON/media
├── export_current_sources.py     # Step 0: scrape NotebookLM Sources → current_sources.json
├── extract_edx.py                # Step 1: unpack .tar.gz → edx_export/ + course_structure.json
├── organize_content.py           # Step 2: build Organized_Course_Content/ + processing_manifest.json
├── validate_manifest.py          # Step 2b: structural manifest checks
├── compare_sources.py            # Step 3: compare & match → comparison_review.json; --apply to execute
├── delete_agent.py               # Step 4: remove sources (REPLACE / DELETE) from NotebookLM
├── upload_agent.py               # Step 5: upload sources (REPLACE / ADD) to NotebookLM
│
├── Archive/                      # Legacy/pruned content (gitignored; not used by pipeline)
├── edx_export/                   # Raw course files (from extract; gitignored)
├── Organized_Course_Content/     # Chapter folders: NN_ChapterName/ (from organize; gitignored)
│
├── course.*.tar.gz               # Your edX export (gitignored)
└── README.md                     # This file
```

**Chapter folder naming:** Folders mirror the **course home page numbering** (see `chapter_dir_slug()` in `config.py`):

| Course outline | Folder |
|----------------|--------|
| `1. Welcome & What GenAI Can Do Today` | `01_Welcome_What_GenAI_Can_Do_Today/` |
| `2. Learning With AI` | `02_Learning_With_AI/`|
| `3. Profession Specific Use Cases`| `03_Profession_Specific_Usecases/`|
| `3A. Track: Business/Industry` | `03A_Track_Business_Industry/` |
| `3B. Track: Academia`| `03B_Track_Academi/`|
| `3C. Track: Public Sector`| `03C_Track_Public_Sector/` |
| `4. What AI is (+Ethics of generative AI)` | `04_What_AI_is_Ethics_of_generative_AI/` |
| `5. Driving Change`| `05_Driving_Change/` |
| `9. Final seminar` | `09_Final_seminar/` |

Track modules use `03A` / `03B` / `03C` (not sequential `04` / `05` / `06`). Legacy names are archived when you re-run `organize_content.py`.

**Notebook URL:** Hardcoded as `PROJECT_URL` in `export_current_sources.py`, `delete_agent.py`, and `upload_agent.py`. Edit that variable if you use a different notebook.

---

## Configuration notes

- **edX export archive:** No filename is hardcoded in code. Resolution order: `python extract_edx.py --tar path\to\export.tar.gz`, or environment variable **`EDX_TAR_PATH`**, or the **newest** `course*.tar.gz` in the project folder (see `config.py`). **`delete_agent.py` does not use the tar** — only `extract_edx.py` / `analyze_tar.py` / `organize_content.py` use the extracted tree.
- **Extract / organized folders:** Defaults `edx_export` and `Organized_Course_Content`; override with `EDX_EXTRACT_DIR`, `ORGANIZED_CONTENT_DIR`, or `organize_content.py --extract-dir` / `--output-dir`.
- **File size:** Files larger than 50 MB are skipped during upload (CDP limit). Threshold: `MAX_UPLOAD_SIZE_MB` in `upload_agent.py`.
- **Account:** Use the Chrome window started with `--remote-debugging-port=9222` and log in with the Google account that has editor access to the notebook.
- **Duplicate cleanup:** `delete_agent.py --dedupe` builds `sources_panel_catalog.json`, then removes extra rows per title. Plan mode (`delete_agent.py` alone) deletes by name from `comparison_review.json` using only the row **overflow (⋮)** menu.
- **Sources panel:** Use Chrome with `--remote-debugging-port=9222`, open the correct notebook, and the **Sources** column so the list is visible before running automation.

---

## Troubleshooting

- **"CDP connection failed"** — Start Chrome with `--remote-debugging-port=9222` and run the script again.
- **"current_sources.json not found"** — Run `export_current_sources.py` first (with Chrome on 9222).
- **No edX archive found** — Add a `course*.tar.gz` in the project folder, set `EDX_TAR_PATH`, or run `python extract_edx.py --tar path\to\file.tar.gz`.
- **"course_structure.json not found"** — Run `extract_edx.py` before `organize_content.py`.
- **Uploads fail or wrong account** — Use Chrome with remote debugging and the correct Google account.
- **Large files skipped** — Check the 50 MB limit in `upload_agent.py` or upload very large files manually.
