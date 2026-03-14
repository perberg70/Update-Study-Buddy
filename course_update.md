---
description: Autonomously processes a new edX course .tar.gz export, compares with current NotebookLM sources, removes old sources, and uploads new ones.
---

**Critical Authentication Mandate:** Whenever you use your Browser Agent to interact with NoteBookLM, you are strictly bound to using [perberg70@gmail.com]. If presented with a Google Account selection screen, you must explicitly locate and click that specific email address. Do NOT default to the university account or any other profile.

**Critical Authentication Mandate:** Whenever interacting with NoteBookLM, you are strictly bound to using [perberg70@gmail.com].

1. **Automation Pipeline**: Execute `python run_full_update.py` in the terminal (with Chrome started as `chrome.exe --remote-debugging-port=9222`). This master script coordinates:
   - `export_current_sources.py`: Connects via CDP, opens the notebook, scrapes the current source names from the sidebar, and writes `current_sources.json`. This keeps the list up to date at the start of every run.
   - `extract_edx.py`: Unpacks the `.tar.gz`.
   - `organize_content.py`: Downloads videos (fixing 403 errors), converts to MP3, and structures text by chapter.
   - `compare_sources.py`: Compares new sources (from `processing_manifest.json`) with current NotebookLM sources (from `current_sources.json`). Writes `comparison_review.json` with ADD (new) vs REPLACE (old name to remove). **Requires** `current_sources.json` (no built-in list).
   - `delete_agent.py`: Connects via CDP, opens the notebook, and removes each source marked REPLACE in the comparison review (More -> Remove source -> Delete).
   - `upload_agent.py`: Uploads all new sources to NoteBookLM (Upload files for documents/audio, Websites for URLs, Copied text for pasted content).

2. **current_sources.json**: Written by `export_current_sources.py` at the start of each full update. It is the single source of truth for "current notebook sources"; compare_sources.py requires it and no longer uses a built-in default list.

3. **Monitoring**: Watch the terminal output for progress.
4. **Verification**: Once complete, verify that old sources were removed and new text/audio sources are visible in the NoteBookLM UI.


- Upload size behavior is configurable via `MAX_UPLOAD_SIZE_MB` and `ENFORCE_UPLOAD_SIZE_LIMIT` (env vars or `config.py`).
