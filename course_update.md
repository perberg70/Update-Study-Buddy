---
description: Processes a new edX course .tar.gz export, compares with current NotebookLM sources, removes old sources, and uploads new ones.
---

**Authentication:** when driving NotebookLM through a browser agent, use the Google
account that owns the notebook. If an account chooser appears, pick that one explicitly
rather than defaulting to whichever profile is first.

## Start here, every run

```
python start_run.py
```

It reports where the course export goes (`course_exports/`), finds it, refuses to guess
between two, records which one it read, and prints the commands to run next.

## The pipeline

`python run_full_update.py` with Chrome started as
`chrome.exe --remote-debugging-port=9222`:

- `export_current_sources.py` — scrapes the notebook's Sources panel over CDP into
  `current_sources.json`. It fails loudly rather than writing a list it cannot vouch for.
- `extract_edx.py` — parses the `.tar.gz` into `course_structure.json`, recording which
  archive it read. **It does not unpack anything**; every later step reads the archive
  directly, so two exports cannot blend.
- `organize_content.py` — downloads videos, converts to MP3, structures text by chapter.
- `compare_sources.py` — matches new files against current sources and writes
  `comparison_review.json` with a suggested action per row. Requires
  `current_sources.json`.
- `delete_agent.py` — removes sources marked for deletion. Exact-title matching by
  default; `--fuzzy` is opt-in and unsafe.
- `upload_agent.py` — uploads the new sources.

A review pause sits between the two halves: `comparison_review.json` is yours to edit,
and the concrete delete list is printed before anything is applied.

## Module PDFs

Separate from the pipeline above, and the direction this is heading:

```
python tools/build_module_pdf.py --module 1
```

One structured PDF per module — units and subunits as headings, prose and video
transcripts inline — instead of many fragment sources. See `README.md`.

## Notes

- Upload size is configurable via `MAX_UPLOAD_SIZE_MB` and `ENFORCE_UPLOAD_SIZE_LIMIT`.
- Deduplication by title is retired: identical titles do not imply identical content.
  See "Deduplication is retired" in `README.md`.
