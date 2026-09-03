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


def current_document() -> str | None:
    log = log_path()
    if not log:
        return None
    text = _reverse_find(log, 'Loading document "')
    if not text:
        return None
    hits = re.findall(r'Loading document "([^"]+)"', text)
    return hits[-1] if hits else None


def load_report(als_path: str) -> dict:
    """What Live's log says about loading this particular set."""
    log = log_path()
    if not log:
        return {"log_found": False}
    marker = f'Loading document "{os.path.abspath(als_path)}"'
    text = _reverse_find(log, marker)
    if text is None:
        return {"log_found": True, "seen_in_log": False}
    window = text[text.rfind(marker):]
    lines = [line for line in window.splitlines() if not NOISE.search(line)]
    return {
        "log_found": True,
        "seen_in_log": True,
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

    crashes_before = len(recent_crashes(86400))
    subprocess.run(["open", "-a", app, target], check=True)
    time.sleep(wait_seconds)

    report = load_report(target)
    crashed = len(recent_crashes(wait_seconds + 60)) > crashes_before
    opened = bool(report.get("loaded")) and not report.get("corrupt") and not crashed
    return {"opened": opened, "document": target, "application": app,
            "crashed_during_load": crashed, **report}


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
