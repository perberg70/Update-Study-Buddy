"""Did an upload actually arrive in the notebook? Pure logic, no browser.

upload_agent.py used to call set_files(), wait three seconds and report success.
Nothing looked at the notebook afterwards, and compare_sources.py --apply deletes
the sources being replaced on the strength of that report - so a slow transfer,
a rejected file or a failed import could authorise deleting the only copy.

This module decides, from what the Sources panel shows, whether each upload
arrived and finished processing. It is kept free of Playwright so every decision
can be tested on scripted panel states.

The rule that shapes it: only DOM facts already proven on the real notebook
count as success. export_current_sources.py read all 144 sources through these
row selectors and the aria-description title, and the checkbox count there was
exactly rows + 1 ("select all") - so a finished source row carries a checkbox.
A row with no checkbox, a disabled one, or a spinner is not finished. Spinners,
error icons and toasts are guesses about a UI that cannot be observed from the
development environment, so they are only ever used to FAIL an upload, never to
pass one. Anything unconfirmed is a failure: a wrongly failed upload costs a
re-run, a wrongly passed one can cost a source.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter

from export_current_sources import is_source_name

# Same row selectors, in the same order, as export_current_sources.SCRAPE_JS -
# tests/test_upload_verify.py checks they have not drifted apart.
PANEL_JS = """() => {
    const rowSelectors = [
        'div.single-source-container',   // Gemini Notebook (current)
        '[role="listitem"]',             // legacy NotebookLM
        'mat-list-item, .mat-mdc-list-item',
    ];
    let rows = [];
    let usedSelector = null;
    for (const sel of rowSelectors) {
        const found = document.querySelectorAll(sel);
        if (found.length) { rows = Array.from(found); usedSelector = sel; break; }
    }
    const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const busy = '[role="progressbar"], mat-progress-spinner, mat-spinner, '
               + '.mat-mdc-progress-spinner, .mat-progress-spinner, [aria-busy="true"]';
    const entries = rows.map(row => {
        const btn = row.querySelector('button[aria-description]');
        const col = row.querySelector('.source-title-column');
        const box = row.querySelector('input[type="checkbox"], [role="checkbox"]');
        return {
            aria: clean(btn ? btn.getAttribute('aria-description') : ''),
            title: clean(col ? (col.innerText || col.textContent) : ''),
            rowText: clean(row.innerText || row.textContent).slice(0, 300),
            hasCheckbox: !!box,
            checkboxDisabled: !!box && (box.disabled === true
                || box.hasAttribute('disabled')
                || box.getAttribute('aria-disabled') === 'true'),
            busy: !!row.querySelector(busy) || row.getAttribute('aria-busy') === 'true',
        };
    });
    const alerts = Array.from(document.querySelectorAll(
        '[role="alert"], mat-snack-bar-container, .mat-mdc-snack-bar-container, simple-snack-bar'
    )).map(e => clean(e.innerText || e.textContent)).filter(Boolean);
    return { usedSelector, rowCount: rows.length, entries, alerts };
}"""

# Words a row's status or a toast uses when something went wrong, English and
# Swedish. Matched against the row text with the title removed, so a source
# called "Error analysis in AI" is not mistaken for a failure.
ERROR_RE = re.compile(
    r"\b(error|failed|failure|couldn['’]?t|could not|cannot|can['’]?t|unable|"
    r"not supported|unsupported|rejected|misslyckad\w*|kunde inte|kan inte|"
    r"stöds inte|fel)\b", re.I)

READY, PROCESSING, ERROR = "ready", "processing", "error"
VERIFIED, UNCONFIRMED, FAILED = "verified", "unconfirmed", "failed"


def canonical(text):
    return " ".join(re.sub(r"[^a-z0-9åäö]+", " ", (text or "").lower()).split())


# --------------------------------------------------------------------------
# One reading of the panel
# --------------------------------------------------------------------------

class Panel:
    """The Sources panel at one moment: its source rows and any alerts."""

    def __init__(self, report):
        self.report = report or {}
        self.rows = []
        for entry in self.report.get("entries") or []:
            name = entry.get("aria") or entry.get("title") or entry.get("rowText") or ""
            if is_source_name(name):
                self.rows.append(dict(entry, name=name.strip()))
        self.alerts = [a for a in self.report.get("alerts") or [] if a]

    def names(self):
        return Counter(row["name"] for row in self.rows)

    def states(self, title):
        return [row_state(row) for row in self.rows if row["name"] == title]

    def status_of(self, title, state):
        for row in self.rows:
            if row["name"] == title and row_state(row) == state:
                return status_text(row)
        return ""


def status_text(row):
    """What a row shows besides its title - icon names, progress, error text."""
    text = row.get("rowText") or ""
    for title in (row.get("aria"), row.get("title")):
        if title:
            text = text.replace(title, " ")
    return " ".join(text.split())


def row_state(row):
    if ERROR_RE.search(status_text(row)):
        return ERROR
    # The proven signal: a finished row has an enabled checkbox and no spinner.
    if row.get("hasCheckbox") and not row.get("checkboxDisabled") and not row.get("busy"):
        return READY
    return PROCESSING


def new_titles(before, after):
    """Titles with more rows now than before, once per extra row. Counting,
    not set difference: a same-title REPLACE's new copy shares the old title."""
    return list((after.names() - before.names()).elements())


def new_alerts(before, after):
    """Error alerts that were not already showing at *before*."""
    fresh = Counter(after.alerts) - Counter(before.alerts)
    return [a for a in fresh.elements() if ERROR_RE.search(a)]


def appeared(before, after, file_name):
    """(title, "") for the upload's row, (None, "") if not there yet, or
    (None, why) when it cannot be told apart from another new row."""
    fresh = sorted(set(new_titles(before, after)))
    if not fresh:
        return None, ""
    stem = canonical(os.path.splitext(file_name)[0])
    matches = [t for t in fresh if stem and stem in canonical(t)]
    if len(fresh) == 1 and not matches:
        return None, f"one row appeared, but its title ({fresh[0][:40]}) does not look like the filename"
    if len(matches) == 1:
        return matches[0], ""
    return None, (f"cannot tell which new source is this upload: {len(fresh)} appeared "
                  f"({'; '.join(t[:40] for t in fresh[:3])})")


# --------------------------------------------------------------------------
# Waiting
# --------------------------------------------------------------------------

def poll(read, check, timeout, interval=2.0, clock=time.monotonic, sleep=time.sleep):
    """read() until check(panel) returns something other than None, or time runs
    out. Returns (result or None, the last panel read)."""
    deadline = clock() + timeout
    while True:
        panel = read()
        result = check(panel)
        if result is not None or clock() >= deadline:
            return result, panel
        sleep(interval)


def stable_read(read, timeout=20.0, interval=1.0, clock=time.monotonic, sleep=time.sleep):
    """A reading taken once two in a row agree.

    The baseline everything is compared against must be the whole panel. Taken
    while a long list is still rendering, a row that renders a moment later
    looks new - and if it is the only new row while an upload is awaited, it
    would be taken for that upload and could be "verified" in its place.
    """
    previous = read()
    deadline = clock() + timeout
    while clock() < deadline:
        sleep(interval)
        current = read()
        if current.names() == previous.names():
            return current
        previous = current
    return previous


def arrival_check(before, file_name):
    """check() for phase A: has this upload's row appeared, or has it failed?"""

    def check(now):
        alerts = new_alerts(before, now)
        if alerts:
            return FAILED, None, f"the notebook reported: {alerts[0][:160]}"
        title, why = appeared(before, now, file_name)
        if why:
            return FAILED, None, why
        if title is None:
            return None
        if now.states(title).count(ERROR) > before.states(title).count(ERROR):
            return FAILED, title, f"'{title[:60]}' shows an error: {now.status_of(title, ERROR)[:120]}"
        return "appeared", title, ""

    return check


def arrival_timeout(size_bytes, base=60.0, per_mb=15.0, cap=600.0):
    """How long a file may take to show up: longer for bigger transfers."""
    return min(cap, base + per_mb * size_bytes / (1024 * 1024))


def settle_check(baseline, uploads, outcomes):
    """check() for phase B: every appeared upload ready, or failed.

    *uploads*: dicts with "title" and "copies" - how many rows carried that
    title right after it appeared. Readiness asks for that many READY rows, so
    in a same-title REPLACE the old copy, already ready, cannot stand in for
    the new one. Decided entries are written into *outcomes* by index.
    """

    def check(now):
        pending = False
        for index, upload in enumerate(uploads):
            if index in outcomes:
                continue
            title = upload["title"]
            states = now.states(title)
            if states.count(ERROR) > baseline.states(title).count(ERROR):
                outcomes[index] = (FAILED, "processing failed: "
                                   + (now.status_of(title, ERROR)[:120] or "error shown"))
            elif states.count(READY) >= upload["copies"]:
                outcomes[index] = (VERIFIED, "")
            else:
                pending = True
        return None if pending else True

    return check


def unsettled(now, uploads, outcomes, minutes):
    """Final outcome for uploads still undecided at the phase B deadline."""
    for index, upload in enumerate(uploads):
        if index in outcomes:
            continue
        if len(now.states(upload["title"])) < upload["copies"]:
            outcomes[index] = (FAILED, "no longer in the Sources panel")
        else:
            outcomes[index] = (UNCONFIRMED, f"still processing after {minutes:g} min - "
                                            "re-run to check again; it will not be "
                                            "uploaded twice")
    return outcomes


def still_there(now, upload):
    """The final re-read: a verified upload must still be present and ready."""
    return now.states(upload["title"]).count(READY) >= upload["copies"]


# --------------------------------------------------------------------------
# What earlier runs established
# --------------------------------------------------------------------------

def file_key(path):
    """Identity of a file on disk: path, size and modification time."""
    stat = os.stat(path)
    return f"{os.path.abspath(path)}|{stat.st_size}|{int(stat.st_mtime)}"


def load_results(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def record_result(path, results, key, **fields):
    """Update one file's entry and save at once, so an interrupted run still
    remembers what it already put in the notebook."""
    entry = dict(results.get(key) or {})
    entry.update(fields, at=time.strftime("%Y-%m-%d %H:%M:%S"))
    results[key] = entry
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2, ensure_ascii=False, sort_keys=True)
    except OSError as exc:
        print(f"   [WARN] Could not save {path}: {exc}")


def prior_decision(results, key, panel, notebook):
    """What to do with a file an earlier run may already have uploaded.

    ("skip", entry)    verified before, and its title is still in the notebook;
    ("promote", entry) it arrived before and is still there, without an error -
                       waited on again rather than uploaded again, so re-running
                       while a long recording is still processing does not
                       upload it twice;
    ("upload", None)   anything else: never recorded, a changed file, gone from
                       the notebook, or showing an error.

    *copies* guards a same-title REPLACE: the old copy alone does not count as
    the new one having arrived.
    """
    entry = results.get(key)
    if not isinstance(entry, dict) or entry.get("notebook") != notebook:
        return "upload", None
    title, copies = entry.get("title"), entry.get("copies") or 1
    if not title:
        return "upload", None
    states = panel.states(title)
    if entry.get("status") == VERIFIED and states.count(READY) >= copies and ERROR not in states:
        return "skip", entry
    if entry.get("status") in (UNCONFIRMED, "appeared") \
            and len(states) >= copies and ERROR not in states:
        return "promote", entry
    return "upload", None


def debug_entry(reason, name, **panels):
    """Everything needed to see why one verification failed."""
    entry = {"reason": reason, "file": name}
    for label, panel in panels.items():
        if panel is None:
            continue
        entry[label] = {
            "usedSelector": panel.report.get("usedSelector"),
            "rowCount": panel.report.get("rowCount"),
            "alerts": panel.alerts,
            "rows": [dict(name=row["name"], state=row_state(row),
                          status=status_text(row),
                          hasCheckbox=row.get("hasCheckbox"),
                          checkboxDisabled=row.get("checkboxDisabled"),
                          busy=row.get("busy")) for row in panel.rows],
        }
    return entry


def save_debug(path, entries):
    """One file for the whole run, so no failure's evidence overwrites another's."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2, ensure_ascii=False)
        return True
    except OSError:
        return False
