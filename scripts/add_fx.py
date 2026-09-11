#!/usr/bin/env python3
# © 2026 Şenol Şahan / SubverseLab · Loom · https://subverselab.com/loom
"""Rule-placed FX layers into the OPEN set: a riser before each rising
section boundary and a drop/impact hit on section starts.

This is NOT corpus evidence -- Sensei has no FX role and Loom has no FX
corpus -- it is a stated rule over the set's own cue points (read from
Live now, never from the file): a held riser note that ends exactly on a
boundary, a one-bar hit that starts on it. Every note is reported with the
rule that placed it. Real presets from the Core Library's Instrument Racks
/ Effects go on the tracks through the OS-load path (verified). No tempo,
no key, no locator.

    python3 scripts/add_fx.py --riser-track FX --riser "Riser Basic" --drop-track "FX Drop" --drop "Dropping Vehicle" \
        --rise-into Verse Verse2 --hit-on Verse Verse2 ES --riser-bars 2
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = Path("/Applications/Ableton Live 12 Beta.app/Contents/App-Resources/Core Library/Racks/Instrument Racks/Effects")
spec = importlib.util.spec_from_file_location("loom_server", ROOT / "mcp_server" / "server.py")
loom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loom)  # type: ignore[union-attr]


def say(text: str) -> None:
    print(text, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--riser-track", default="FX")
    parser.add_argument("--riser", default="Riser Basic")
    parser.add_argument("--drop-track", default="FX Drop")
    parser.add_argument("--drop", default="Dropping Vehicle")
    parser.add_argument("--rise-into", nargs="*", default=["Verse", "Verse2"], help="cue names a riser leads into")
    parser.add_argument("--hit-on", nargs="*", default=["Verse", "Verse2", "ES"], help="cue names that get a hit on their first beat")
    parser.add_argument("--riser-bars", type=int, default=2)
    parser.add_argument("--hit-bars", type=int, default=1)
    parser.add_argument("--riser-note", type=int, default=59, help="MIDI note the riser is played on (a trigger, not a scale degree)")
    parser.add_argument("--hit-note", type=int, default=47)
    parser.add_argument("--wait", type=float, default=60.0)
    args = parser.parse_args()
    bpb = 4.0

    state = loom.handle_live_state({"wait_seconds": 10, "include_devices": True})
    if not state.get("is_fresh"):
        say(f"STOP: Live state is not fresh ({state.get('reason') or state.get('message')})"); return 1
    cues = sorted(((float(c["time"]), str(c["name"]).strip()) for c in state.get("cue_points") or []), key=lambda c: c[0])
    say(f"SET   {(state.get('set') or {}).get('name')}  tempo {state.get('tempo')}  cues {[(n, b) for b, n in cues]}")
    tracks = {str(t.get("name")): t for t in state.get("tracks") or []}
    target = loom.resolve_bridge_target()
    gate = loom.bridge_client.mutation_gate(target, "write_arrangement_clip")
    if gate:
        say(f"STOP: {gate.get('status')} {gate.get('error')}"); return 1
    run_key = f"{target.session_id}:fx:{int(time.time())}"

    def send(payload, key):
        return loom.bridge_client.submit_request(payload, args.wait, target=target, idempotency_key=f"{run_key}:{key}")

    # the rule, as placements (start_beat, length_beats, note, why)
    riser_events = []
    hit_events = []
    for index, (beat, name) in enumerate(cues):
        if name in args.rise_into and beat - args.riser_bars * bpb >= 0:
            riser_events.append((beat - args.riser_bars * bpb, args.riser_bars * bpb, args.riser_note, f"riser ending on {name}@{beat:g}"))
        if name in args.hit_on and index > 0:  # never on the very first cue (intro start)
            hit_events.append((beat, args.hit_bars * bpb, args.hit_note, f"hit on {name}@{beat:g}"))
    say(f"RULE  riser {args.riser_bars} bar(s) before {args.rise_into} -> {len(riser_events)} placements; {args.hit_bars}-bar hit on {args.hit_on} (not the first cue) -> {len(hit_events)}")

    def prepare(track_name: str, preset: str):
        path = LIB / f"{preset}.adg"
        if not path.is_file():
            say(f"\nTRACK {track_name}: preset file not found: {path}"); return None
        created = False
        if track_name in tracks:
            devices = [d.get("name") for d in tracks[track_name].get("devices") or []]
            if devices != [preset]:
                # An existing track cannot receive a preset through the OS (Live loads onto the selected track; measured).
                say(f"\nTRACK {track_name}: exists with {devices or 'no device'} -- an existing track cannot be given a preset from here; give it {preset!r} in Live or let me create a new track"); return None
            index = tracks[track_name].get("index"); say(f"\nTRACK {track_name}: adopted (index {index}, already holds {preset!r})")
        else:
            created = True
            outcome = loom._create_midi_track_request(track_name, None, args.wait, target=target, idempotency_key=f"{run_key}:track:{track_name}")
            if outcome.get("status") != "OK":
                say(f"\nTRACK {track_name}: {outcome.get('status')} {outcome.get('error')}"); return None
            index = (outcome.get("result") or {}).get("index"); say(f"\nTRACK {track_name}: created (index {index})")
        loaded = loom._os_load_preset(str(path), track_name, preset, args.wait, send, expected_index=index, just_created=created)
        say(f"  PRESET {preset}: {loaded.get('status')} verified={loaded.get('verified')} via={loaded.get('via')} {loaded.get('seconds', '')} {loaded.get('reason') or ''}")
        return loaded.get("verified") and track_name

    results = []
    for track_name, preset, events, label in ((args.riser_track, args.riser, riser_events, "riser"), (args.drop_track, args.drop, hit_events, "hit")):
        ok = prepare(track_name, preset)
        if not ok:
            continue
        for start, length, note, why in events:
            notes = [{"pitch": note, "start": 0.0, "duration": length, "velocity": 100}]
            written = loom.write_arrangement_clip({"track": track_name, "start_bar": int(start // bpb) + 1, "length_beats": length, "beats_per_bar": bpb,
                                                   "name": f"{label} {why.split()[-1]}", "notes": notes, "wait_seconds": args.wait, "on_conflict": "refuse"},
                                                  target=target, idempotency_key=f"{run_key}:clip:{track_name}:{start:g}")
            held = written.get("result") or {}
            results.append((track_name, start, length, note, why, written.get("status"), held.get("verified_notes_match"), written.get("error")))
    say("\nCLIPS (rule-placed)")
    for track_name, start, length, note, why, status, verified, error in results:
        say(f"  {track_name:8} bar {int(start // bpb) + 1:>3}  {length / bpb:g} bar  note {note}  {why:26} {status}  verified={verified} {error or ''}")
    say("Nothing else was touched: no tempo, no key, no locator. These placements are a rule, not corpus evidence.")
    return 0 if results and all(r[5] == "OK" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
