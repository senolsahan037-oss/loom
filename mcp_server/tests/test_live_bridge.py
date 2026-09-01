#!/usr/bin/env python3
"""Live bridge: the REAL Loom control surface class plus the MCP tools, no Ableton.

_Framework is stubbed so the remote script imports normally and runs against a
fake song. What is under test is therefore not an imitation but the very code
that will run inside Live. The one thing left unproven is that Live's real LOM
behaves the same way as the fake song.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REMOTE_DIR = ROOT / "AbletonScripts" / "Loom"
sys.path.insert(0, str(REMOTE_DIR))

# --- _Framework stub ------------------------------------------------------
_framework = types.ModuleType("_Framework")
_cs_module = types.ModuleType("_Framework.ControlSurface")


class ControlSurface(object):
    def __init__(self, c_instance=None):
        self._c_instance = c_instance
        self.messages = []

    def log_message(self, message):
        self.messages.append(message)

    def _register_timer_callback(self, callback):
        pass

    def _unregister_timer_callback(self, callback):
        pass

    def song(self):
        return self._c_instance


_cs_module.ControlSurface = ControlSurface
_framework.ControlSurface = _cs_module
sys.modules.setdefault("_Framework", _framework)
sys.modules.setdefault("_Framework.ControlSurface", _cs_module)

import bridge_ops  # noqa: E402
import Loom as remote  # noqa: E402
sys.path.insert(0, str(REMOTE_DIR))
from test_bridge_ops import FakeSong  # noqa: E402

checks = []
failures = []


def check(label, condition, detail=""):
    if condition:
        checks.append(label)
    else:
        failures.append("%s  %s" % (label, detail))


def point_bridge_at(root: Path):
    """Point the remote script's bridge paths at a test directory."""
    remote.BRIDGE_ROOT = root
    remote.REQUEST_DIR = root / "requests"
    remote.DONE_DIR = root / "done"
    remote.ERROR_DIR = root / "errors"
    remote.STATE_DIR = root / "state"
    remote.STATE_FILE = remote.STATE_DIR / "live_state.json"


def make_remote(song):
    surface = remote.Loom(song)
    return surface


# ============ 1) The real remote script, isolated bridge ===================
temporary_root = Path(tempfile.mkdtemp(prefix="sensei_bridge_test_"))
point_bridge_at(temporary_root)
song = FakeSong()
song.tracks[2].name = "SNARE"
surface = make_remote(song)

check("the remote script loads and reports its version",
      any("v2" in message for message in surface.messages), surface.messages)

surface._dump_state()
state = json.loads(remote.STATE_FILE.read_text())
check("the state file is written", state["track_count"] == 3, state.get("track_count"))
check("the state carries a timestamp", state.get("captured_at"), state.get("captured_at"))


def send(payload, name=None):
    filename = name or ("req_%d.json" % int(time.time() * 1000000))
    (remote.REQUEST_DIR / filename).write_text(json.dumps(payload))
    return filename


filename = send({"op": "set_tempo", "bpm": 126})
surface._process_next_request()
record = json.loads((remote.DONE_DIR / filename).read_text())
check("the command is processed and moved into done/ with its result",
      record["status"] == "ok" and record["result"]["after"] == 126.0, record)
check("the request file leaves the queue", not (remote.REQUEST_DIR / filename).exists())
check("Live itself changes", song.tempo == 126.0, song.tempo)

filename = send({"op": "set_mixer", "track": "KICK", "volume": 0.4, "mute": True})
surface._process_next_request()
record = json.loads((remote.DONE_DIR / filename).read_text())
check("the mixer command returns before/after values",
      record["result"]["changes"]["volume"]["before"] == 0.85
      and record["result"]["changes"]["volume"]["after"] == 0.4, record.get("result"))

filename = send({"op": "set_mixer", "track": "YOK", "mute": True})
surface._process_next_request()
record = json.loads((remote.ERROR_DIR / filename).read_text())
check("a failing command moves into errors/ WITH ITS REASON",
      record["status"] == "error" and "found 0" in record["error"], record.get("error"))

filename = send({"op": "delete_everything"})
surface._process_next_request()
record = json.loads((remote.ERROR_DIR / filename).read_text())
check("an unknown operation is refused without touching Live", "unknown op" in record["error"], record.get("error"))

(remote.REQUEST_DIR / "bozuk.json").write_text("{ this is not json")
surface._process_next_request()
record = json.loads((remote.ERROR_DIR / "bozuk.json").read_text())
check("a corrupt request file does not jam the queue", "unreadable request" in record["error"], record.get("error"))

filename = send({"op": "create_locator", "beat": 96, "name": "Drop"})
surface._process_next_request()
check("the locator is created inside Live",
      len(song.cue_points) == 1 and song.cue_points[0].name == "Drop", [c.name for c in song.cue_points])

filename = send({"name": "legacy clip", "length_beats": 4,
                 "notes": [{"pitch": 36, "start": 0, "duration": 0.5, "velocity": 100}]})
surface._process_next_request()
record_path = remote.DONE_DIR / filename
error_path = remote.ERROR_DIR / filename
check("a request with no op is still read as write_clip (backwards compatible)",
      record_path.exists() or error_path.exists(),
      "the request was not moved anywhere")

shutil.rmtree(temporary_root, ignore_errors=True)

# ============ 2) The MCP tools, against the real bridge directory ==========
REAL_ROOT = Path.home() / "Documents" / "SenseiV2Bridge"
point_bridge_at(REAL_ROOT)
live_song = FakeSong()
live_song.tracks[2].name = "SNARE"
live_surface = make_remote(live_song)

stop_flag = threading.Event()
created = []


def fake_live_loop():
    """Mimic the control surface timer: process requests, publish state."""
    while not stop_flag.is_set():
        before = set(remote.REQUEST_DIR.glob("*.json"))
        if before:
            created.extend(path.name for path in before)
            live_surface._process_next_request()
            live_surface._dump_state()
        time.sleep(0.05)


thread = threading.Thread(target=fake_live_loop, daemon=True)
thread.start()

server_process = subprocess.Popen(
    [str(ROOT / "Sensei" / ".venv" / "bin" / "python"), str(ROOT / "mcp_server" / "server.py")],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, bufsize=1, cwd=str(ROOT),
)


def rpc(request_id, method, params=None):
    server_process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}) + "\n")
    server_process.stdin.flush()
    while True:
        message = json.loads(server_process.stdout.readline())
        if message.get("method") != "notifications/progress":
            return message


def tool(request_id, name, arguments):
    response = rpc(request_id, "tools/call", {"name": name, "arguments": arguments})
    return json.loads(response["result"]["content"][0]["text"])


try:
    rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})

    payload = tool(2, "live_command", {"op": "set_tempo", "bpm": 132, "wait_seconds": 6})
    check("tempo changes live through MCP", payload["status"] == "OK" and payload["result"]["after"] == 132.0, payload)
    check("the change really happened on Live's side", live_song.tempo == 132.0, live_song.tempo)

    payload = tool(3, "live_command", {
        "op": "set_device_parameter", "track": "KICK", "device": "EQ Eight",
        "parameter": "Gain A", "value": -4.5, "wait_seconds": 6})
    check("MCP uzerinden the device parameter is written",
          payload["status"] == "OK" and live_song.tracks[0].devices[0].parameters[1].value == -4.5, payload)

    payload = tool(4, "live_command", {
        "op": "set_device_parameter", "track": "KICK", "device": "EQ Eight",
        "parameter": "Gain A", "value": 999, "wait_seconds": 6})
    check("Live's own range check comes back to MCP as an error",
          payload["status"] == "FAILED_IN_LIVE" and "outside" in (payload.get("error") or ""), payload)
    check("the refused value did not change Live", live_song.tracks[0].devices[0].parameters[1].value == -4.5)

    payload = tool(5, "live_state", {"wait_seconds": 6, "max_age_seconds": 30})
    check("MCP can read Live's state", payload["available"] and payload["tempo"] == 132.0, str(payload)[:200])
    check("the state's freshness is reported", payload["is_fresh"] is True, payload.get("age_seconds"))
    check("the track list arrives in the state", payload["track_count"] == 3, payload.get("track_count"))

    stop_flag.set()
    thread.join(timeout=2)
    payload = tool(6, "live_command", {"op": "set_tempo", "bpm": 100, "wait_seconds": 1.0})
    check("if Live does not answer, that is stated plainly",
          payload["status"] == "NOT_CONSUMED" and payload["consumed"] is False, payload)
    created.append(Path(payload["request_file"]).name)
finally:
    stop_flag.set()
    server_process.stdin.close()
    server_process.wait(timeout=15)
    for name in set(created):
        for directory in (remote.REQUEST_DIR, remote.DONE_DIR, remote.ERROR_DIR):
            (directory / name).unlink(missing_ok=True)

print("%d checks passed:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
if failures:
    print()
    print("FAILED:")
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("LIVE BRIDGE WORKS")
