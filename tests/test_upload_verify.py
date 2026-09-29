"""An upload counts only when the notebook shows it arrived and finished processing.

compare_sources.py --apply deletes the sources being replaced only when
upload_agent.py exits 0. That exit used to follow a fixed three-second wait, so
a slow transfer or a rejected file could authorise deleting the only copy. These
tests drive the real verification code on scripted Sources-panel states, and
upload_agent.run_upload() end to end against a fake page.

Run: python tests/test_upload_verify.py
"""

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import upload_verify as uv  # noqa: E402


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


# -- panel states -----------------------------------------------------------------

def entry(name, state):
    """One row as PANEL_JS reports it. Only ready rows carry an enabled checkbox."""
    return {
        "ready": dict(aria=name, title=name, rowText=f"description {name} more_vert",
                      hasCheckbox=True, checkboxDisabled=False, busy=False),
        "processing": dict(aria=name, title=name, rowText=f"{name}",
                           hasCheckbox=False, checkboxDisabled=False, busy=True),
        "disabled": dict(aria=name, title=name, rowText=f"description {name}",
                         hasCheckbox=True, checkboxDisabled=True, busy=False),
        "error": dict(aria=name, title=name, rowText=f"error {name} Couldn't import this file",
                      hasCheckbox=False, checkboxDisabled=False, busy=False),
    }[state]


def panel(*rows, alerts=()):
    entries = [entry(name, state) for name, state in rows]
    return uv.Panel({"usedSelector": "div.single-source-container",
                     "rowCount": len(entries), "entries": entries, "alerts": list(alerts)})


def scripted(*panels):
    """read() returning each panel in turn, then the last one forever, and a clock
    that only moves when poll() sleeps - so timeouts cost no real time."""
    queue, now = list(panels), [0.0]

    def read():
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += max(seconds, 1.0)

    return read, clock, sleep


def arrive(before, file_name, *after, timeout=10):
    read, clock, sleep = scripted(*after)
    return uv.poll(read, uv.arrival_check(before, file_name), timeout,
                   clock=clock, sleep=sleep)


# -- unit cases ---------------------------------------------------------------------

def unit_cases():
    out = []

    # Row states: only the proven signal passes; the title never decides error.
    check(out, uv.row_state(entry("a.txt", "ready")) == uv.READY, "ready row")
    check(out, uv.row_state(entry("a.txt", "processing")) == uv.PROCESSING, "spinner row")
    check(out, uv.row_state(entry("a.txt", "disabled")) == uv.PROCESSING,
          "a disabled checkbox is not finished")
    check(out, uv.row_state(entry("a.txt", "error")) == uv.ERROR, "error row")
    check(out, uv.row_state(entry("Error analysis in AI.txt", "ready")) == uv.READY,
          "a title containing 'Error' must not read as a failure")
    swedish = dict(entry("b.txt", "processing"), rowText="b.txt Uppladdningen misslyckades")
    check(out, uv.row_state(swedish) == uv.ERROR, "Swedish failure text is an error")
    check(out, not panel(("add Add sources", "ready")).rows,
          "UI labels are not sources")

    # The row selectors must not drift from the export's, which were proven.
    from export_current_sources import SCRAPE_JS
    selectors = lambda js: re.findall(r"^\s*'([^']+)',", js, re.M)
    check(out, selectors(uv.PANEL_JS) == selectors(SCRAPE_JS) and selectors(SCRAPE_JS),
          f"row selectors drifted: {selectors(uv.PANEL_JS)} vs {selectors(SCRAPE_JS)}")

    base = panel(("Old.txt", "ready"))

    # Arrival: processing is enough for phase A; the row's title is recorded.
    result, _ = arrive(base, "a.txt", base, panel(("Old.txt", "ready"), ("a.txt", "processing")))
    check(out, result and result[:2] == ("appeared", "a.txt"), f"arrival: {result}")

    result, _ = arrive(base, "a.txt", panel(("Old.txt", "ready"), ("a.txt", "error")))
    check(out, result and result[0] == uv.FAILED and "Couldn't import" in result[2],
          f"an error row fails the upload with its text: {result}")

    result, _ = arrive(base, "a.txt",
                       panel(("Old.txt", "ready"), alerts=["Upload failed: file too large"]))
    check(out, result and result[0] == uv.FAILED and "too large" in result[2],
          f"an error toast fails the upload: {result}")

    stale = panel(("Old.txt", "ready"), alerts=["Upload failed earlier"])
    result, _ = arrive(stale, "a.txt", stale, panel(("Old.txt", "ready"), ("a.txt", "ready"),
                                                    alerts=["Upload failed earlier"]))
    check(out, result and result[0] == "appeared",
          "a toast already showing before the upload is not this upload's failure")

    result, last = arrive(base, "a.txt", base, timeout=10)
    check(out, result is None, f"never appearing times out as None: {result}")

    result, _ = arrive(base, "Module_1.pdf",
                       panel(("Old.txt", "ready"), ("Module 1: Welcome", "processing")))
    check(out, result and result[1] == "Module 1: Welcome",
          f"a PDF titled from its metadata is still this upload: {result}")

    two = panel(("Old.txt", "ready"), ("a.txt", "processing"), ("Other thing", "ready"))
    result, _ = arrive(base, "a.txt", two)
    check(out, result and result[1] == "a.txt", f"two new rows, the stem decides: {result}")
    result, _ = arrive(base, "zzz.txt", two)
    check(out, result and result[0] == uv.FAILED and "cannot tell" in result[2],
          f"two new rows, neither ours by name: ambiguous is a failure: {result}")

    # Same-title REPLACE: the old copy shares the title.
    same_before = panel(("01_Welcome.txt", "ready"))
    same_after = panel(("01_Welcome.txt", "ready"), ("01_Welcome.txt", "processing"))
    result, now = arrive(same_before, "01_Welcome.txt", same_after)
    check(out, result and result[1] == "01_Welcome.txt"
          and len(now.states("01_Welcome.txt")) == 2,
          f"the second copy is the arrival: {result}")
    uploads = [dict(title="01_Welcome.txt", copies=2)]
    outcomes = {}
    check_fn = uv.settle_check(same_before, uploads, outcomes)
    check(out, check_fn(same_after) is None and not outcomes,
          "the old ready copy must not stand in for the new one")
    check(out, check_fn(panel(("01_Welcome.txt", "ready"), ("01_Welcome.txt", "ready")))
          and outcomes[0][0] == uv.VERIFIED, "both copies ready: verified")

    # Phase B deadline: still processing is unconfirmed; gone is failed.
    outcomes = {}
    uploads = [dict(title="a.txt", copies=1), dict(title="b.txt", copies=1)]
    read, clock, sleep = scripted(panel(("a.txt", "processing")))
    _, now = uv.poll(read, uv.settle_check(base, uploads, outcomes), 30,
                     clock=clock, sleep=sleep)
    uv.unsettled(now, uploads, outcomes, 0.5)
    check(out, outcomes[0][0] == uv.UNCONFIRMED and outcomes[1][0] == uv.FAILED
          and "no longer" in outcomes[1][1], f"deadline outcomes: {outcomes}")

    outcomes = {}
    uv.settle_check(base, [dict(title="a.txt", copies=1)], outcomes)(
        panel(("a.txt", "error")))
    check(out, outcomes[0][0] == uv.FAILED, "an error during processing fails it")

    # The baseline waits for the panel to stop changing: a row that renders late
    # must not look like a new upload.
    read, clock, sleep = scripted(panel(("A", "ready")),
                                  panel(("A", "ready"), ("Late", "ready")),
                                  panel(("A", "ready"), ("Late", "ready")))
    settled = uv.stable_read(read, clock=clock, sleep=sleep)
    check(out, "Late" in settled.names(), "the baseline includes rows that render late")

    # The final re-read.
    up = dict(title="a.txt", copies=1)
    check(out, uv.still_there(panel(("a.txt", "ready")), up), "still there")
    check(out, not uv.still_there(panel(("b.txt", "ready")), up),
          "gone after processing is caught")

    # What earlier runs established.
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "a.txt")
        with open(f, "w") as fh:
            fh.write("x")
        key, store = uv.file_key(f), os.path.join(tmp, "r.json")
        results = {}
        uv.record_result(store, results, key, status=uv.VERIFIED, title="a.txt",
                         copies=1, notebook="nb")
        check(out, uv.load_results(store).get(key, {}).get("status") == uv.VERIFIED,
              "results are saved at once, not at the end of the run")

        decide = lambda r, p, nb="nb": uv.prior_decision(r, key, p, nb)[0]
        check(out, decide(results, panel(("a.txt", "ready"))) == "skip",
              "verified and still present: not uploaded again")
        check(out, decide(results, panel(("b.txt", "ready"))) == "upload",
              "verified but gone from the notebook: upload again")
        check(out, decide(results, panel(("a.txt", "ready")), "other") == "upload",
              "a different notebook: upload")

        pending = {key: dict(status="appeared", title="a.txt", copies=1, notebook="nb")}
        check(out, decide(pending, panel(("a.txt", "processing"))) == "promote",
              "arrived and still processing: wait on it, do not send it twice")
        check(out, decide(pending, panel(("a.txt", "error"))) == "upload",
              "arrived but errored: send it again")
        replace = {key: dict(status=uv.UNCONFIRMED, title="a.txt", copies=2, notebook="nb")}
        check(out, decide(replace, panel(("a.txt", "ready"))) == "upload",
              "same-title REPLACE whose new copy is gone: the old copy is not it")

        with open(f, "w") as fh:
            fh.write("changed")
        os.utime(f, (1, 1))
        check(out, uv.prior_decision(results, uv.file_key(f), panel(("a.txt", "ready")),
                                     "nb")[0] == "upload",
              "a changed file is uploaded again")
    return out


# -- run_upload end to end, against a fake page ---------------------------------------

class FakeLocator:
    def __init__(self, page, name):
        self.page, self.name = page, name

    @property
    def first(self):
        return self

    def wait_for(self, **_kw):
        pass

    def click(self, **_kw):
        pass


class FakeChooser:
    def __init__(self, page):
        self.page = page

    def set_files(self, path):
        self.page.receive(path)


class FakeExpect:
    def __init__(self, page):
        self.value = FakeChooser(page)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakePage:
    """A Sources panel that reacts to uploads the way *behaviour* says:
    ("ok", reads-until-ready[, title]), ("error",), ("never",), ("toast",),
    ("slow",) stays processing, ("vanish", reads) disappears, ("flash", reads)
    finishes and then disappears - accepted, then dropped."""

    def __init__(self, rows=(), behaviour=None):
        self.rows = [dict(name=n, state=s) for n, s in rows]
        self.behaviour = behaviour or {}
        self.pending, self.alerts, self.uploads = [], [], []
        self.keyboard = types.SimpleNamespace(press=lambda _k: None)

    def goto(self, *_a, **_k):
        pass

    def wait_for_load_state(self, *_a, **_k):
        pass

    def wait_for_timeout(self, _ms):
        pass

    def get_by_role(self, _role, name=None):
        return FakeLocator(self, name)

    def expect_file_chooser(self):
        return FakeExpect(self)

    def receive(self, path):
        name = os.path.basename(path)
        self.uploads.append(name)
        how = self.behaviour.get(name, ("ok", 2))
        if how[0] == "never":
            return
        if how[0] == "toast":
            self.alerts.append("Upload failed: this file could not be imported")
            return
        row = dict(name=how[2] if len(how) > 2 else name, state="processing")
        self.rows.append(row)
        if how[0] == "error":
            row["state"] = "error"
        elif how[0] == "ok":
            self.pending.append([row, how[1], "ready"])
        elif how[0] == "vanish":
            self.pending.append([row, how[1], "gone"])
        elif how[0] == "flash":
            self.pending.append([row, how[1], "ready"])
            self.pending.append([row, how[1] + 1, "gone"])

    def evaluate(self, _js):
        for item in list(self.pending):
            item[1] -= 1
            if item[1] <= 0:
                if item[2] == "gone":
                    self.rows.remove(item[0])
                else:
                    item[0]["state"] = item[2]
                self.pending.remove(item)
        entries = [entry(r["name"], r["state"]) for r in self.rows]
        return {"usedSelector": "div.single-source-container", "rowCount": len(entries),
                "entries": entries, "alerts": list(self.alerts)}


def run(agent, page, names, only=None, wait_minutes=0.02):
    """run_upload() against *page*, with a review file listing *names* as ADD."""
    review = {"pairs": [], "current_only": [],
              "new_only": [dict(name=n, path=os.path.join(agent._tmp, n), type="text",
                                action="ADD", chapter="1") for n in names]}
    with open(agent.REVIEW_PATH, "w", encoding="utf-8") as fh:
        json.dump(review, fh)
    agent.connect = lambda _p: (None, page)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = agent.run_upload(only=only, wait_minutes=wait_minutes)
    return code, buf.getvalue()


def end_to_end():
    out = []
    # The browser stack is not installed in CI; run_upload only needs the name.
    fake = types.ModuleType("playwright.sync_api")
    fake.sync_playwright = lambda: contextlib.nullcontext(object())
    sys.modules.setdefault("playwright", types.ModuleType("playwright"))
    sys.modules["playwright.sync_api"] = fake

    import upload_agent as agent

    with tempfile.TemporaryDirectory() as tmp:
        agent._tmp = tmp
        agent.REVIEW_PATH = os.path.join(tmp, "review.json")
        agent.UPLOAD_RESULTS_PATH = os.path.join(tmp, "upload_results.json")
        agent.UPLOAD_DEBUG_PATH = os.path.join(tmp, "upload_verify_debug.json")
        agent.describe_page = lambda _p: "fake"
        agent.POLL_INTERVAL = 0
        agent.ARRIVAL_BASE_SECONDS = 0.2
        agent.ARRIVAL_PER_MB_SECONDS = 0
        names = ["ok.txt", "err.txt", "lost.txt", "toast.txt", "slow.txt", "01_Welcome.txt"]
        for name in names:
            with open(os.path.join(tmp, name), "w") as fh:
                fh.write(name)

        # 1. Everything arrives and finishes: exit 0, all recorded as verified.
        page = FakePage([("Old.txt", "ready")])
        code, text = run(agent, page, ["ok.txt"])
        results = json.load(open(agent.UPLOAD_RESULTS_PATH))
        check(out, code == 0 and "1 verified" in text, f"clean run should exit 0:\n{text}")
        check(out, [r["status"] for r in results.values()] == [uv.VERIFIED],
              f"recorded as verified: {results}")

        # 2. A run with every kind of failure: exit 1, each reason reported,
        #    and the debug file written.
        os.remove(agent.UPLOAD_RESULTS_PATH)
        page = FakePage([("Old.txt", "ready")],
                        {"err.txt": ("error",), "lost.txt": ("never",),
                         "toast.txt": ("toast",), "slow.txt": ("slow",)})
        code, text = run(agent, page, ["ok.txt", "err.txt", "lost.txt", "toast.txt",
                                       "slow.txt"])
        check(out, code == 1, f"any unverified upload must exit 1:\n{text}")
        for needle in ["1 verified", "1 still processing", "3 failed",
                       "never appeared", "could not be imported", "Couldn't import",
                       "still processing after"]:
            check(out, needle in text, f"run output should say {needle!r}:\n{text}")
        check(out, os.path.exists(agent.UPLOAD_DEBUG_PATH), "debug file written")
        check(out, "wait_for_timeout(3000)" not in open(agent.__file__).read(),
              "the fixed three-second wait is gone")

        # 3. Re-run: the verified file is not sent again, the one still processing
        #    is waited on rather than re-sent, the failures are retried.
        for row in page.rows:
            if row["name"] == "slow.txt":
                row["state"] = "ready"          # it finished in the meantime
        page.rows = [r for r in page.rows if r["name"] != "err.txt"]
        page.behaviour = {}
        page.alerts, page.uploads = [], []
        code, text = run(agent, page, ["ok.txt", "err.txt", "lost.txt", "toast.txt",
                                       "slow.txt"])
        check(out, sorted(page.uploads) == ["err.txt", "lost.txt", "toast.txt"],
              f"only the failures are re-sent, got {page.uploads}")
        check(out, code == 0 and "already in the notebook" in text,
              f"everything verified on the re-run should exit 0:\n{text}")

        # 4. Same-title REPLACE: the notebook already has a ready "01_Welcome.txt".
        os.remove(agent.UPLOAD_RESULTS_PATH)
        page = FakePage([("01_Welcome.txt", "ready")], {"01_Welcome.txt": ("slow",)})
        code, text = run(agent, page, ["01_Welcome.txt"])
        check(out, code == 1, "the old copy must not verify a new one still processing:\n"
              + text)
        os.remove(agent.UPLOAD_RESULTS_PATH)
        page = FakePage([("01_Welcome.txt", "ready")], {"01_Welcome.txt": ("ok", 3)})
        code, text = run(agent, page, ["01_Welcome.txt"])
        results = json.load(open(agent.UPLOAD_RESULTS_PATH))
        check(out, code == 0 and list(results.values())[0].get("copies") == 2,
              f"verified once the second copy is ready: {results}\n{text}")

        # 5. An arrival that then disappears is a failure.
        os.remove(agent.UPLOAD_RESULTS_PATH)
        page = FakePage([], {"ok.txt": ("vanish", 2)})
        code, text = run(agent, page, ["ok.txt"])
        check(out, code == 1 and "no longer in the Sources panel" in text,
              f"a row that vanishes fails:\n{text}")

        # ...and so is one that finishes processing, then is dropped: only the
        # final re-read after phase B can see that.
        os.remove(agent.UPLOAD_RESULTS_PATH)
        page = FakePage([], {"ok.txt": ("flash", 3)})
        code, text = run(agent, page, ["ok.txt"])
        check(out, code == 1 and "gone from the Sources panel after processing" in text,
              f"accepted then dropped must fail:\n{text}")

        # 6. --only: a supervised single-file run.
        os.remove(agent.UPLOAD_RESULTS_PATH)
        page = FakePage([])
        code, text = run(agent, page, ["ok.txt", "slow.txt"], only="ok")
        check(out, page.uploads == ["ok.txt"] and code == 0, f"--only ok: {page.uploads}")
        code, text = run(agent, FakePage([]), ["ok.txt"], only="nothing-like-it")
        check(out, code == 1 and "No planned upload" in text, "--only with no match fails")
    return out


def apply_gate():
    """compare_sources --apply deletes nothing unless the upload step exits 0."""
    out = []
    import compare_sources as cs
    with tempfile.TemporaryDirectory() as tmp:
        new = os.path.join(tmp, "new.txt")
        with open(new, "w") as fh:
            fh.write("x")
        cs.REVIEW_PATH = os.path.join(tmp, "review.json")
        with open(cs.REVIEW_PATH, "w") as fh:
            json.dump({"pairs": [dict(new_name="new.txt", new_path=new, new_type="text",
                                      old_name="Old source", action="REPLACE")],
                       "new_only": [], "current_only": []}, fh)
        for upload_exit, deletes in [(1, False), (0, True)]:
            calls = []

            def fake_run(cmd, check=False, _code=upload_exit):
                calls.append(os.path.basename(cmd[-1]))
                return types.SimpleNamespace(returncode=_code if "upload" in cmd[-1] else 0)

            cs.subprocess.run, saved = fake_run, cs.subprocess.run
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    try:
                        cs.apply_review()
                    except SystemExit:
                        pass
            finally:
                cs.subprocess.run = saved
            check(out, ("delete_agent.py" in calls) == deletes,
                  f"upload exit {upload_exit}: delete ran = {'delete_agent.py' in calls}")
    return out


def main():
    failures = unit_cases() + end_to_end() + apply_gate()
    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] ready only with the proven checkbox signal; errors from status,")
        print("         never from a title; toasts, ambiguity and silence all fail")
        print("  [PASS] same-title REPLACE needs the new copy ready, not the old one")
        print("  [PASS] re-runs skip verified uploads and wait on ones still processing")
        print("  [PASS] run_upload exits 0 only when every upload is verified")
        print("  [PASS] --apply deletes nothing after an unverified upload")
    return not failures


if __name__ == "__main__":
    print("upload verification")
    raise SystemExit(0 if main() else 1)
