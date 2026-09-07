#!/usr/bin/env python3
"""Build a project into the running Live, driving the MCP's own writers.

Every note this script places is produced by `midi_generate` (Sensei's locked
corpus) and written by `midi_write_arrangement` through the Loom extension --
both inside `project_build`. Nothing here constructs a note, a pitch or a
position; if the corpus has no evidence for a role, the part is not written
and the reason is printed.

What the script adds on top of `project_build` is the decision that a single
call cannot make: **a plan carries one genre, but the corpus does not have
every role under every genre** (measured 2026-09-07: Hip Hop has drums and
chords but no bass; Trap has drums and bass but no chords). So it measures
the evidence per role first, groups the plan's tracks by a genre that can
actually answer for them, and runs one `project_build` per group -- reporting
every substitution instead of leaving blocked writes to be discovered.

    scripts/build_live_project.py --prompt "boom bap hip hop, 90 bpm, in D minor" \
        --tracks Kit Keys "Main Bass" \
        --device Keys=Electric --device "Main Bass=Operator" [--dry-run]

**The kit is yours.** By default this script does not build one: it writes onto
the Drum Rack already on the track, reading its pads from the device. That is
the only path that keeps a kit whole -- macros, choke groups, per-pad effects
and Simpler settings are not readable through the SDK, so anything Loom
"rebuilds" from samples is a different kit that happens to share files. If the
drum track has no pads the drum parts are not written and the script says to
load a kit and run again. `--rebuild-kit-from NAME` is the explicit opt-in for
the rebuild, for a set where loading one by hand is not wanted.

**A new take every run.** The seed decides which corpus entries are drawn.
It is fresh on each run and printed; pass `--seed N` to reproduce a take
exactly.

Safety: refuses to write into a set that already holds tracks or clips, so a
new project means a new project. `--into-current-set` overrides that when you
mean to add to what is open (which is how you continue after loading a kit).
Nothing is deleted; a clip Loom did not write is never touched.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The MCP server is the only entry: its handlers are what a client would call.
_spec = importlib.util.spec_from_file_location("loom_server_cli", ROOT / "mcp_server" / "server.py")
loom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loom)

# Tracks Live's own template leaves in a new set. They are the user's, not
# Loom's leftovers, and their presence does not make a set "used".
TEMPLATE_TRACK = ("-MIDI", "-Audio")
ROLES = ("drum", "bass", "chord")


def say(line: str = "") -> None:
    print(line, flush=True)


def check_bridge() -> dict:
    status = loom.handle_live_bridge_status({})
    bridge = status.get("bridge") or {}
    say("BRIDGE")
    say(f"  root       {status.get('bridge_root')}")
    say(f"  extension  {bridge.get('surface_version')}  protocol {bridge.get('bridge_protocol')}  session {bridge.get('session_id')}")
    say(f"  mutations  {status.get('mutations_allowed')}  state age {bridge.get('state_age_seconds')}s  journal {(bridge.get('journal') or {}).get('state')}")
    for legacy in status.get("legacy_bridges") or []:
        say(f"  superseded {legacy['extension_id']} ({legacy['state']}) -- {legacy.get('next_step') or 'nothing to do'}")
    if not status.get("mutations_allowed"):
        raise SystemExit(f"refusing to build: {status.get('protocol', {}).get('status')} -- {status.get('protocol', {}).get('reason') or status.get('error')}")
    return status


def check_set_is_new(allow_used: bool) -> dict:
    state = loom.handle_live_state({"wait_seconds": 10, "include_devices": True})
    if not state.get("available"):
        raise SystemExit(f"no Live state: {state.get('reason')} -- {state.get('message')}")
    tracks = state.get("tracks") or []
    used = [t for t in tracks if not str(t.get("name", "")).endswith(TEMPLATE_TRACK) or t.get("devices")]
    say("\nOPEN SET")
    say(f"  tempo {state.get('tempo')}  tracks {len(tracks)}  locators {len(state.get('cue_points') or [])}")
    for track in tracks:
        devices = [d["name"] for d in track.get("devices") or []]
        say(f"    {track['name']:16} {devices if devices else ''}")
    if used and not allow_used:
        raise SystemExit(
            "\nrefusing to build: this set already has named tracks or devices.\n"
            "Open a new set in Live (File > New Live Set) and run again, or pass\n"
            "--into-current-set to add to what is open.")
    return state


def measure_evidence(genres: list[str], roles: list[str], generate_pads: list[int] | None, key: tuple[str, str]) -> dict:
    """Which (role, genre) pairs the corpus can actually answer, measured by
    calling the generator -- not by consulting a table.

    `generate_pads` must be the notes the corpus writes IN, not the kit's own
    pad numbers: Sensei's drum corpus is General-MIDI pitched and the kit's
    pads are reached afterwards through `pad_mapping`. Passing the kit's raw
    pads here measures the wrong thing and reports "no candidate" for a role
    the corpus can answer perfectly well (found by this script, 2026-09-07).
    """
    say("\nEVIDENCE (measured, not assumed)")
    table: dict[str, dict[str, bool]] = {}
    for role in roles:
        table[role] = {}
        for genre in genres:
            extra: dict = {}
            if role == "drum":
                extra["pad_notes"] = generate_pads or [36, 38, 42, 46]
            else:
                extra["instrument_verified"] = True
            answer = loom.handle_midi_generate({"role": role, "genre": genre, "bars": 4, "seed": 7,
                                                "target_root": key[0], "target_mode": key[1], **extra})
            ok = bool(answer.get("generation_safe"))
            table[role][genre] = ok
            notes = len((answer.get("payload") or {}).get("notes") or [])
            detail = f"{notes} notes" if ok else str(answer.get("error"))[:44]
            say(f"  {role:6} {genre:10} {'OK  ' if ok else 'none'} {detail}")
    return table


def genre_for(role: str, table: dict, plan_genre: str) -> str | None:
    """The plan's genre when it can answer for this role, else the first genre
    that can. None when nothing can -- the part is then not written."""
    if table.get(role, {}).get(plan_genre):
        return plan_genre
    return next((genre for genre, ok in table.get(role, {}).items() if ok), None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prompt", default=None, help="the musical brief ArrangementGPS plans from (or --plan-path)")
    parser.add_argument("--plan-path", default=None, help="an existing plan -- the one project_file_build built the set from")
    parser.add_argument("--set-manifest", default=None, metavar="JSON",
                        help="the manifest project_file_build wrote next to the set now open in Live: the kit is verified "
                             "against it (nothing rebuilt), the other tracks are adopted with their real presets; implies --into-current-set")
    parser.add_argument("--kit", metavar="NAME_OR_ADG", default=None,
                        help="load THIS real kit preset instead of the plan's (explicit override; loaded natively, never rebuilt)")
    parser.add_argument("--rebuild-kit-from", metavar="NAME_OR_ADG", default=None, help=argparse.SUPPRESS)  # old name of --kit
    parser.add_argument("--seed", type=int, default=None,
                        help="reproduce a take exactly. Default: a fresh seed each run, printed in the report.")
    parser.add_argument("--tracks", nargs="+", required=True, help="which plan tracks to build (an explicit simplification)")
    parser.add_argument("--device", action="append", default=[], metavar="TRACK=DEVICE",
                        help="native device to insert instead of the plan's preset name; repeatable")
    parser.add_argument("--fallback-genres", nargs="*", default=[],
                        help="genres to fall back to for a role the plan's genre has no evidence for")
    parser.add_argument("--wait", type=float, default=90.0, help="seconds to wait per Live request")
    parser.add_argument("--dry-run", action="store_true", help="report the write list, change nothing in Live")
    parser.add_argument("--into-current-set", action="store_true", help="build into the open set even if it is not empty")
    args = parser.parse_args()

    device_map = dict(pair.split("=", 1) for pair in args.device)
    started = time.time()
    # A take is the seed. Fresh each run so a rebuild is a new take, printed so
    # it can be reproduced; --seed pins it.
    seed = args.seed if args.seed is not None else int(time.time()) % 100_000
    say(f"TAKE  seed {seed}" + ("  (given)" if args.seed is not None else "  (fresh; pass --seed %d to reproduce this take)" % seed))

    state = check_bridge()
    if args.set_manifest:
        args.into_current_set = True
    if not (args.prompt or args.plan_path):
        parser.error("give --prompt or --plan-path")
    live_state = check_set_is_new(args.into_current_set) if not args.dry_run else loom.handle_live_state({"wait_seconds": 10, "include_devices": True})

    # --- the kit -----------------------------------------------------------
    # Default: whatever is on the track already, read from the device. Only
    # with --rebuild-kit-from does the script put one there from samples.
    kit = None
    pads = None
    if args.kit or args.rebuild_kit_from:
        kit = loom.resolve_kit_reference(args.kit or args.rebuild_kit_from)
        pads = loom.pad_notes_resolver()(None, kit)
        say(f"\nKIT (explicit override, loaded natively)  {kit['kit']}")
        say(f"  file      {kit['path']}")
        say(f"  pads      {len(kit['pads'])}/{kit['pad_count']} resolved, {len(kit['missing'])} sample(s) missing, states {kit['sample_states']}")
        say(f"  roles     {sorted({p['role'] for p in kit['pads']})}")
        dropped = kit["fidelity"]["dropped"]
        say(f"  NOT carried: {len(dropped['macros'])} macro(s), per-pad effects on {len(dropped['per_pad_effects'])} pad(s), {', '.join(dropped['always'])}")
        say("  this is a different kit that shares sample files; load the preset yourself for the real one")
    else:
        if args.set_manifest:
            say(f"\nKIT  the real preset already in the set file; verified against {args.set_manifest}")
        else:
            say("\nKIT  the plan's real preset, handed to Live through the OS onto the new track and read back (nothing rebuilt; no load = PRESET_LOAD_REQUIRED)")
        drum_tracks = [t["name"] for t in (live_state.get("tracks") or [])
                       if any("Drum" in d.get("name", "") or "Kit" in d.get("name", "") for d in t.get("devices") or [])]
        say(f"  racks in the set: {drum_tracks or 'none yet'}")

    # --- the plan: ArrangementGPS, not this script -------------------------
    if args.plan_path:
        plan_path = Path(args.plan_path).expanduser()
        plan_run = {"plan_path": str(plan_path), "run_id": "reuse", "status": "REUSED"}
        say(f"\nPLAN  reused {plan_path}")
    else:
        plan_run = loom.handle_plan_create({"prompt": args.prompt})
        plan_path = Path(plan_run["plan_path"])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    project = plan.get("project") or {}
    root, mode = loom._plan_key(str(project.get("key") or ""))
    say(f"\nPLAN  {plan_run['run_id']}")
    say(f"  {project.get('bpm')} bpm, {project.get('key')}, genre {project.get('genre')}, {len(plan.get('locators') or [])} sections, {project.get('total_bars')} bars")
    say(f"  sections  {[l['name'] for l in plan.get('locators') or []]}")
    say(f"  tracks    {len(plan.get('tracks') or [])} in the plan, {len(args.tracks)} selected: {args.tracks}")

    wanted = {name: track for track in plan.get("tracks") or []
              if (name := loom._plan_track_name(track)) in args.tracks}
    unknown = [name for name in args.tracks if name not in wanted]
    if unknown:
        raise SystemExit(f"not in the plan: {unknown}\navailable: {[loom._plan_track_name(t) for t in plan.get('tracks') or []]}")

    plan_genre = str(project.get("genre") or "Trap")
    roles_used = sorted({str(t.get("sensei_role")) for t in wanted.values() if t.get("sensei_role")})
    # The corpus writes in General MIDI; probe it there. When a kit is being
    # rebuilt its own mapping is known up front, otherwise the live pads
    # decide it at build time and the GM core is the right probe.
    probe_pads = pads["mapping"]["generate_pads"] if pads else None
    evidence = measure_evidence([plan_genre] + [g for g in args.fallback_genres if g != plan_genre],
                                roles_used, probe_pads, (root, mode))

    # --- group the selected tracks by a genre that can answer for them -----
    groups: dict[str, list[str]] = {}
    skipped: list[tuple[str, str]] = []
    for name, track in wanted.items():
        role = str(track.get("sensei_role") or "")
        if not role or role == "None":
            skipped.append((name, "no Sensei role in the plan (melody/vocal/fx are out of scope by design)"))
            continue
        genre = genre_for(role, evidence, plan_genre)
        if genre is None:
            skipped.append((name, f"the corpus has no {role} evidence for {plan_genre} or the fallbacks -- not written"))
            continue
        groups.setdefault(genre, []).append(name)

    say("\nBUILD GROUPS (one project_build per genre the corpus can answer with)")
    for genre, names in groups.items():
        note = "the plan's own genre" if genre == plan_genre else f"substituted for {plan_genre}: no evidence there for these roles"
        say(f"  {genre:10} {names}  ({note})")
    for name, why in skipped:
        say(f"  skipped    {name}: {why}")
    if not groups:
        raise SystemExit("nothing to build")

    # --- run them, through project_build and nothing else -------------------
    results = []
    for index, (genre, names) in enumerate(groups.items()):
        group_plan = dict(plan, project=dict(project, genre=genre))
        group_plan["tracks"] = [t for t in plan["tracks"] if loom._plan_track_name(t) in names]
        group_path = plan_path.with_name(f"build_group_{index}_{genre.replace(' ', '_').lower()}.json")
        group_path.write_text(json.dumps(group_plan, indent=2, ensure_ascii=False), encoding="utf-8")
        say(f"\n=== project_build  genre={genre}  tracks={names}  dry_run={args.dry_run} ===")
        request = {"plan_path": str(group_path), "dry_run": args.dry_run, "seed": seed,
                   "tracks": names, "device_map": {k: v for k, v in device_map.items() if k in names},
                   "wait_seconds": args.wait}
        if args.set_manifest:
            request["set_manifest"] = args.set_manifest
        if args.kit or args.rebuild_kit_from:
            request["kit"] = args.kit or args.rebuild_kit_from  # a different real preset, loaded natively
        answer = loom.handle_project_build(request)
        results.append((genre, answer))
        report(answer, args.dry_run)

    say(f"\n=== SUMMARY  {time.time() - started:.1f}s ===")
    for genre, answer in results:
        totals = answer.get("totals") or {}
        say(f"  {genre:10} {answer['status']:12} writes {totals}")
        for item in answer.get("indeterminate") or []:
            say(f"    INDETERMINATE {item['step']}: {item.get('side_effects')} -> {item.get('next_step')}")
    if not args.dry_run:
        state = loom.handle_live_state({"wait_seconds": 10, "include_devices": True})
        say(f"\nLIVE NOW  tempo {state.get('tempo')}  locators {len(state.get('cue_points') or [])}")
        for track in state.get("tracks") or []:
            say(f"    {track['name']:16} {[d['name'] for d in track.get('devices') or []]}")
    blocked_drums = [w for _g, a in results for w in a.get("writes") or []
                     if w["status"] == "blocked" and "drum_rack_not_verified" in str(w.get("reason"))]
    if blocked_drums:
        drum_track = blocked_drums[0]["track"]
        say(f"\nDRUMS NOT WRITTEN ({len(blocked_drums)} clips): '{drum_track}' has no Drum Rack pads.")
        say(f"  Load your own kit onto '{drum_track}' in Live, then run again with just that track:")
        say(f"    --tracks {drum_track} --into-current-set --seed {seed}")
        say("  (the other parts are already written; asking for them again would be refused, correctly,")
        say("   because they are Loom's own clips and nothing overwrites them without on_conflict.)")
    say(f"\nSeed {seed}. Every note came from midi_generate (Sensei corpus) and was written by "
        "midi_write_arrangement through the Loom extension; this script placed none.")
    return 0 if all(answer["status"] in ("completed", "dry_run") for _genre, answer in results) else 1


def report(answer: dict, dry_run: bool) -> None:
    for name, kit in (answer.get("kits") or {}).items():
        if kit.get("resolved"):
            say(f"  KIT {name}: {kit.get('reference')} (source={kit.get('selection_source')}, plan={kit.get('plan_reference')})")
            if kit.get("native_preset_load") or kit.get("native_in_set_file"):
                say(f"  REAL PRESET: {'loaded into the open set through the OS' if kit.get('native_preset_load') else 'already in the set file'}; "
                    f"pads expected {kit.get('expected_pad_notes')}; nothing rebuilt, preset preserved")
                continue
            summary = kit.get("fidelity_summary") or {}
            dropped = summary.get("dropped_categories") or {}
            say(f"  PRESET PRESERVED: {kit.get('preset_preserved')}  mode={summary.get('rebuild_mode')}  "
                f"device replacements={dropped.get('device_replacements', {}).get('kinds')}  "
                f"read-not-applied={dropped.get('device_parameters', {}).get('read_not_applied')}  "
                f"macros={dropped.get('macros', {}).get('count')}  per-pad effects on {dropped.get('per_pad_effects', {}).get('pads')} pad(s)")
            say(f"  full resolution: {kit.get('overflow_path')}")
            if kit.get("rebuild_blocker"):
                say(f"  REBUILD BLOCKED: {kit['rebuild_blocker']}")
    if answer.get("status") in ("failed", "blocked") and (answer.get("stage") or answer.get("reason") or answer.get("problems")):
        say(f"  {answer['status'].upper()} at {answer.get('stage')}: {answer.get('reason') or answer.get('problems')}")
        return
    if answer.get("status") == "blocked":
        # Blocked per write, not at a stage: each write carries its own reason.
        reasons = sorted({str(w.get("reason")) for w in answer.get("writes") or [] if w.get("status") == "blocked"})
        say(f"  BLOCKED per write: {reasons}")
    for track in answer.get("tracks") or []:
        line = f"  track {track['track']:14} {str(track.get('status')):6} {str(track.get('instrument') or track.get('status'))[:34]:36}"
        if track.get("needs_preset"):
            line += f" NEEDS PRESET: {track['needs_preset'].get('family')}"
        kit_note = track.get("kit")
        if isinstance(kit_note, dict):
            line += f" kit={kit_note.get('status')} {kit_note.get('pads_requested') or kit_note.get('pads')} pads"
        if isinstance(track.get("preset"), dict):
            pr = track["preset"]
            line += f" preset={pr.get('name')} {'VERIFIED' if pr.get('verified') else 'NOT VERIFIED: ' + str(pr.get('reason'))} via {pr.get('via')}" + (f" {pr.get('seconds')}s" if pr.get("seconds") else "")
        elif kit_note:
            line += f" kit: {kit_note}"
        elif track.get("pad_notes"):
            line += f" pads={len(track['pad_notes'])} ({track.get('pad_source')})"
        if track.get("needs_preset"):
            line += f" NEEDS PRESET: {track['needs_preset']['family']}"
        say(line)
    for write in answer.get("writes") or []:
        status = write["status"]
        if status in ("OK", "would_write"):
            continue
        say(f"    {write['track']:14} {str(write.get('section')):12} {status:14} {str(write.get('reason') or write.get('error') or '')[:90]}")
    verified = [w for w in answer.get("writes") or [] if w["status"] == "OK"]
    if verified and not dry_run:
        notes = sum(int(w.get("notes") or 0) for w in verified)
        checked = sum(1 for w in verified if w.get("verified"))
        say(f"  clips {len(verified)} written, {checked} verified note-for-note by Live, {notes} notes total")
    steps = answer.get("session_steps") or []
    locators = [s for s in steps if s["kind"] == "locator"]
    if locators and not dry_run:
        say(f"  locators {sum(1 for s in locators if s.get('verified'))}/{len(locators)} verified from the read-back")


if __name__ == "__main__":
    raise SystemExit(main())
