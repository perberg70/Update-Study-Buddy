# Update Study Buddy

Automates updating a **Google NotebookLM** notebook from an **edX course export**: extract content, compare with current sources, let you review proposed changes, then apply deletions and uploads so your study notebook stays in sync with the course.

---

## Quick start

1. Export the course from edX Studio: **Tools → Export → Export Course Content**.
2. Put the resulting `course.<run-id>.tar.gz` in **`course_exports/`**.
3. From the project folder:

```powershell
python start_run.py
```

It finds the export, confirms it really is a course archive, records which one it read,
lists the modules, and prints the commands to run next. Run it at the start of every
update: the two questions that have caused the most trouble here are *where does the
export go* and *which one is being read*, and this answers both before any work happens.

```powershell
pip install -r requirements.txt   # first time
python preflight.py               # checks Python, ffmpeg, packages, branch, export
```

---

## Purpose

- **Input:** An edX course export (`.tar.gz`) and your existing NotebookLM notebook.
- **Output:** either
  - **module PDFs** — one structured PDF per module, units and subunits as headings, with
    prose and video transcripts inline; or
  - **a synced notebook** — old sources removed and new ones added, by chapter: merged
    text files and MP3s from videos.
- **Use case:** when the course is re-run or content changes, refresh the study material
  instead of re-uploading and cleaning up by hand.

**Static assets are not uploaded.** `organize_content.py` copies PDFs/DOCX/XLSX from the
course into `Organized_Course_Content/Global_Assets/`, but nothing adds them to the
manifest, so they never reach the notebook. Copy them across by hand if you want them
there. (Known gap, not yet fixed.)

---

## Workflow (full update)

The pipeline runs in two phases when you execute `run_full_update.py`:

### Phase 1 — Extract & Compare

| Step | Script | What it does |
|------|--------|--------------|
| **0** | `export_current_sources.py` | Connects to Chrome (CDP), opens your NotebookLM notebook, scrapes the **Sources** panel, and writes `current_sources.json`. |
| **1** | `extract_edx.py` | Parses the edX `.tar.gz` into `course_structure.json`, recording which archive it read. Nothing is unpacked. |
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

- **Python 3.9+**, then `pip install -r requirements.txt`.
- **ffmpeg** on `PATH` — converts video to MP3 and to the audio transcription reads.
- **Chrome** — only for the notebook-sync half, via remote debugging.
- **An edX course export** in `course_exports/` (see Quick start).

Three packages are optional, each enabling one feature. `python preflight.py` reports
which are installed and what each does:

| Package | Enables |
|---|---|
| `playwright` | anything that drives the notebook (export / delete / upload) |
| `reportlab` | module PDFs |
| `youtube-transcript-api` | captions for YouTube-hosted videos |
| `faster-whisper` | local transcription of short videos |

---

## How to run

### One-time: start Chrome with remote debugging

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

In that Chrome window, sign in to the Google account you use for NotebookLM.

### Full update (recommended)

1. Put your edX course export in `course_exports/` and run `python start_run.py`
   (see Quick start). Or name one with `--tar`, or set `EDX_TAR_PATH`.
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

### Command reference

**Start a run**

- `python start_run.py` — find the export, parse it, print what to do next. Start here.
- `python preflight.py` — Python, ffmpeg, packages, git branch, and whether the export reads.
- `python extract_edx.py --tar <file>` — parse one archive into `course_structure.json`.
  With no `--tar` it uses the only export it finds, and **refuses if there are two**.

**Build a module PDF**

- `python tools/build_module_pdf.py --list` — list the modules.
- `python tools/fetch_youtube_transcripts.py --module 1` — captions for YouTube videos.
- `python tools/transcribe_videos.py --module 1 --dry-run` — what local transcription
  would do, and roughly how long. Then drop `--dry-run` to run it.
- `python tools/build_module_pdf.py --module 1` — build `Module_1.pdf`.

**Sync the notebook** (needs Chrome on 9222)

- `python run_full_update.py` — the whole pipeline with a review pause.
- `python export_current_sources.py` — refresh `current_sources.json` only.
- `python compare_sources.py` — generate `comparison_review.json` for review.
- `python compare_sources.py --apply` — apply the reviewed plan (upload, then delete).
- `python organize_content.py` — build organized content and the manifest.
- `python upload_agent.py` — upload per `comparison_review.json` (or the full manifest).
- `python delete_agent.py --dry-run` — preview exactly which names are delete targets.
  Every run announces its mode first: `[MODE] dry run` or `[MODE] LIVE`.
- `python delete_agent.py` — delete per `comparison_review.json`. **Exact titles only.**
- `python delete_agent.py --fuzzy` — match on shared words instead. **Unsafe:** on a real
  144-source notebook this made 78% of titles match some *other* source, because
  chapter-prefixed filenames share most of their words. `--dry-run` first, always.
- `python delete_agent.py --dedupe-current --dry-run` — **list** repeated titles. Safe.
- `python delete_agent.py --dedupe-current` — **refuses.** See "Deduplication is retired".

**Diagnose**

- `python tools/which_archive.py` — which archives exist, and which one is in use.
- `python tools/find_text.py "<phrase>"` — trace text in a PDF back to its component, and
  say why the course page might not show it.
- `python tools/video_report.py --module 1 --verbose` — every video, its duration, and
  whether a transcript exists.
- `python tools/preview_names.py` — what `organize_content.py` would name things, scored
  against the notebook's current sources, without downloading anything.
- `python tools/check_conflict_markers.py` — pre-commit check for conflict markers.

---

## Project structure

```
Update Study Buddy/
├── start_run.py                  # Start here: find the export, parse it, say what is next
├── preflight.py                  # Environment, packages, git branch, export readability
├── run_full_update.py            # Notebook sync: export, compare, review, apply
│
├── config.py                     # Paths, URLs, limits, where the export is looked for
├── olx_archive.py                # Reads the OLX tree straight out of the .tar.gz
├── extract_edx.py                # Parse .tar.gz (no unpacking) → course_structure.json
├── organize_content.py           # Build Organized_Course_Content/ + processing_manifest.json
├── compare_sources.py            # Compare & match → comparison_review.json; --apply runs it
├── export_current_sources.py     # Scrape the notebook's Sources panel → current_sources.json
├── delete_agent.py               # Delete sources; exact titles by default, --fuzzy is opt-in
├── upload_agent.py               # Upload sources (REPLACE / ADD)
├── notebooklm_client.py          # Shared CDP connection + tab selection
│
├── course_exports/               # >>> Put your edX .tar.gz here <<<
├── transcripts/                  # <video url_name>.txt — from OLX, YouTube, Teams or Whisper
├── Organized_Course_Content/     # Chapter folders with .txt and .mp3, and the module PDFs
│
├── course_structure.json         # Parsed course tree + which archive it came from
├── processing_manifest.json      # Files per chapter: { name, path, type }
├── current_sources.json          # Source names currently in the notebook
├── comparison_review.json        # Review plan: pairs + current_only + new_only
│
├── tools/
│   ├── build_module_pdf.py       # One structured PDF per module
│   ├── transcribe_videos.py      # Local Whisper for the short videos
│   ├── fetch_youtube_transcripts.py
│   ├── which_archive.py          # Which archive is in use, and is it unchanged
│   ├── find_text.py              # Trace a phrase back to its course component
│   ├── video_report.py           # Every video: duration, transcript, visibility
│   ├── preview_names.py          # Predicted names scored against the notebook
│   └── check_conflict_markers.py
├── tests/                        # python tests/test_*.py — no framework needed
├── requirements.txt
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

### Where the course export goes

`course_exports/` — see Quick start. This is how it is found, in detail.

Search order: `--tar`, then `EDX_TAR_PATH`, then the first of `course_exports/`, the
project folder and the working directory that holds a `course*.tar.gz`. The first
directory with a match wins, so `course_exports/` is authoritative once in use, and an
archive already sitting in the project folder keeps working.

**Two in the same folder and no `--tar` refuses and lists both** rather than choosing.
Choosing by date is not safe here: OneDrive rewrites modification times on sync, so the
newest file is not the newest export. An archive in a lower-priority location is reported
as ignored, never silently passed over.

### edXUpdater is a different tool, going the other way

`edXUpdater` is a separate repository that **builds** an Open edX *import* archive and
uploads it **to** edX, to update the course home page. This project **reads** a Studio
*export* pulled **from** edX. Opposite directions, different trees, and nothing here
calls anything there.

They collide by filename. `New-EdxImportArchive.ps1` produces the exact name
`course.tar.gz`, which `course*.tar.gz` matches, and it puts `course.xml` at the archive
root — which is a layout this project accepts. So dropped into `course_exports/` it would
be found, parsed and used to build study material from the wrong tree, without complaint.

`start_run.py` and `preflight.py` therefore flag an archive named exactly
`course.tar.gz`:

```
[WARN] This looks like an edXUpdater import archive, not a course
       export - named exactly course.tar.gz.
```

A warning, not a refusal — the name is strong evidence, not proof, and an export may have
been renamed. A Studio export carries a run id: `course.hp_m6v88.tar.gz`.

**Keep edXUpdater's output out of `course_exports/`.**

### Which archive did this come from?

**Nothing is unpacked.** Every step reads the course straight out of the `.tar.gz`
(`olx_archive.py`). This used to work differently: `extract_edx.py` unpacked into
`edx_export/` and never cleared it, so extracting a second export left behind every file
the first contained — producing a course whose structure came from one export and whose
content came from another, with nothing recording which. That directory is gone, and with
it the only way two exports could blend.

`extract_edx.py` records what it read — path, size, a hash prefix, the course root inside
the archive, and when it was read — into `course_structure.json` under `_source`. Every
later step reopens **that same archive** rather than guessing again, and
`build_module_pdf.py` and `preview_names.py` print it, so any generated document can be
traced back to its export.

To see what you have:

```powershell
python tools/which_archive.py
```

It lists every `course*.tar.gz` it can find with its hash and course root, shows what
`course_structure.json` records, and exits non-zero if that archive has gone missing or
changed since it was parsed.

**Note on automatic selection:** the code never names a particular archive. With no
`--tar` and no `EDX_TAR_PATH`, it uses the only `course*.tar.gz` in the folder — and if
there is more than one it **refuses and lists them** rather than choosing. It used to take
the newest by modification time, which is not the newest export: OneDrive rewrites mtimes
on sync, so the pick could change with no new export and nothing in the output naming what
was read.

### Content the course page does not show

An export carries what the course *holds*, not what a student *sees*. Two things reach a
generated document without ever being on the page:

- **Units hidden in the OLX** — `visible_to_staff_only`, `hide_from_toc`. Retired or
  draft material stays in the export.
- **Text hidden by CSS** — `sr-only`, `visually-hidden`, `display:none`. This is the
  standard screen-reader pattern: an image carries a long text description that sighted
  users never see. It is correct markup, but read as body prose in a study document it
  looks like content that contradicts the page.

`build_module_pdf.py` skips both by default and **says what it skipped** — a silent drop
would be as bad as a silent inclusion. `--include-hidden` keeps everything.

### Structure and content must come from the same export

A document takes its skeleton from `course_structure.json` and its text from the archive.
Nothing forced those to be the same export, and when they are not you get a document whose
structure says one thing and whose text says another.

`build_module_pdf.py`, `transcribe_videos.py` and `organize_content.py` now **refuse** to
run when `course_structure.json` records no source archive, or records a different one
from the archive being read. A warning was not enough: the last one printed correctly,
above sixty lines of output, and was gone by the time it mattered.

```
[FAIL] course_structure.json was parsed from a different archive than the
  one being read. The document would take its structure from one
  export and its content from another.
  structure came from: sha:3ff949c4077e (course.hp_m6v88.tar.gz)
  about to read:       sha:a91be0771d23 (course.old.tar.gz)
  Fix:      python extract_edx.py --tar course.old.tar.gz
  Override: --allow-stale (builds anyway, provenance unverified)
```

The read-only reporters (`video_report.py`, `preview_names.py`, `find_text.py`,
`which_archive.py`) still only warn — they produce no document, and `which_archive.py`
exists precisely to be run when things are inconsistent.

To trace a specific phrase back to its source:

```powershell
python tools/find_text.py "a phrase from the PDF"
```

It names the component, its chapter/unit/subunit, every visibility attribute on that
chain, whether CSS hides the match, and prints the raw HTML around it. If the phrase is
in no component at all, the document was built from a different export — which it now
tells you outright.

Note: an image's `alt` text is still included, as `[Image: ...]`. The PDF embeds no
images, so that text is the only trace of one. Where an image's long description was
itself `sr-only`, the alt text can end up referring to a description that was skipped.

### Video transcripts

Three sources, cheapest first. All of them write `<video url_name>.txt` into
`transcripts/`, which `build_module_pdf.py` reads automatically.

| Source | Command | Cost |
|---|---|---|
| Transcripts inside the export | none — used automatically | free |
| YouTube captions | `python tools\fetch_youtube_transcripts.py --module 1` | one HTTP request each |
| Teams recordings | export the VTT, drop it in `transcripts\` | manual, best quality for webinars |
| Everything else (short clips) | `python tools\transcribe_videos.py --module 1` | local CPU time |

`transcribe_videos.py` runs **entirely on your machine** — `AGENTS.md` forbids sending
course content to a third-party service, so no cloud speech-to-text option exists here.
It needs one of:

```powershell
pip install faster-whisper     # recommended: several times quicker on CPU
pip install openai-whisper     # alternative; pulls in torch
```

Every optional package is listed in `requirements.txt`, and `python preflight.py` reports
which are installed and what each one enables — so a missing one shows up before a run
rather than partway through it:

```
[OK]   reportlab available - module PDFs (tools/build_module_pdf.py)
[WARN] youtube-transcript-api missing - YouTube captions (tools/fetch_youtube_transcripts.py)
       pip install youtube-transcript-api
```

They are warnings, not failures: none of them gates the core pipeline.

On Windows, `ctranslate2` and numpy/MKL each load their own Intel OpenMP runtime, which
aborts the process with `OMP: Error #15`. The tool sets `KMP_DUPLICATE_LIB_OK=TRUE` for
its own run and says so — you should not need `set KMP_DUPLICATE_LIB_OK=TRUE` yourself. If
an abort happens anyway, setting it in the shell first still works.

It transcribes only what nothing cheaper already covers, and only short videos —
`--max-minutes` (default 20) keeps hour-long webinars out, since their Teams export is
both faster and more accurate. Always start with `--dry-run`: it prints exactly which
videos it would do, which it would skip and why, and a rough time estimate, without
downloading anything.

### Video downloads

`organize_content.py` fetches and transcodes every video with a direct `.mp4`, which for
this course is 17 files and several hours of audio. It is built to survive a long
unattended run:

- **Timeout** (`DOWNLOAD_TIMEOUT`, default 60s) — a stalled CDN response fails instead of
  hanging the run with no output.
- **Retry with backoff** (`DOWNLOAD_RETRIES`, default 3) — 2s, then 4s.
- **Resume** — a video whose `.mp3` already exists is skipped, so a run interrupted at
  video 15 of 17 does not start again from the first.
- **Truncation is rejected** — a response shorter than its `Content-Length` is discarded
  rather than kept as a valid file.
- **ffmpeg's own error text is printed**, not just `CalledProcessError`.
- Downloads land in a `.part` file and are renamed on success, so an interrupted transfer
  can never be mistaken for a finished one. Temporary files are removed either way.
- A failure is reported per video, the rest continue, and the exit code is non-zero with a
  summary of what to re-run.

### A misspelled flag cannot disarm a safety

Every script parses with `argparse` and **rejects** anything it does not recognise. That
sounds like housekeeping; it was not. These flags were read by substring-testing
`sys.argv`, so a typo silently changed behaviour — and the danger was one-directional:

| flag | typo'd | was |
|---|---|---|
| `delete_agent.py --dry-run` | `--dryrun` | **a real deletion**, unundoable |
| `compare_sources.py --apply` | `--aply` | **the hand-edited review file overwritten** |
| `--fuzzy` | `--fuzy` | exact matching — harmless |
| `--confirm-unsafe-dedupe` | any typo | a refusal — harmless |

`delete_agent.py` also announces its mode on the first line, so the state a typo used to
flip is now stated rather than assumed. And `compare_sources.py` says so before replacing
an existing `comparison_review.json`, since that file holds your edits.

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
- **"No edX export found"** — Put the `.tar.gz` in `course_exports/` and run `python start_run.py`. Or pass `--tar <file>`, or set `EDX_TAR_PATH`.
- **"N course archives ... and none was named"** — Two exports in one folder. Keep one, or name it: `--tar "<file>"`. It will not choose by date: OneDrive rewrites modification times on sync, so the newest file is not the newest export.
- **"course_structure.json records no source archive"** — The structure predates provenance tracking, or came from a different export. Re-run `python extract_edx.py --tar "<file>"`. `--allow-stale` overrides, leaving provenance unverified.
- **"This looks like an edXUpdater import archive"** — See "edXUpdater is a different tool" above.
- **"course_structure.json not found"** — Run `extract_edx.py` (or `start_run.py`) first.
- **`OMP: Error #15` / the process dies during transcription** — Two OpenMP runtimes. `transcribe_videos.py` sets `KMP_DUPLICATE_LIB_OK=TRUE` for its own run; if it still aborts, `set KMP_DUPLICATE_LIB_OK=TRUE` in the shell first.
- **`ModuleNotFoundError` from a tool** — Run `python preflight.py`; it names every optional package and its `pip install`.
- **Uploads fail or wrong account** — Use Chrome with remote debugging and the correct Google account.
- **Large files skipped** — Check `MAX_UPLOAD_SIZE_MB` / `ENFORCE_UPLOAD_SIZE_LIMIT` in `config.py` (or env vars).
- **Duplicates remain after delete** — run `python delete_agent.py --dry-run` first and confirm the planned names. The matcher requires an **exact** normalised title by default; if a name misses, copy the exact source title from the notebook into `comparison_review.json`. `--fuzzy` exists but is unsafe (see the command reference).
- **The module PDF is not where I expected** — `Organized_Course_Content/Module_<n>.pdf`, or wherever `--out-dir` points.
- **A flag seems to be ignored** — it is not; every script rejects unrecognised flags and
  exits 2. `python <script>.py --help` lists what it accepts. This used to be silent, and
  `delete_agent.py --dryrun` therefore deleted for real.
- **Pre-merge check** — run `python tools/check_conflict_markers.py` before commit/PR to ensure no `<<<<<<<`, `=======`, `>>>>>>>` markers remain.
