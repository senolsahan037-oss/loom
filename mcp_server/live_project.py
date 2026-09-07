"""Opening, inspecting and closing Ableton Live projects.

Live's own scripting cannot open or close a project -- that is an operating
system level action -- so this module drives it from outside and then reads the
verdict out of Live's own log rather than assuming the open succeeded.

Two facts shape the design, both measured against Live 12.4.5 on 2026-09-02:

* Live does not watch the `.als` on disk. Editing a set that is already open
  changes nothing; "reload" means save and open again.
* Live's log is written to continuously by any active control surface and can
  reach hundreds of megabytes, so it is searched backwards in chunks rather
  than read whole or tailed by a fixed amount.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import time
from pathlib import Path

NOISE = re.compile(r"RemoteScriptMessage|_Framework|MemoryUsage|METER_")
CRASH_DIR = Path.home() / "Library" / "Logs" / "DiagnosticReports"
PREFS = Path.home() / "Library" / "Preferences" / "Ableton"


def installed_apps() -> list[str]:
    return sorted(os.path.basename(p) for p in glob.glob("/Applications/Ableton Live*.app"))


def default_app() -> str | None:
    """The Live that is running, else the newest installed one."""
    for app in installed_apps():
        if running(app):
            return app
    apps = installed_apps()
    return apps[-1] if apps else None


def running(app: str | None = None) -> list[str]:
    pattern = f"{app}/Contents/MacOS/Live" if app else "Ableton Live.*/Contents/MacOS/Live"
    out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout
    return [line for line in out.split() if line]


def log_path() -> Path | None:
    logs = list(PREFS.glob("Live */Log.txt"))
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def _reverse_find(path: Path, needle: str, max_mb: int = 256, chunk_mb: int = 8) -> str | None:
    size = path.stat().st_size
    step = chunk_mb * 1024 * 1024
    limit = min(size, max_mb * 1024 * 1024)
    read = 0
    with path.open("rb") as handle:
        while read < limit:
            read = min(read + step, limit)
            handle.seek(size - read)
            text = handle.read(read).decode("utf-8", "ignore")
            if needle in text:
                return text
    return None


# Live logs every document it loads, including its own defaults: the new-set
# template on File > New, and "Default Audio Track.als"-style files each time a
# track is created. Only a path outside the app bundle is a user's set.
TEMPLATE_MARK = "/Builtin/Templates/DefaultLiveSet.als"
APP_INTERNAL_MARK = "/Contents/App-Resources/"
_LOAD_LINE = re.compile(r'^(\S+): info: Loading document "([^"]+)"', re.MULTILINE)


def window_title(timeout: float = 5.0) -> str | None:
    """Live's front window title (the set name, or "Untitled"), through System
    Events. Needs the Accessibility permission of the process running this;
    None when it cannot be read, never a guess."""
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["osascript", "-e", 'tell application "System Events" to tell process "Live" to get name of front window'],
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    title = out.stdout.strip()
    return title or None


def open_set(title_reader=window_title) -> dict:
    """Which set is open in Live, and how that is known.

    The Extensions SDK exposes no set name or path (measured 2026-09-07:
    Song has tracks/tempo/scale, Environment has storage dirs). Two OS
    sources are read instead: Live's own log (the last "Loading document"
    that is not one of Live's internal files: a user path = a saved set,
    the new-set template = an unsaved set) and the window title (the set
    name Live shows). Each is reported with its value; `agreement` says
    whether they name the same set.
    """
    answer: dict = {"name": None, "path": None, "project_dir": None, "saved": None, "kind": "unknown",
                    "log_document": None, "loaded_at": None, "window_title": None, "agreement": None, "sources": []}
    log = log_path()
    if log:
        text = _reverse_find(log, 'Loading document "')
        hits = _LOAD_LINE.findall(text) if text else []
        for stamp, doc in reversed(hits):
            if TEMPLATE_MARK in doc:
                answer.update({"kind": "new_unsaved", "saved": False, "log_document": doc, "loaded_at": stamp})
                break
            if APP_INTERNAL_MARK in doc:
                continue  # a track default or similar, not a set
            answer.update({"kind": "saved_set", "saved": True, "path": doc, "project_dir": os.path.dirname(doc),
                           "name": os.path.splitext(os.path.basename(doc))[0], "log_document": doc, "loaded_at": stamp})
            break
        if answer["kind"] != "unknown":
            answer["sources"].append("live_log")
    title = title_reader() if title_reader else None
    if title:
        answer["window_title"] = title
        answer["sources"].append("window_title")
        if answer["kind"] == "saved_set":
            answer["agreement"] = title == answer["name"]
        elif answer["kind"] == "new_unsaved":
            answer["agreement"] = title.lower().startswith("untitled")
        else:
            answer["name"], answer["kind"] = title, "window_title_only"
    if answer["kind"] == "saved_set" and answer["agreement"] is False:
        answer["note"] = "the log names one set and the window another: the log may predate a File > New or a rename"
    return answer


def current_document() -> str | None:
    """The open set's path, or None for a new unsaved set (see open_set)."""
    return open_set(title_reader=None).get("path")


def log_marker() -> dict | None:
    """Where Live's log ends right now: identity (inode) and size. A report
    taken against this marker only sees lines written after it, so an earlier
    load of the same set in the same log is never mistaken for this one."""
    log = log_path()
    if not log:
        return None
    stat = log.stat()
    return {"path": str(log), "inode": stat.st_ino, "size": stat.st_size}


def _text_since(marker: dict | None) -> tuple[Path | None, str | None, str]:
    """(log, text written since the marker, how it was read). If the log was
    rotated or replaced since the marker, the whole new file is the answer."""
    log = log_path()
    if not log:
        return None, None, "no_log"
    stat = log.stat()
    if marker is None:
        return log, None, "whole_log"
    same_file = str(log) == marker["path"] and stat.st_ino == marker["inode"]
    if same_file and stat.st_size >= marker["size"]:
        with log.open("rb") as handle:
            handle.seek(marker["size"])
            return log, handle.read().decode("utf-8", "ignore"), "since_marker"
    # A different inode, a different file or a shrunk file: Live rotated its
    # log after the marker was taken. Everything in the new file is new.
    return log, log.read_text(encoding="utf-8", errors="ignore"), "rotated"


def load_report(als_path: str, since: dict | None = None) -> dict:
    """What Live's log says about loading this particular set -- only in the
    part of the log written after `since`, when a marker is given."""
    marker = f'Loading document "{os.path.abspath(als_path)}"'
    log, text, how = _text_since(since)
    if not log:
        return {"log_found": False}
    if text is None:
        text = _reverse_find(log, marker)
    if text is None or marker not in text:
        return {"log_found": True, "seen_in_log": False, "log_read": how}
    window = text[text.rfind(marker):]
    lines = [line for line in window.splitlines() if not NOISE.search(line)]
    return {
        "log_found": True,
        "seen_in_log": True,
        "log_read": how,
        "loaded": any("Loaded document was created by" in l for l in lines),
        "corrupt": [l.split("info: ")[-1] for l in lines if "corrupt" in l.lower()][:3],
        "repairs": sum(1 for l in lines if "Repair Track" in l),
        "unopenable_files": sorted({m for l in lines
                                    for m in re.findall(r'The file "([^"]+)" could not be opened', l)}),
    }


def recent_crashes(seconds: float) -> list[str]:
    now = time.time()
    return [p.name for p in CRASH_DIR.glob("Live*.ips")
            if now - p.stat().st_mtime <= seconds]


def status() -> dict:
    app = default_app()
    pids = running(app) if app else []
    return {
        "live_running": bool(pids),
        "pids": pids,
        "application": app,
        "installed": installed_apps(),
        "open_document": current_document() if pids else None,
        "open_set": open_set() if pids else None,
        "log": str(log_path()) if log_path() else None,
        "crashes_last_hour": recent_crashes(3600),
    }


def open_project(als_path: str, wait_seconds: float = 30, allow_switch: bool = False) -> dict:
    target = os.path.abspath(os.path.expanduser(als_path))
    if not os.path.exists(target):
        return {"opened": False, "error": f"file not found: {target}"}
    app = default_app()
    if not app:
        return {"opened": False, "error": "no Ableton Live found in /Applications"}

    already = running(app)
    if already and not allow_switch:
        return {
            "opened": False,
            "needs_allow_switch": True,
            "note": ("Live is already running. Opening another set may raise Live's own "
                     "unsaved-changes dialog, which only the person at the keyboard can "
                     "answer; nothing is ever discarded automatically. Also note that an "
                     "installed Extension is killed by a set switch (Extension Host crash "
                     "in the SDK's own document-change handling), while control surfaces "
                     "survive it. Pass allow_switch to proceed."),
        }

    # Identity, not counts: a crash report that was already there is not this
    # load's crash, and a log line from an earlier load of the same set is not
    # this load's line.
    crashes_before = set(recent_crashes(86400))
    marker = log_marker()
    subprocess.run(["open", "-a", app, target], check=True)
    time.sleep(wait_seconds)

    report = load_report(target, since=marker)
    new_crashes = sorted(set(recent_crashes(wait_seconds + 60)) - crashes_before)
    crashed = bool(new_crashes)
    opened = bool(report.get("loaded")) and not report.get("corrupt") and not crashed
    return {"opened": opened, "document": target, "application": app,
            "crashed_during_load": crashed, "new_crash_reports": new_crashes, **report}


def quit_live(wait_seconds: float = 8) -> dict:
    app = default_app()
    if not app or not running(app):
        return {"quit": True, "note": "Live was not running"}
    subprocess.run(["osascript", "-e", 'tell application "Live" to quit'],
                   capture_output=True)
    time.sleep(wait_seconds)
    still = running(app)
    return {"quit": not still,
            "note": ("Live is still running -- it is most likely waiting on its own "
                     "save dialog, which only the person at the keyboard can answer."
                     if still else "closed")}
