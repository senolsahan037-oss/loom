#!/usr/bin/env python3
"""Add layers to the arrangement that is OPEN in Live -- and nothing else.

Not a project build: no tempo, no key, no locators, no plan. The sections
come from the set's own cue points as Live reports them now (never from
the file on disk), each requested track gets its real preset through the
OS-load path (verified on the target track), and MIDI is written only
into the sections named. Every note comes from midi_generate; nothing
here places a note.

    python3 scripts/add_layers.py --track "Dub Kit:drum:Fear Pressure Kit" --track "Wobble:bass:Reese Filtered" \
        --sections Verse Verse2 --genre Dubstep --key "B Minor" [--clip-bars 8] [--density 0.7] [--seed N]
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loom_server", ROOT / "mcp_server" / "server.py")
loom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loom)  # type: ignore[union-attr]


def say(text: str) -> None:
    print(text, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--track", action="append", required=True, metavar="NAME:ROLE:PRESET")
    parser.add_argument("--sections", nargs="+", required=True, help="cue names to write into (a repeated cue name means every cue of that name)")
    parser.add_argument("--genre", default="Dubstep")
    parser.add_argument("--key", default="B Minor")
    parser.add_argument("--clip-bars", type=int, default=8)
    parser.add_argument("--density", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--wait", type=float, default=60.0)
    parser.add_argument("--adopt", action="store_true", help="write into an existing track of that name when it holds exactly the named preset (clips of the same name are replaced)")
    args = parser.parse_args()
    seed = args.seed if args.seed is not None else int(time.time()) % 100_000
    root, mode = loom._plan_key(args.key)

    # --- the set as Live reports it now ---
    state = loom.handle_live_state({"wait_seconds": 10, "include_devices": True})
    if not state.get("is_fresh"):
        say(f"STOP: Live state is not fresh ({state.get('reason') or state.get('message')})"); return 1
    bpb = 4.0
    cues = sorted(((float(c["time"]), str(c["name"]).strip()) for c in state.get("cue_points") or []), key=lambda c: c[0])
    tempo = state.get("tempo")
    say(f"SET   {((state.get('set') or {}).get('name'))}  tempo {tempo}  cues {[(n, b) for b, n in cues]}")
    if not cues:
        say("STOP: the set has no cue points; name the sections in Live first"); return 1
    existing = {str(t.get("name")) for t in state.get("tracks") or []}
    # sections: cue -> next cue (the last cue runs to the last clip end, unknown here: to next cue only)
    sections = []
    for index, (beat, name) in enumerate(cues):
        if name not in args.sections:
            continue
        if index + 1 >= len(cues):
            say(f"skip {name}@{beat:g}: last cue, no end known"); continue
        sections.append({"name": name, "start_beat": beat, "end_beat": cues[index + 1][0]})
    say(f"SECTIONS to write: {[(s['name'], s['start_beat'], s['end_beat']) for s in sections]}  (no locator, tempo or key is written)")
    if not sections:
        return 1

    target = loom.resolve_bridge_target()
    gate = loom.bridge_client.mutation_gate(target, "write_arrangement_clip")
    if gate:
        say(f"STOP: {gate.get('status')} {gate.get('error')}"); return 1
    set_tag = (state.get("set") or {}).get("name") or "set"
    run_key = f"{target.session_id}:layers:{set_tag}:{seed}"

    def send(payload, key):
        return loom.bridge_client.submit_request(payload, args.wait, target=target, idempotency_key=f"{run_key}:{key}")

    report = {"tracks": [], "clips": []}
    for spec_ in args.track:
        name, role, preset = (x.strip() for x in spec_.split(":", 2))
        kit = loom.resolve_kit_reference(preset) if role == "drum" else None
        preset_path = kit["path"] if kit else (loom._catalog_entry(preset) or {}).get("path")
        if not preset_path:
            say(f"\nTRACK {name}: no preset file for {preset!r} on this machine -- not created"); continue
        expected_device = str(kit["kit"] if kit else Path(preset_path).stem)
        if name in existing:
            devices = [d.get("name") for t in state.get("tracks") or [] if str(t.get("name")) == name for d in t.get("devices") or []]
            if not (args.adopt and devices == [expected_device]):
                say(f"\nTRACK {name}: already in the set with {devices} -- refusing (pass --adopt when it holds exactly {expected_device!r})"); continue
            say(f"\nTRACK {name}: adopted (holds {expected_device!r}); clips of the same name will be replaced")
            result = {"index": next(t.get("index") for t in state.get("tracks") or [] if str(t.get("name")) == name)}
        else:
            outcome = loom._create_midi_track_request(name, None, args.wait, target=target, idempotency_key=f"{run_key}:track:{name}")
            result = outcome.get("result") or {}
            say(f"\nTRACK {name}: {outcome.get('status')} created={result.get('created')} index={result.get('index')}")
            if outcome.get("status") != "OK":
                continue
        loaded = loom._os_load_preset(preset_path, name, expected_device, args.wait, send, expected_index=result.get("index"), just_created=name not in existing)
        say(f"  PRESET {Path(preset_path).name}: {loaded.get('status')} verified={loaded.get('verified')} via={loaded.get('via')} {loaded.get('seconds', '')}s {loaded.get('reason') or ''}")
        if not loaded.get("verified"):
            continue
        pad_notes = None
        mapping = {"mode": "direct", "generate_pads": None, "map": {}}
        if role == "drum":
            pads = send({"op": "drum_pads", "track": name}, f"pads:{name}")
            pad_notes = [int(n) for n in ((pads.get("result") or {}).get("pad_notes") or [])]
            expected = sorted(int(p["note"]) for p in kit["pads"])
            if sorted(pad_notes) != expected:
                say(f"  PADS differ: live {sorted(pad_notes)} vs file {expected} -- no drum written"); continue
            resolved = loom.pad_notes_resolver()(pad_notes, kit, kit_verified=True)
            mapping = resolved["mapping"]
            say(f"  PADS {len(pad_notes)} = the file's decoded pads; roles {resolved.get('role_source')}; mapping {mapping['mode']} targets {mapping.get('target_notes') or mapping.get('generate_pads')}")
        report["tracks"].append({"track": name, "preset": preset, "verified": True})
        for s_index, section in enumerate(sections):
            length_beats = section["end_beat"] - section["start_beat"]
            clip_beats = args.clip_bars * bpb
            starts = [section["start_beat"] + i * clip_beats for i in range(int(length_beats // clip_beats))]
            if length_beats % clip_beats:
                starts.append(section["start_beat"] + int(length_beats // clip_beats) * clip_beats)
            for c_index, start in enumerate(starts):
                bars = min(args.clip_bars, int((section["end_beat"] - start) // bpb))
                if bars <= 0:
                    continue
                generated = loom.handle_midi_generate({
                    "role": role, "genre": args.genre, "bars": bars, "instrument_family": preset,
                    "seed": seed + s_index * 100 + c_index, "density": args.density, "genre_style": args.genre.lower(),
                    "target_root": root, "target_mode": mode, "beats_per_bar": bpb,
                    "pad_notes": mapping["generate_pads"] if role == "drum" else None, "instrument_verified": role != "drum"})
                if not generated.get("generation_safe") or not generated.get("writable_to_live"):
                    report["clips"].append({"track": name, "section": section["name"], "start_beat": start, "status": "blocked", "reason": generated.get("error") or generated.get("writable_reason")}); continue
                raw = (generated.get("payload") or {}).get("notes") or []
                if mapping.get("mode") == "by_role":
                    raw = [{**n, "pitch": mapping["map"][int(n["pitch"])]} for n in raw if int(n["pitch"]) in mapping["map"]]
                notes = [{"pitch": n["pitch"], "start": n.get("time", n.get("start", 0.0)), "duration": n["duration"], "velocity": n.get("velocity", 100)} for n in raw]
                if role == "drum" and pad_notes is not None:
                    outside = sorted({int(n["pitch"]) for n in notes} - set(pad_notes))
                    if outside:
                        report["clips"].append({"track": name, "section": section["name"], "start_beat": start, "status": "blocked", "reason": f"notes_outside_pads {outside}"}); continue
                if not notes:
                    report["clips"].append({"track": name, "section": section["name"], "start_beat": start, "status": "blocked", "reason": "no notes"}); continue
                written = loom.write_arrangement_clip({
                    "track": name, "start_bar": int(start // bpb) + 1, "length_beats": bars * bpb, "beats_per_bar": bpb,
                    "name": f"{section['name']} {c_index + 1}", "notes": notes, "wait_seconds": args.wait, "on_conflict": "refuse"},
                    target=target, idempotency_key=f"{run_key}:clip:{name}:{section['name']}@{start:g}")
                held = written.get("result") or {}
                report["clips"].append({"track": name, "section": section["name"], "start_beat": start, "bars": bars, "notes": len(notes),
                                        "pitches": sorted({int(n["pitch"]) for n in notes}), "status": written.get("status"), "key": generated.get("key"),
                                        "verified": held.get("verified_notes_match"), "error": written.get("error")})
    say("\nCLIPS")
    for c in report["clips"]:
        k = c.get("key") or {}
        key_note = (f"key {k.get('source_root')} {k.get('source_mode')} -> {k.get('target_root')} ({k.get('semitones'):+d} st, from {k.get('key_source')})" if k.get("applied") else (f"KEY NOT APPLIED: {k.get('reason')}" if k.get("applied") is False else ""))
        say(f"  {c['track']:8} {c['section']:7} bar {int(c['start_beat'] // bpb) + 1:>3}  {c.get('bars', '-')}b  {c.get('notes', '-')}n  {c['status']}  verified={c.get('verified')}  {c.get('reason') or c.get('error') or ''}  pitches={c.get('pitches')}  {key_note}")
    say(f"\nSUMMARY {dict(Counter(c['status'] for c in report['clips']))}  seed {seed}  (pass --seed {seed} to reproduce)")
    say("Nothing else was touched: no tempo, no key, no locator.")
    return 0 if report["clips"] and all(c["status"] == "OK" for c in report["clips"]) else 1


if __name__ == "__main__":
    sys.exit(main())
