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
| **3** | `compare_sources.py` | Compares cleaned notebook sources vs edX content and writes `comparison_review.json` with suggested actions (`REPLACE`, `ADD`, `KEEP`, etc.). |

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
- `python delete_agent.py --dedupe-current --dry-run` — **list** which titles appear more than once. Safe and useful.
- `python delete_agent.py --dedupe-current` — **refuses to run.** See "Deduplication is retired" below.
- `python delete_agent.py --fuzzy` — match sources on shared words instead of exact titles. **Unsafe.** On a real 144-source notebook this made 78% of titles match some *other* source, because chapter-prefixed filenames share most of their words. Only with `--dry-run` first.
- `python extract_edx.py` — extract and parse the newest `course*.tar.gz` (or pass `--tar <file> --out <dir>`).
- `python organize_content.py` — build organized content and manifest.
- `python compare_sources.py` — generate `comparison_review.json` for review.
- `python compare_sources.py --apply` — apply the reviewed plan (delete + upload).
- `python delete_agent.py` — delete sources per `comparison_review.json`.
- `python delete_agent.py --dry-run` — preview exactly which names are interpreted as delete targets (pairs + current_only) before automation.
- `python upload_agent.py` — upload sources per `comparison_review.json` (or full manifest).

---

## Project structure

```
Update Study Buddy/
├── run_full_update.py            # Main entry: export, compare, review, apply
├── export_current_sources.py     # Step 0/2: scrape NotebookLM Sources → current_sources.json
├── delete_agent.py               # Delete sources during apply; --dedupe-current is opt-in, not automatic
├── notebooklm_client.py          # Shared CDP connection + tab selection
├── extract_edx.py                # Step 3: unpack .tar.gz (safe extraction) → edx_export/ + course_structure.json
├── organize_content.py           # Step 4: build Organized_Course_Content/ + processing_manifest.json
├── compare_sources.py            # Step 5: compare & match → comparison_review.json; --apply executes Step 6-7
├── upload_agent.py               # Step 7: upload sources (REPLACE / ADD) to NotebookLM
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
### Deduplication is retired

`--dedupe-current` no longer deletes anything. **A title does not identify a source.**

Web sources are titled from the target page's `<title>`, and many pages share one. This
notebook contains **seven** separate sources all titled `Microsoft Forms` — seven
different forms, not seven copies. Deleting "duplicates" by title would destroy six of
them. The information needed to tell a copy from a distinct source is the *content*,
which the script cannot see.

- `--dedupe-current --dry-run` still lists repeated titles, which is genuinely useful.
- `--dedupe-current` prints that list, explains the above, and exits non-zero.
- `--dedupe-current --confirm-unsafe-dedupe` proceeds, for someone who has checked the
  content themselves.

Resolve duplicates in the notebook UI, where you can open each source. Duplicates were
themselves caused by a broken export (comparison saw no existing sources and re-uploaded
everything), so a working export removes the need for routine deduplication.

### Safety behaviour

- **Deletion matches titles exactly** and is bounded by the number of copies actually
  present. `--fuzzy` restores word-overlap matching; on this notebook that made 78% of
  titles match some *other* source, so it is opt-in and warns.
- **Low-confidence matches default to `KEEP`.** Only pairs scoring ≥ 0.75 default to
  `REPLACE`. Lower-scoring pairs still appear in the review file — promote them yourself.
- **Negated actions are rejected.** `DO NOT DELETE` used to resolve to `DELETE`. Anything
  containing a negation now fails validation and names the row instead of guessing.
- **Uploads run before deletions**, and a failed upload cancels the deletions. A transient
  duplicate is recoverable; a source deleted before its replacement arrives is not.
- **Files are verified to exist** before anything is deleted.
- **The review pause prints the actual delete list** before asking you to press Enter.
- **Notebook URL:** NotebookLM is now "Gemini Notebook" at `notebook.google.com`. `config.py` points there; `notebooklm.google.com` still redirects. Override with `NOTEBOOKLM_PROJECT_URL`.
- **Archive layout:** `extract_edx.py` accepts either shape of Open edX archive — `course.xml` at the archive root (what an import archive built for upload contains), or wrapped in a single top-level directory (what a Studio export produces, named after the course run). If it finds neither it lists what the archive actually contains rather than just naming the missing file.

---

## Troubleshooting

- **Merge conflict after pull/rebase** — run `git add --renormalize .` once after pulling this change, then commit. For real conflicts: `git status`, edit conflict blocks, `git add <file>`, then continue with `git rebase --continue` or complete the merge commit.
- **"CDP connection failed"** — Start Chrome with `--remote-debugging-port=9222` and run the script again.
- **"current_sources.json not found"** — Run `export_current_sources.py` first (with Chrome on 9222).
- **"No edX export found"** — Place `course*.tar.gz` in the project folder, pass `--tar <file>`, or set `EDX_TAR_PATH`.
- **"course_structure.json not found"** — Run `extract_edx.py` before `organize_content.py`.
- **Uploads fail or wrong account** — Use Chrome with remote debugging and the correct Google account.
- **Large files skipped** — Check `MAX_UPLOAD_SIZE_MB` / `ENFORCE_UPLOAD_SIZE_LIMIT` in `config.py` (or env vars).
- **Duplicates remain after delete** — run `python delete_agent.py --dry-run` first and confirm planned names. The delete matcher now uses token/fuzzy matching for truncated UI labels; if names still miss, copy exact source titles from NotebookLM into `comparison_review.json`.
- **Pre-merge check** — run `python tools/check_conflict_markers.py` before commit/PR to ensure no `<<<<<<<`, `=======`, `>>>>>>>` markers remain.
