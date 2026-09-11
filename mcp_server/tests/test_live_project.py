#!/usr/bin/env python3
# © 2026 Şenol Şahan / SubverseLab · Loom · https://subverselab.com/loom
"""live_project.open_project judged by identity, not by counts.

Fake Live: a log file and a crash directory in a temporary tree, `open`
replaced by a function that appends what Live would have logged. What is
proven: an earlier load of the same set in the same log is not this load's
evidence; a crash report that was already there is not this load's crash;
a crash that appears during the load is; and a log Live rotated during the
open is read whole instead of from a stale offset.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("live_project_under_test", ROOT / "mcp_server" / "live_project.py")
lp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lp)

checks, failures = [], []


def check(label, condition, detail=""):
    (checks if condition else failures).append(label if condition else "%s  %s" % (label, detail))


base = Path(tempfile.mkdtemp(prefix="loom_live_project_"))
prefs = base / "Preferences" / "Ableton" / "Live 12.4.15b1"
prefs.mkdir(parents=True)
crashes = base / "DiagnosticReports"
crashes.mkdir()
log = prefs / "Log.txt"
als = base / "Set Project" / "Set.als"
als.parent.mkdir()
als.write_bytes(b"not really a set")
lp.PREFS = base / "Preferences" / "Ableton"
lp.CRASH_DIR = crashes
lp.default_app = lambda: "Fake Live.app"
lp.running = lambda app=None: []
lp.time.sleep = lambda _seconds: None

LOAD_LINES = 'info: Loading document "%s"\ninfo: Loaded document was created by Ableton Live 12.4\n'
ok_line = LOAD_LINES % os.path.abspath(als)


def fake_open(append: str | None = None, rotate_to: str | None = None, crash: str | None = None):
    def run(argv, check=False, **_kw):
        assert argv[:2] == ["open", "-a"], argv
        if rotate_to is not None:
            log.unlink()
            log.write_text(rotate_to, encoding="utf-8")
        elif append:
            with log.open("a", encoding="utf-8") as handle:
                handle.write(append)
        if crash:
            (crashes / crash).write_text("crash", encoding="utf-8")
    lp.subprocess.run = run


try:
    # 1) an earlier load of the same set, nothing new after the open
    log.write_text("info: boot\n" + ok_line + "info: idle\n", encoding="utf-8")
    fake_open(append="info: nothing happened\n")
    report = lp.open_project(str(als), wait_seconds=0)
    check("an earlier load of the same set in the log is not this load's evidence",
          report["opened"] is False and report["seen_in_log"] is False and report["log_read"] == "since_marker", report)

    # 2) the load really happens after the open
    fake_open(append=ok_line)
    report = lp.open_project(str(als), wait_seconds=0)
    check("a load logged after the open counts", report["opened"] is True and report["loaded"] is True, report)

    # 3) an old crash report does not count; a new one does
    (crashes / "Live-2026-09-04-old.ips").write_text("old", encoding="utf-8")
    fake_open(append=ok_line)
    report = lp.open_project(str(als), wait_seconds=0)
    check("a crash report that was already there is not this load's crash",
          report["crashed_during_load"] is False and report["new_crash_reports"] == [], report)
    fake_open(append=ok_line, crash="Live-2026-09-05-new.ips")
    report = lp.open_project(str(als), wait_seconds=0)
    check("a crash report that appears during the load is this load's crash",
          report["crashed_during_load"] is True and report["new_crash_reports"] == ["Live-2026-09-05-new.ips"] and report["opened"] is False, report)

    # 4) Live rotated its log during the open: the new, smaller file is read whole
    log.write_text("x" * 5000 + "\n", encoding="utf-8")
    fake_open(rotate_to="info: fresh log\n" + ok_line)
    report = lp.open_project(str(als), wait_seconds=0)
    check("a rotated log is read whole instead of from a stale offset",
          report["opened"] is True and report["log_read"] == "rotated", report)

    # 5) which set is open: the log's last real document, cross-checked with the window title
    template = "/Applications/Ableton Live 12 Beta.app/Contents/App-Resources/Builtin/Templates/DefaultLiveSet.als"
    track_default = "/Applications/Ableton Live 12 Beta.app/Contents/App-Resources/Core Library/Defaults/Creating Tracks/Audio Track/Default Audio Track.als"
    line = '2026-09-07T18:43:%02d.000000: info: Loading document "%s"\n'
    log.write_text(line % (17, template) + line % (28, os.path.abspath(als)) + line % (40, track_default) + line % (41, track_default), encoding="utf-8")
    seen = lp.open_set(title_reader=lambda: "Set")
    check("the open set is the last real document in the log, not Live's track defaults loaded after it; the window title agrees",
          seen["kind"] == "saved_set" and seen["name"] == "Set" and seen["path"] == os.path.abspath(als) and seen["saved"] is True
          and seen["loaded_at"].startswith("2026-09-07T18:43:28") and seen["agreement"] is True and seen["sources"] == ["live_log", "window_title"], seen)
    log.write_text(line % (17, os.path.abspath(als)) + line % (50, template) + line % (51, track_default), encoding="utf-8")
    seen = lp.open_set(title_reader=lambda: "Untitled")
    check("File > New after a set: the template is the last real load, so the set is new and unsaved, and 'Untitled' agrees",
          seen["kind"] == "new_unsaved" and seen["saved"] is False and seen["path"] is None and seen["agreement"] is True, seen)
    seen = lp.open_set(title_reader=lambda: "Diplomat")
    check("a window title that names another set is reported as disagreement, never resolved by guessing",
          seen["kind"] == "new_unsaved" and seen["window_title"] == "Diplomat" and seen["agreement"] is False, seen)
    seen = lp.open_set(title_reader=lambda: None)
    check("without a readable window title the log alone answers and says so", seen["sources"] == ["live_log"] and seen["agreement"] is None, seen)
    check("current_document is the same reading: None for an unsaved set", lp.current_document() is None, lp.current_document())

    # 6) no log at all is said, not guessed
    shutil.rmtree(prefs)
    fake_open()
    report = lp.open_project(str(als), wait_seconds=0)
    check("without a log the report says so", report["opened"] is False and report["log_found"] is False, report)
    seen = lp.open_set(title_reader=lambda: "Diplomat")
    check("without a log only the window title is known, and it is labelled as such", seen["kind"] == "window_title_only" and seen["name"] == "Diplomat" and seen["sources"] == ["window_title"], seen)
finally:
    shutil.rmtree(base, ignore_errors=True)

print("%d checks passed:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
if failures:
    print("FAILED:")
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("LIVE PROJECT OPEN VERIFICATION WORKS")
