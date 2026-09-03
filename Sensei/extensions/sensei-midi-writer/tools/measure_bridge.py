"""Measure the extension-side bridge against the running Live through the
MCP's own handlers.

    python3 tools/measure_bridge.py [bridge_root]

Without an argument it finds the freshest extension bridge on this machine.
Writes measure_bridge.json next to itself. Touches the open set: tempo (set
and restored), one locator at beat 320, two probe tracks, one arrangement
clip at bar 81, one session clip -- run it on a scratch set.
"""
import importlib.util, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # .../Loom

def discover_bridge_root() -> Path:
    """Explicit argument, else the freshest extension bridge on this machine
    (hosted: ~/Library/Application Support/Ableton/Extensions Data/*/bridge;
    developer mode: the extension's .dev/storage/bridge)."""
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser()
    candidates = []
    data = Path.home() / "Library" / "Application Support" / "Ableton" / "Extensions Data"
    if data.exists():
        candidates += [p / "bridge" for p in data.iterdir() if (p / "bridge" / "state" / "live_state.json").exists()]
    dev = ROOT / "Sensei" / "extensions" / "sensei-midi-writer" / ".dev" / "storage" / "bridge"
    if (dev / "state" / "live_state.json").exists():
        candidates.append(dev)
    fresh = []
    for root in candidates:
        try:
            state = json.loads((root / "state" / "live_state.json").read_text())
            if str(state.get("surface_version", "")).startswith("loom-extension"):
                fresh.append((time.time() - float(state.get("captured_at") or 0), root))
        except Exception:
            continue
    if not fresh:
        sys.exit("no extension bridge has published state on this machine; is the extension loaded in Live?")
    fresh.sort()
    age, root = fresh[0]
    print(f"bridge root: {root}  (state age {age:.1f}s)")
    return root

BRIDGE = discover_bridge_root()
os.environ["LOOM_BRIDGE_ROOT"] = str(BRIDGE)
spec = importlib.util.spec_from_file_location("loom_server", ROOT / "mcp_server" / "server.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

rows = []
def run(label, fn):
    t = time.time()
    try:
        out = fn()
        status = out.get("status") if isinstance(out, dict) else None
        rows.append((label, status, round(time.time() - t, 2), out))
    except Exception as error:  # noqa: BLE001
        rows.append((label, f"EXC {type(error).__name__}", round(time.time() - t, 2), {"error": str(error)}))

W = 12
run("bridge_status", lambda: m.handle_live_bridge_status({}))
run("live_state", lambda: m.handle_live_state({"wait_seconds": W}))
run("set_tempo 124", lambda: m.handle_live_command({"op": "set_tempo", "bpm": 124, "wait_seconds": W}))
run("set_mixer Kit vol .7", lambda: m.handle_live_command({"op": "set_mixer", "track": "Kit", "volume": 0.7, "wait_seconds": W}))
run("list params Keys/Electric Piano Daze", lambda: m.handle_live_command({"op": "list_device_parameters", "track": "Keys", "device": "Electric Piano Daze", "wait_seconds": W}))
run("create_locator 320 Tail", lambda: m.handle_live_command({"op": "create_locator", "beat": 320, "name": "Tail", "wait_seconds": W}))
run("create_midi_track ExtProbe + Drum Rack", lambda: m.handle_live_command({"op": "create_midi_track", "name": "ExtProbe", "instrument_family": "Drum Rack", "wait_seconds": W}))
run("create_midi_track ExtProbe2 + preset name", lambda: m.handle_live_command({"op": "create_midi_track", "name": "ExtProbe2", "instrument_family": "Boom Bap Kit", "wait_seconds": W}))
notes = [{"pitch": 36, "start": 0, "duration": 0.5, "velocity": 110}, {"pitch": 38, "start": 1, "duration": 0.5, "velocity": 100},
         {"pitch": 36, "start": 2, "duration": 0.5, "velocity": 110}, {"pitch": 38, "start": 3, "duration": 0.5, "velocity": 100}]
run("write_arrangement_clip ExtProbe bar 81", lambda: m.handle_midi_write_arrangement({"track": "ExtProbe", "start_bar": 81, "length_beats": 8, "beats_per_bar": 4, "name": "ext probe", "notes": notes, "wait_seconds": W}))
run("midi_write_to_live session clip", lambda: m.handle_midi_write_to_live({"name": "ext session probe", "length_beats": 4, "notes": notes, "wait_seconds": W}))
run("set_key (expect refusal)", lambda: m.handle_live_command({"op": "set_key", "root": "F", "mode": "Minor", "wait_seconds": W}))
run("transport play (expect refusal)", lambda: m.handle_live_command({"op": "transport", "action": "play", "wait_seconds": W}))
run("set_tempo back 126", lambda: m.handle_live_command({"op": "set_tempo", "bpm": 126, "wait_seconds": W}))

print("%-42s %-16s %6s  %s" % ("op", "status", "s", "detail"))
for label, status, secs, out in rows:
    detail = ""
    if isinstance(out, dict):
        res = out.get("result") if isinstance(out.get("result"), dict) else None
        if out.get("error"): detail = str(out["error"])[:110]
        elif label == "bridge_status": detail = "aktif=%s" % [c["root"].split("/")[-3] + ":" + str(c.get("state")) + ":" + str(c.get("surface_version")) for c in out.get("bridge_candidates", []) if c.get("active")]
        elif label == "live_state": detail = "surface=%s tracks=%s fresh=%s caps=%s" % (out.get("surface_version"), out.get("track_count"), out.get("is_fresh"), (out.get("capabilities") or {}).get("transport"))
        elif res is not None: detail = json.dumps(res, ensure_ascii=False)[:120]
        elif out.get("message"): detail = str(out["message"])[:100]
    print("%-42s %-16s %6s  %s" % (label, status, secs, detail))
json.dump([(l, s, t, o) for l, s, t, o in rows], open(Path(__file__).with_name("measure_bridge.json"), "w"), default=str, indent=1)
