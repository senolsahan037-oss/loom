#!/usr/bin/env python3
"""The Loom MCP server (stdio JSON-RPC).

One tool namespace over Loom's engines. Responsibilities, in file order:
  1. paths, argument validation, response discipline, resources and prompts
  2. engine handlers -- Sensei (MIDI), AIMixMaster (.als analysis and
     automation), ArrangementGPS (plans), Presetor, AISoundDesigner,
     MusicalIntelligence, Mix Check, the crate agent
  3. Live handlers -- every read or write of a running Live goes through
     bridge_client.submit_request(), the Loom extension's file bridge; there
     is no other Live endpoint
  4. project_build -- the one orchestration: plan -> validate -> gate ->
     tempo -> tracks (with target evidence) -> clips -> locators -> readback
  5. the JSON-RPC loop: concurrency, progress, cooperative cancellation

Tool names and input schemas live in tool_schemas.py; the bridge protocol
and its status vocabulary in bridge_client.py.
"""

from __future__ import annotations

import datetime
import glob
import gzip
import hashlib
import json
import base64
import contextvars
import os
import re
import shutil
import subprocess
import tempfile
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

# mcp_server is not a package: its sibling modules are imported by name.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_client  # noqa: E402
from bridge_client import BridgeTarget, BridgeUnavailable, resolve_bridge_target  # noqa: E402

LOOM_DIR = Path(__file__).resolve().parents[1]
# Optional output relocation for isolated runs; source/data roots stay fixed.
OUTPUT_ROOT = Path(os.environ.get("LOOM_OUTPUT_ROOT", str(LOOM_DIR))).expanduser().resolve()
SENSEI_DIR = LOOM_DIR / "Sensei"
AIMIXMASTER_DIR = LOOM_DIR / "AIMixMaster"
ARRANGEMENTGPS_DIR = LOOM_DIR / "ArrangementGPS"
PRESETOR_DIR = LOOM_DIR / "Presetor"
SOUNDDESIGNER_DIR = LOOM_DIR / "AISoundDesigner"
# The directory on disk is "Docs". This only ever worked because macOS is
# case-insensitive by default; on a case-sensitive volume the gap log would
# have been written to a second, invisible directory.
DOCS_DIR = LOOM_DIR / "Docs"
SENSEI_IDENTITY_PATH = SENSEI_DIR / "data" / "genre_identity" / "ableton_preset_genre_identities.jsonl"

for _module_dir in (SENSEI_DIR, AIMIXMASTER_DIR, PRESETOR_DIR, SOUNDDESIGNER_DIR):
    if str(_module_dir) not in sys.path:
        sys.path.insert(0, str(_module_dir))

GAP_LOG_PATH = OUTPUT_ROOT / "Docs" / "MISSING_CONTROLS_LOG.md"


def log_debug(msg: str) -> None:
    sys.stderr.write(f"[loom-mcp] {msg}\n")
    sys.stderr.flush()


CAMELOT_MAP = {
    ("C", "Major"): "8B", ("G", "Major"): "9B", ("D", "Major"): "10B",
    ("A", "Major"): "11B", ("E", "Major"): "12B", ("B", "Major"): "1B",
    ("F#", "Major"): "2B", ("C#", "Major"): "3B", ("G#", "Major"): "4B",
    ("D#", "Major"): "5B", ("A#", "Major"): "6B", ("F", "Major"): "7B",
    ("A", "Minor"): "8A", ("E", "Minor"): "9A", ("B", "Minor"): "10A",
    ("F#", "Minor"): "11A", ("C#", "Minor"): "12A", ("G#", "Minor"): "1A",
    ("D#", "Minor"): "2A", ("A#", "Minor"): "3A", ("F", "Minor"): "4A",
    ("C", "Minor"): "5A", ("G", "Minor"): "6A", ("D", "Minor"): "7A",
}


from tool_schemas import TOOLS  # noqa: E402


class ToolArgumentError(ValueError):
    pass


TOOL_SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in TOOLS}

_JSON_TYPES = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _check_type(name: str, value: Any, expected: str) -> None:
    python_type = _JSON_TYPES.get(expected)
    if python_type is None:
        return
    # bool is a subclass of int in Python; a boolean is not a number here.
    if expected in ("number", "integer") and isinstance(value, bool):
        raise ToolArgumentError(f"'{name}' must be {expected}, got boolean")
    if not isinstance(value, python_type):
        raise ToolArgumentError(f"'{name}' must be {expected}, got {type(value).__name__}")


def validate_arguments(tool_name: str, arguments: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ToolArgumentError("arguments must be an object")

    properties = schema.get("properties") or {}
    required = schema.get("required") or []

    missing = [name for name in required if name not in arguments or arguments[name] is None]
    if missing:
        raise ToolArgumentError(f"missing required argument(s): {', '.join(missing)}")

    unknown = [name for name in arguments if name not in properties]
    if unknown:
        raise ToolArgumentError(
            f"unknown argument(s): {', '.join(sorted(unknown))}. Valid: {', '.join(sorted(properties)) or '(none)'}"
        )

    resolved: dict[str, Any] = {}
    for name, spec in properties.items():
        if name in arguments and arguments[name] is not None:
            value = arguments[name]
            if "type" in spec:
                _check_type(name, value, spec["type"])
            if spec.get("enum") and value not in spec["enum"]:
                raise ToolArgumentError(f"'{name}' must be one of: {', '.join(map(str, spec['enum']))}")
            if spec.get("type") == "array" and isinstance(spec.get("items"), dict) and "type" in spec["items"]:
                for index, item in enumerate(value):
                    _check_type(f"{name}[{index}]", item, spec["items"]["type"])
            resolved[name] = value
        elif "default" in spec:
            resolved[name] = spec["default"]
    return resolved


# --- 3) Path restriction ---------------------------------------------------
# The previous version opened whatever als_path it was given, and the scan
# tool walked any directory handed to it. Even for a local server that is a
# prompt-injection surface: text inside a .als can steer the model.

ALLOWED_ROOTS = tuple(
    path.resolve()
    for path in (
        LOOM_DIR,
        OUTPUT_ROOT,
        Path.home() / "Desktop",
        Path.home() / "Documents",
        Path.home() / "Music",
        *([bridge_client.BRIDGE_ROOT] if bridge_client.BRIDGE_ROOT is not None else []),
    )
)
# Ev dizini altinda olsalar bile asla dolasilmayacak yerler.
DENIED_PARTS = (".ssh", ".aws", ".gnupg", ".config", "Keychains", ".password-store", ".env")


class PathNotAllowed(ValueError):
    pass


def _assert_within_allowed(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    parts = set(resolved.parts)
    denied = parts.intersection(DENIED_PARTS)
    if denied:
        raise PathNotAllowed(f"path_denied: {resolved} touches {sorted(denied)[0]}")
    for root in ALLOWED_ROOTS:
        if resolved == root or root in resolved.parents:
            return resolved
    raise PathNotAllowed(
        f"path_outside_allowed_roots: {resolved}. Allowed: {', '.join(str(root) for root in ALLOWED_ROOTS)}"
    )


def resolve_als_path(raw: str) -> Path:
    path = _assert_within_allowed(Path(raw))
    if path.suffix.lower() != ".als":
        raise PathNotAllowed(f"not_an_als_file: {path.name}")
    if not path.exists():
        raise FileNotFoundError(f"ALS file not found: {path}")
    return path


def resolve_scan_root(raw: str) -> Path:
    path = _assert_within_allowed(Path(raw))
    if not path.is_dir():
        raise PathNotAllowed(f"not_a_directory: {path}")
    return path


# --- 6) Response discipline ------------------------------------------------
# Measured: analyze_mixer 21.8 KB (~5.5K tokens), tools/list 12.6 KB, neither
# truncated. Oversized responses are written to disk and the client gets the
# head plus the path to the whole thing.
MAX_RESPONSE_CHARS = 24000
OVERFLOW_DIR = OUTPUT_ROOT / "mcp_server" / "responses"


def write_overflow(kind: str, payload: Any) -> Path:
    """The whole of a payload the answer cannot carry, as a file the client can read."""
    OVERFLOW_DIR.mkdir(parents=True, exist_ok=True)
    stem = "".join(ch if ch.isalnum() else "_" for ch in kind)[:40] or "response"
    path = OVERFLOW_DIR / f"{stem}_{int(time.time())}_{uuid.uuid4().hex[:6]}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def render_tool_text(payload: Any) -> tuple[str, str | None]:
    """The text block of a tool answer: always one complete JSON value.

    An oversized answer is not cut at the character limit -- a client
    parsing the text block would get half an object and a JSONDecodeError
    (measured 2026-09-07: project_build at 66,750 characters). It is written
    whole to the overflow directory and the text block becomes a structured
    envelope naming that file, with the status and top-level keys of the
    real answer and as much of its head as fits, inside a string.
    """
    text = json.dumps(payload, indent=2, default=str)
    if len(text) <= MAX_RESPONSE_CHARS:
        return text, None

    overflow_path = write_overflow("response", payload)
    envelope: dict[str, Any] = {
        "truncated": True,
        "overflow_path": str(overflow_path),
        "full_response_chars": len(text),
        "limit": MAX_RESPONSE_CHARS,
        "note": "The answer was over the text limit. This envelope is complete JSON; the whole answer is in overflow_path.",
    }
    if isinstance(payload, dict):
        envelope["status"] = payload.get("status")
        envelope["keys"] = sorted(str(k) for k in payload)
    envelope["preview"] = ""
    budget = MAX_RESPONSE_CHARS - len(json.dumps(envelope, indent=2)) - 64
    while budget > 0:
        envelope["preview"] = text[:budget]
        rendered = json.dumps(envelope, indent=2, default=str)
        if len(rendered) <= MAX_RESPONSE_CHARS:
            break
        budget -= max(64, len(rendered) - MAX_RESPONSE_CHARS)
    else:
        envelope["preview"] = ""
        rendered = json.dumps(envelope, indent=2, default=str)
    notice = (
        f"[truncated] Full response was {len(text)} characters, over the {MAX_RESPONSE_CHARS} limit. "
        f"Complete JSON written to {overflow_path}"
    )
    return rendered, notice


# --- 5) Resources and prompts ----------------------------------------------
# The measured datasets and the gap log are natural resources: they should be
# readable without spending a tool call, and without spending tokens.
def _evidence_resource(uri: str, name: str, description: str, measured: Path, fixture: Path) -> dict[str, Any]:
    """An evidence dataset, pointing at whatever the loaders actually read.

    The measured file is personal and never published, so a clean clone falls
    back to the synthetic fixture -- exactly as chain_evidence and
    source_evidence do. The description says which one this is, because a
    fixture presented as measurement would be a lie by omission.
    """
    using_measured = measured.exists()
    return {
        "uri": uri,
        "name": name,
        "description": description + (
            " Measured from this machine's own projects."
            if using_measured
            else " SYNTHETIC FIXTURE -- the measured file is absent, so this says nothing about anyone's projects."
        ),
        "mimeType": "application/json",
        "path": measured if using_measured else fixture,
    }


RESOURCES = [
    _evidence_resource(
        "loom://evidence/device-chains",
        "Device chain evidence",
        "Every track's device chain, with role and rack contents expanded.",
        PRESETOR_DIR / "data" / "measured_device_chains.json",
        PRESETOR_DIR / "data" / "fixture_device_chains.json",
    ),
    _evidence_resource(
        "loom://evidence/sound-sources",
        "Sound source evidence",
        "Instrument devices and the samples they load, per track.",
        SOUNDDESIGNER_DIR / "data" / "measured_sound_sources.json",
        SOUNDDESIGNER_DIR / "data" / "fixture_sound_sources.json",
    ),
    {
        "uri": "loom://docs/gap-log",
        "name": "Missing controls and gap log",
        "description": "Ableton API gaps found during development, with the workaround each one currently uses.",
        "mimeType": "text/markdown",
        "path": DOCS_DIR / "MISSING_CONTROLS_LOG.md",
    },
    {
        "uri": "loom://plan/session",
        "name": "Current session plan",
        "description": "The most recently generated ArrangementGPS session plan: tracks, roles, instruments, locators.",
        "mimeType": "application/json",
        "path": OUTPUT_ROOT / "ArrangementGPS" / "engine" / "output" / "ableton_session_plan.json",
    },
]


def list_resources() -> list[dict[str, Any]]:
    return [
        {key: value for key, value in resource.items() if key != "path"}
        for resource in RESOURCES
        if resource["path"].exists()
    ]


def read_resource(uri: Any) -> list[dict[str, Any]]:
    for resource in RESOURCES:
        if resource["uri"] == uri:
            if not resource["path"].exists():
                raise KeyError(f"resource_not_available: {uri} (no file at {resource['path']})")
            return [{
                "uri": resource["uri"],
                "mimeType": resource["mimeType"],
                "text": resource["path"].read_text(encoding="utf-8"),
            }]
    raise KeyError(f"unknown_resource: {uri}")


PROMPTS = [
    {
        "name": "build_track_from_prompt",
        "description": "Run the full ArrangementGPS chain from a musical brief, then verify the plan before touching Live.",
        "arguments": [{"name": "brief", "description": "e.g. 'dark rolling tech house, 126 bpm'", "required": True}],
        "template": (
            "Build an Ableton project plan for this brief: {brief}\n\n"
            "1. Call plan_create with the brief as the prompt.\n"
            "2. Call plan_verify and stop if it reports any failure.\n"
            "3. Report the tempo, key and genre it derived, how many tracks Sensei can generate for, "
            "and which lanes are out of scope. Do not claim anything was written into Live."
        ),
    },
    {
        "name": "audit_project",
        "description": "Read-only review of one .als: gain staging, clip alignment, automation and arrangement shape.",
        "arguments": [{"name": "als_path", "description": "Absolute path to the .als", "required": True}],
        "template": (
            "Audit this Ableton project, read-only: {als_path}\n\n"
            "Call project_analyze_mixer, project_analyze_clips, "
            "automation_read and project_inspect_arrangement. "
            "Report only what the tools actually returned, and say plainly which checks found nothing."
        ),
    },
    {
        "name": "plan_device_chains",
        "description": "Compare a project's device chains against what the user actually builds, and propose transplants.",
        "arguments": [{"name": "als_path", "description": "Absolute path to the .als", "required": True}],
        "template": (
            "Compare the device chains in {als_path} against the user's own measured habits.\n\n"
            "Call chain_plan, then chain_evidence for any role you discuss. "
            "For each empty track name the donor and the evidence percentages. "
            "Do not apply anything -- report the dry run and let the user decide."
        ),
    },
]


def list_prompts() -> list[dict[str, Any]]:
    return [
        {key: value for key, value in prompt.items() if key != "template"}
        for prompt in PROMPTS
    ]


def get_prompt(name: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    for prompt in PROMPTS:
        if prompt["name"] == name:
            missing = [
                item["name"]
                for item in prompt["arguments"]
                if item.get("required") and not arguments.get(item["name"])
            ]
            if missing:
                raise KeyError(f"missing required prompt argument(s): {', '.join(missing)}")
            text = prompt["template"].format(**{item["name"]: arguments.get(item["name"], "") for item in prompt["arguments"]})
            return {
                "description": prompt["description"],
                "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
            }
    raise KeyError(f"unknown_prompt: {name}")



# Handlers


def handle_part_suggest(args: dict[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, str(LOOM_DIR / "MusicalIntelligence"))
    from mi import compose

    if args.get("als_path"):
        context = handle_project_inspect({"als_path": args["als_path"]})
    else:
        # No file named: take the key and tempo from the running session.
        state = handle_live_state({})
        context = {"key_root": state.get("key_root") or "C",
                   "scale": state.get("scale") or "Major",
                   "tempo": state.get("tempo"), "als_path": "live session"}
    try:
        return compose.render(
            context, args["layer"],
            bars=int(args.get("bars", 8)),
            seed=int(args.get("seed", 7)),
            chords_per_bar=int(args.get("chords_per_bar", 1)),
            octave=int(args.get("octave", 3)),
            beats_per_bar=float(args.get("beats_per_bar") or 4.0),
        )
    except compose.NoEvidence as error:
        return {"layer": args["layer"], "wrote_nothing": True, "reason": str(error)}


def handle_genre_evidence(args: dict[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, str(LOOM_DIR / "MusicalIntelligence"))
    from mi import profiles

    layer = args["layer"]
    if layer == "drum":
        style = args.get("style")
        if not style:
            return {"layer": "drum", "known_styles": profiles.known_styles(),
                    "note": "Name a style to get its pattern."}
        evidence = profiles.drum_evidence(style)
        if evidence is None:
            return {"layer": "drum", "style": style, "has_evidence": False,
                    "known_styles": profiles.known_styles(),
                    "reason": "no measured pattern for this style; nothing is approximated"}
        return {"layer": "drum", "has_evidence": True, **evidence}
    if layer == "bass":
        evidence = profiles.bass_evidence()
        return {"layer": "bass", "has_evidence": evidence is not None, **(evidence or {})}
    if layer == "chord":
        chords = profiles.chord_evidence() or {}
        melody = profiles.melody_evidence() or {}
        return {"layer": "chord", "has_evidence": bool(chords), **chords,
                "melody_interval_share": melody.get("interval_share")}
    evidence = profiles.arrangement_evidence(int(args.get("song_maps") or 0))
    return {"layer": "arrangement", "has_evidence": evidence is not None, **(evidence or {})}


def handle_midi_generate(args: dict[str, Any]) -> dict[str, Any]:
    from core.midi_runtime import prepare_midi_variation

    role = args.get("role")
    beats_per_bar = float(args.get("beats_per_bar") or 4.0)
    target_context: dict[str, Any] = {}
    # What the generated part is good for is stated separately from whether
    # it was generated: an offline suggestion is not a verified Live target.
    evidence: dict[str, Any] = {"target_evidence": "offline_profile", "writable_to_live": False,
                                "writable_reason": "no Live target was verified for this part"}
    if args.get("preset_path"):
        target_context["loaded_preset_path"] = args["preset_path"]
    if args.get("explicit_profile_id"):
        target_context["explicit_profile_id"] = args["explicit_profile_id"]
    elif role in ("bass", "chord"):
        target_context["explicit_profile_id"] = _profile_for_role(role, args.get("instrument_family"))
    elif role == "drum":
        # One pad resolver decides which notes this part is written against and
        # says where they came from; the General MIDI map is a named source,
        # not a silent default, and a part built on it is not writable.
        resolved_pads = pad_notes_resolver()(args.get("pad_notes"))
        target_context["device_classes"] = ["DrumGroupDevice"]
        target_context["verified_pad_map"] = True
        target_context["verified_pad_notes"] = list(resolved_pads["value"])
        evidence = {"target_evidence": {"live_drum_rack": "live_drum_rack_pads",
                                        "preset_xml": "preset_kit_pads"}.get(resolved_pads["source"], "assumed_general_midi_pads"),
                    "pad_source": resolved_pads["source"],
                    "writable_to_live": resolved_pads["writable"],
                    "pad_notes": list(resolved_pads["value"])}
        if not resolved_pads["writable"]:
            evidence["writable_reason"] = resolved_pads["reason"]
    if role in ("bass", "chord") and args.get("instrument_verified"):
        evidence = {"target_evidence": "live_instrument_device", "writable_to_live": True}

    result = prepare_midi_variation(
        target_context=target_context,
        genre=args.get("genre", "Trap"),
        bars=int(args.get("bars", 4)),
        seed=int(args.get("seed", 42)),
        variation_amount=float(args.get("variation_amount", 0.35)),
        density=float(args["density"]) if args.get("density") is not None else None,
        genre_style=args.get("genre_style") or None,
        target_root=args.get("target_root", "C"),
        target_mode=args.get("target_mode", "Minor"),
        beats_per_bar=beats_per_bar,
    )
    result.update(evidence)
    result["beats_per_bar"] = beats_per_bar

    if args.get("auto_write_to_live"):
        if not (result.get("generation_safe") and result.get("payload")):
            result["bridge_write_status"] = {"status": "NOT_WRITTEN", "reason": result.get("error")}
        elif not evidence["writable_to_live"]:
            result["bridge_write_status"] = {"status": "BLOCKED", "reason": evidence.get("writable_reason")}
        else:
            payload = result["payload"]
            clip_name = f"Sensei {args.get('genre', 'Var')} {role or ''}".strip()
            result["bridge_write_status"] = handle_midi_write_to_live({
                "name": clip_name,
                "notes": [{"pitch": n["pitch"], "start": n.get("time", n.get("start", 0.0)),
                           "duration": n["duration"], "velocity": n.get("velocity", 100)} for n in payload.get("notes", [])],
                "length_beats": float(payload.get("clip_length") or int(args.get("bars", 4)) * beats_per_bar),
                "track": args.get("track"),
                "prompt": f"Auto-write variation: {args.get('genre')} {role}",
            })
    return result


def handle_midi_write_to_live(args: dict[str, Any]) -> dict[str, Any]:
    """The legacy session-clip writer, on the common protocol: one request
    file with op=write_clip, answered by the extension like every other op."""
    payload: dict[str, Any] = {
        "op": "write_clip",
        "name": args.get("name", "Sensei MCP Clip"),
        "notes": args.get("notes", []),
        "length_beats": float(args.get("length_beats", 16.0)),
        "prompt": args.get("prompt", "Generated by Loom MCP"),
    }
    for key in ("track", "slot", "on_conflict"):
        if args.get(key) is not None:
            payload[key] = args[key]
    answer = bridge_client.submit_request(payload, float(args.get("wait_seconds", 15)))
    answer["status"] = {"OK": "WRITTEN_TO_LIVE", "REFUSED_IN_LIVE": "REJECTED_BY_LIVE"}.get(answer.get("status"), answer.get("status"))
    answer["note_count"] = len(payload["notes"])
    answer["length_beats"] = payload["length_beats"]
    if answer["status"] in ("REJECTED_BY_LIVE", "FAILED_IN_LIVE"):
        answer["error_detail"] = answer.get("error")
    return answer


def handle_live_state(args: dict[str, Any]) -> dict[str, Any]:
    """Live's current state as the Loom extension publishes it."""
    max_age = float(args.get("max_age_seconds", 10))
    try:
        target = resolve_bridge_target()
    except BridgeUnavailable as error:
        return {"available": False, "reason": error.status, "message": str(error), "candidates": error.candidates}
    state_file = target.state_file
    if args.get("refresh", True):
        # Ask Live for a fresh dump; if Live is closed, whatever is on disk is
        # read instead and its staleness is stated outright.
        answer = bridge_client.submit_request({"op": "get_state", "include_devices": bool(args.get("include_devices", True))},
                                              float(args.get("wait_seconds", 3)), target=target)
        fresh = answer.get("result") if isinstance(answer.get("result"), dict) else None
        if fresh and fresh.get("tracks") is not None:
            fresh = dict(fresh)
            fresh["available"] = True
            fresh["state_file"] = str(state_file)
            fresh["state_source"] = "get_state_answer"
            fresh["age_seconds"] = 0.0
            fresh["is_fresh"] = True
            fresh["set"] = _open_set_info()
            fresh["bridge"] = target.describe()
            return fresh

    if not state_file.exists():
        return {
            "available": False,
            "state_file": str(state_file),
            "reason": "no_state_published_yet",
            "message": "The Loom extension has never published state here. Live may not be running, or the "
                       "extension is not loaded in it.",
            "bridge": target.describe(),
        }
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001
        return {"available": False, "state_file": str(state_file), "reason": "state_unreadable", "message": str(error)}
    captured_at = float(state.get("captured_at") or 0)
    age = time.time() - captured_at if captured_at else None
    state["available"] = True
    state["state_file"] = str(state_file)
    state["state_source"] = "state_file"
    state["age_seconds"] = round(age, 2) if age is not None else None
    state["is_fresh"] = bool(age is not None and age <= max_age)
    state["bridge"] = target.describe()
    if not state["is_fresh"]:
        state["warning"] = (
            f"State is {state['age_seconds']}s old (limit {max_age}s). Treat it as a last-known snapshot, "
            "not as what Live shows right now."
        )
    return state


def handle_live_project(args: dict[str, Any]) -> dict[str, Any]:
    """Open / inspect / close a Live project, verified from Live's own log."""
    import live_project as lp  # noqa: PLC0415  (mcp_server is not a package)

    op = args.get("op")
    if op == "status":
        return lp.status()
    if op == "quit":
        return lp.quit_live(float(args.get("wait_seconds", 8)))
    if op == "open":
        als = args.get("als_path")
        if not als:
            return {"opened": False, "error": "als_path is required for op=open"}
        return lp.open_project(str(als), float(args.get("wait_seconds", 30)),
                               bool(args.get("allow_switch", False)))
    return {"error": f"unknown op: {op}"}




def _beats_per_bar(args: dict[str, Any]) -> tuple[float, str]:
    """How many beats a bar has, and where that number came from.

    The Extensions SDK exposes no song time signature (GAP-003), so the
    running session cannot be asked. An explicit value wins, then a state that
    happens to carry a signature, then the project file, and only then 4/4,
    which is reported as an assumption rather than passed off as a reading.
    """
    if args.get("beats_per_bar"):
        return float(args["beats_per_bar"]), "explicit"
    try:
        state = handle_live_state({"refresh": False, "max_age_seconds": 120})
        numerator = state.get("signature_numerator")
        denominator = state.get("signature_denominator") or 4
        if state.get("available") and state.get("is_fresh") and numerator:
            return float(numerator) * 4.0 / float(denominator), "live_session"
    except Exception:  # noqa: BLE001 -- a stale or missing state is not an error here
        pass
    if args.get("als_path"):
        try:
            return float(handle_project_inspect_arrangement({"als_path": args["als_path"]})["beats_per_bar"]), "als"
        except Exception:  # noqa: BLE001
            pass
    return 4.0, "assumed_4_4"


def write_arrangement_clip(args: dict[str, Any], *, target: BridgeTarget | None = None,
                           idempotency_key: str | None = None) -> dict[str, Any]:
    """One arrangement clip through the extension. `target` and the key are
    internal context (a build holds one resolved target for all its writes);
    they never travel inside the user-facing argument dictionary."""
    beats_per_bar, bpb_source = _beats_per_bar(args)
    if "start_beat" in args:
        start_beat = float(args["start_beat"])
    else:
        # Bars are 1-based in every plan this repo writes; beat 0 is bar 1.
        start_beat = (int(args.get("start_bar", 1)) - 1) * beats_per_bar
    payload: dict[str, Any] = {
        "op": "write_arrangement_clip",
        "start_beat": start_beat,
        "length_beats": float(args.get("length_beats", 16.0)),
        "name": str(args.get("name") or "Loom"),
        "notes": args.get("notes") or [],
    }
    if args.get("track"):
        payload["track"] = args["track"]
    if args.get("on_conflict"):
        payload["on_conflict"] = args["on_conflict"]
    response = bridge_client.submit_request(payload, float(args.get("wait_seconds", 15)),
                                            target=target, idempotency_key=idempotency_key)
    response["beats_per_bar"] = beats_per_bar
    response["beats_per_bar_source"] = bpb_source
    return response


def handle_midi_write_arrangement(args: dict[str, Any]) -> dict[str, Any]:
    return write_arrangement_clip(args, idempotency_key=args.get("idempotency_key"))



# Instrument profile ids that actually exist in Sensei's catalogue
# (Sensei/data/instrument_capabilities). The old chord default,
# "ableton.chord.polyphonic.v1", never did -- every chord write was blocked
# with explicit_profile_unknown until 2026-09-03.
_CHORD_FAMILY_PROFILES = [
    (("electric piano", "e-piano", "rhodes", "wurli", "daze"), "ableton.chord.electric-piano.v1"),
    (("organ",), "ableton.chord.organ.v1"),
    (("clav",), "ableton.chord.clav.v1"),
    (("pad", "string", "ambient", "texture"), "ableton.chord.pad.v1"),
    (("piano", "grand", "keys"), "ableton.chord.piano.v1"),
    (("synth", "lead", "poly"), "ableton.chord.synth-keys.v1"),
]
_BASS_FAMILY_PROFILES = [
    (("808", "sub"), "ableton.bass.808.v1"),
    (("upright", "double bass", "acoustic"), "ableton.bass.upright.v1"),
    (("electric", "finger", "pick", "slap"), "ableton.bass.electric.v1"),
    (("mono",), "ableton.bass.monophonic.v1"),
    (("synth", "analog", "reese", "acid"), "ableton.bass.synth.v1"),
]
_ROLE_DEFAULT_PROFILE = {"bass": "ableton.bass.synth.v1", "chord": "ableton.chord.piano.v1"}


def _profile_for_role(role: str, instrument_family: str | None = None) -> str | None:
    """Pick a real profile id for a role from the plan's instrument family name.
    Drums are resolved from the verified device, never from a default."""
    table = {"chord": _CHORD_FAMILY_PROFILES, "bass": _BASS_FAMILY_PROFILES}.get(role)
    if table is None:
        return None
    family = (instrument_family or "").lower()
    for keywords, profile_id in table:
        if family and any(word in family for word in keywords):
            return profile_id
    return _ROLE_DEFAULT_PROFILE[role]


def _create_midi_track_request(name: str, instrument_family: str | None, wait: float,
                               target: BridgeTarget | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    """create_midi_track through the extension. The SDK can make the track and
    insert a native device with its default preset; a browser preset cannot be
    loaded and the answer says so -- nothing else is asked to do it."""
    return bridge_client.submit_request({"op": "create_midi_track", "name": name, "instrument_family": instrument_family},
                                        wait, target=target, idempotency_key=idempotency_key)


def _catalog_entry(name: str, role: str | None = None) -> dict[str, Any] | None:
    """The identity-catalogue entry for a preset name (first whose file exists)."""
    wanted = str(name or "").strip().lower().removesuffix(".adg").removesuffix(".adv")
    if not wanted:
        return None
    for entry in _load_sensei_identities():
        if role and entry.get("role") != role:
            continue
        if str(entry.get("normalized_name") or "").lower() == wanted and Path(str(entry.get("path") or "")).is_file():
            return entry
    return None


def resolve_kit_reference(reference: str) -> dict[str, Any]:
    """A Drum Rack preset -> pads the extension can rebuild, with what the SDK
    path drops stated. Raises KitResolveError when nothing matches."""
    from ableton.kit_resolver import resolve_kit  # noqa: PLC0415  (Sensei owns the .adg reading)

    return resolve_kit(reference, _load_sensei_identities())


def _kit_pads_payload(kit: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"note": p["note"], "sample": p["sample"], "name": p["name"]} for p in kit["pads"]]


def _kit_answer(kit: dict[str, Any], *, include_fidelity: bool = True) -> dict[str, Any]:
    """What a tool answer says about a resolved kit.

    The full resolution (every pad, every device parameter read from the
    preset) goes to the overflow directory and is referenced by path; the
    answer keeps the facts a caller decides on and a fidelity summary that
    names every loss category. Measured before this: one 16-pad kit put 15 KB
    into project_build three times over, the answer passed MAX_RESPONSE_CHARS
    and the client received cut-off, unparseable JSON.
    """
    from ableton.kit_resolver import fidelity_summary  # noqa: PLC0415  (Sensei owns the kit facts)

    try:
        overflow = str(write_overflow(f"kit_{kit.get('kit') or 'kit'}", kit))
    except OSError as error:
        overflow = f"not written: {error}"
    answer = {**{k: kit.get(k) for k in ("kit", "path", "pad_count", "missing", "sample_states", "profile_write_safety")},
              "pads_resolved": len(kit.get("pads") or []), "overflow_path": overflow}
    if include_fidelity:
        # Only the sample-reconstruction op (live_command build_drum_kit) loses fidelity; say what.
        answer.update({"preset_preserved": False, "fidelity_summary": fidelity_summary(kit)})
    return answer


def _kit_rebuild_problem(kit: dict[str, Any], allow_lossy: bool) -> str | None:
    if not kit.get("pads") or kit.get("missing"):
        return "kit_samples_missing"
    if not allow_lossy:
        return "kit_rebuild_requires_consent"
    return None


# The pad vocabulary -- which note is which drum role, the General MIDI map,
# and how generated notes reach a kit whose pads sit elsewhere -- lives with
# the kit reader (Sensei/ableton/kit_resolver.py). These are thin accessors so
# the MCP has one import site and no second copy of the table.
def pad_notes_resolver():
    from ableton.kit_resolver import resolve_pad_notes  # noqa: PLC0415

    return resolve_pad_notes


def kit_pad_mapping(kit_pads: list[dict[str, Any]]) -> dict[str, Any]:
    from ableton.kit_resolver import pad_mapping  # noqa: PLC0415

    return pad_mapping(kit_pads)


def _journal_import(args: dict[str, Any], wait: float) -> dict[str, Any]:
    """Carry the replay journal of an earlier extension id into the current
    bridge, one journal_import request per legacy root."""
    roots = [(Path(str(args["source"])).name, Path(str(args["source"])).expanduser())] if args.get("source") else bridge_client.legacy_bridge_roots()
    imports = []
    for ext_id, root in roots:
        entries = bridge_client.legacy_journal_entries(root)
        if not entries:
            imports.append({"source": ext_id, "root": str(root), "status": "NOTHING_TO_IMPORT", "entries": 0})
            continue
        answer = bridge_client.submit_request({"op": "journal_import", "source": ext_id, "entries": entries}, wait)
        imports.append({"source": ext_id, "root": str(root), "entries": len(entries), **{k: answer.get(k) for k in ("status", "result", "error", "outcome")}})
    overall = "OK" if imports and all(i["status"] in ("OK", "NOTHING_TO_IMPORT") for i in imports) else (imports[-1]["status"] if imports else "NOTHING_TO_IMPORT")
    return {"status": overall, "op": "journal_import", "imports": imports,
            "note": "imported entries belong to other Live sessions: their keys are refused here (replay_refused_other_session / indeterminate_earlier_attempt), never replayed or re-applied"}


def handle_live_command(args: dict[str, Any]) -> dict[str, Any]:
    operation = args["op"]
    wait = float(args.get("wait_seconds", 15))
    if operation == "create_midi_track":
        return _create_midi_track_request(str(args.get("name") or ""), args.get("instrument_family"), wait)
    if operation == "journal_import":
        return _journal_import(args, wait)
    kit_info: dict[str, Any] | None = None
    if operation == "build_drum_kit" and args.get("kit"):
        try:
            kit_info = resolve_kit_reference(str(args["kit"]))
        except Exception as error:  # noqa: BLE001 -- KitResolveError or an unreadable preset
            return bridge_client.refusal("REFUSED_IN_LIVE", f"kit_unresolved: {error}", code="kit_unresolved", op=operation)
        problem = _kit_rebuild_problem(kit_info, args.get("allow_lossy_kit") is True)
        if problem:
            return bridge_client.refusal("REFUSED_IN_LIVE", problem,
                                         code=problem, op=operation, kit=kit_info)
        args = {**args, "pads": _kit_pads_payload(kit_info)}
    payload: dict[str, Any] = {"op": operation}
    for key in ("track", "device", "parameter", "value", "bpm", "volume", "pan", "mute", "solo",
                "action", "position", "beat", "name", "include_devices", "instrument_family", "root", "mode",
                "path", "slot", "start_beat", "end_beat", "duration_beats", "warped", "pads"):
        if key in args:
            payload[key] = args[key]
    if operation == "build_drum_kit":
        # The extension cannot read outside its storage; the MCP checks the
        # files exist before Live is asked to load them.
        missing = [str(p.get("sample")) for p in (args.get("pads") or []) if not Path(str(p.get("sample") or "")).is_file()]
        if missing:
            return bridge_client.refusal("REFUSED_IN_LIVE", f"sample files not found: {missing}", code="sample_missing", op=operation)
    answer = bridge_client.submit_request(payload, wait)
    if kit_info is not None:
        answer["kit"] = {**_kit_answer(kit_info), "pads_requested": len(kit_info["pads"])}
    return answer


def handle_project_inspect(args: dict[str, Any]) -> dict[str, Any]:
    als_path = resolve_als_path(args["als_path"])

    with gzip.open(str(als_path), "rb") as f:
        root = ET.parse(f).getroot()

    creator = root.attrib.get("Creator", "Unknown")

    # Tempo, key and track names come from the one .als reader
    # (aimixmaster.project_analyzer), so this tool and every scan report the
    # same fact for the same file. Each answer says where its value came from.
    from aimixmaster.project_analyzer import musical_context, resolve_track_name  # noqa: PLC0415

    context = musical_context(root)
    tempo_resolved, key_resolved = context["tempo"], context["key"]
    tempo = tempo_resolved.value if tempo_resolved.value is not None else 120.0
    key = key_resolved.value or {}
    root_name = key.get("root") or ""
    # No ScaleInformation in the set is the common case: reported as unknown
    # rather than presented as C major.
    scale_title = key.get("scale") or ("Major" if root_name else "")
    camelot = CAMELOT_MAP.get((root_name, scale_title), "Unknown")

    # Track breakdown
    track_tags = ["AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack", "MainTrack"]
    track_counts = Counter()
    tracks_list = []

    for elem in root.iter():
        if elem.tag in track_tags:
            track_counts[elem.tag] += 1
            resolved_name = resolve_track_name(elem)
            tname = resolved_name.value or elem.tag

            mute_node = elem.find(".//Speaker/Manual")
            is_muted = mute_node.attrib.get("Value", "true") == "false" if mute_node is not None else False

            # Device search
            devices = []
            for dev in elem.iter():
                if dev.tag.endswith("Device") or dev.tag in ("Eq8", "Compressor2", "Saturator", "GlueCompressor", "Reverb", "Delay"):
                    d_name = (dev.find("./UserName") or ET.Element("")).attrib.get("Value") or dev.tag
                    devices.append(d_name)

            tracks_list.append({
                "type": elem.tag,
                "name": tname,
                "name_source": resolved_name.source or "track_tag",
                "is_muted": is_muted,
                "device_count": len(devices),
                "devices": devices[:5]
            })

    return {
        "als_path": str(als_path),
        "creator": creator,
        "tempo": tempo,
        "tempo_source": tempo_resolved.source or "assumed_120",
        "key_root": root_name or "Unknown",
        "scale": scale_title or "Unknown",
        "key_source": key_resolved.source or "absent_in_set",
        "camelot": camelot,
        "track_counts": dict(track_counts),
        "total_tracks": sum(track_counts.values()),
        "tracks": tracks_list
    }


_NAME_ROLE_WORDS = {"kick": ("kick", "bd"), "snare": ("snare", "clap", "sd", "snr"), "hat": ("hat", "hh", "hihat"),
                    "bass": ("bass", "sub", "808"), "fx": ("fx", "glitch", "riser", "impact")}
_ROLE_TO_FEATURE = {"kick": "kick", "snare": "snare", "clap": "snare", "rim": "snare", "closed_hat": "hat", "open_hat": "hat"}


def role_evidence(root: ET.Element) -> dict[str, Any]:
    """Which drum/bass roles a set holds, from three NAMED sources per track:
    the track name (words), the sample file names on the track (Sensei's role
    vocabulary, the one owner), and the MIDI pitches played into a Drum Rack
    (the General MIDI map, same owner). A Drum Rack track called "Kit" with
    a kick sample and notes on 36 counts as a kick; the old name-only reading
    counted it as nothing (Diplomat, 2026-09-07: kick/snare/hat = 0)."""
    from ableton.kit_resolver import GM_PAD_ROLES, resolve_pad_role  # noqa: PLC0415  (Sensei owns the role vocabulary)

    features: dict[str, dict[str, Any]] = {f: {"tracks": [], "sources": Counter(), "examples": []} for f in ("kick", "snare", "hat", "bass", "fx")}

    def hit(feature: str, track: str, source: str, example: str | None = None) -> None:
        if track not in features[feature]["tracks"]:
            features[feature]["tracks"].append(track)
        features[feature]["sources"][source] += 1
        if example and example not in features[feature]["examples"] and len(features[feature]["examples"]) < 5:
            features[feature]["examples"].append(example)

    tracks_node = root.find("LiveSet/Tracks")
    for track in (tracks_node if tracks_node is not None else []):
        if track.tag not in ("MidiTrack", "AudioTrack"):
            continue
        name_node = track.find("Name/UserName")
        eff = track.find("Name/EffectiveName")
        name = (name_node.get("Value") if name_node is not None and name_node.get("Value") else (eff.get("Value") if eff is not None else "")) or track.tag
        words = re.sub(r"[_\-]", " ", name.lower())
        for feature, needles in _NAME_ROLE_WORDS.items():
            if any(re.search(r"\b" + re.escape(w) + r"\b", words) for w in needles):
                hit(feature, name, "track_name")
        # sample file names anywhere on the track (instrument or clips)
        seen: set[str] = set()
        for ref in track.iter("FileRef"):
            rel = ref.find("RelativePath")
            value = rel.get("Value") if rel is not None else None
            if not value or value.endswith((".adg", ".adv", ".alp")) or value in seen:
                continue
            seen.add(value)
            stem = Path(value).stem
            role = resolve_pad_role("", None, value)["value"]
            if role in _ROLE_TO_FEATURE:
                hit(_ROLE_TO_FEATURE[role], name, "sample_name", stem)
            elif role == "unknown_pad" and re.search(r"\b(808|bass|sub)\b", stem.lower()) and not re.search(r"\bbass\s*drum\b", stem.lower()):
                hit("bass", name, "sample_name", stem)
        # notes played into a Drum Rack: pitch -> General MIDI role
        if track.tag == "MidiTrack" and track.find(".//DrumGroupDevice") is not None:
            pitches = {int(k.find("MidiKey").get("Value")) for k in track.findall("DeviceChain/MainSequencer/ClipTimeable/ArrangerAutomation/Events/MidiClip//KeyTrack")
                       if k.find("MidiKey") is not None and k.find("Notes/MidiNoteEvent") is not None}
            for pitch in pitches:
                role = GM_PAD_ROLES.get(pitch)
                if role in _ROLE_TO_FEATURE:
                    hit(_ROLE_TO_FEATURE[role], name, "drum_rack_midi_notes", f"pitch {pitch} ({role})")
    return {f: {"count": len(v["tracks"]), "tracks": v["tracks"], "sources": dict(v["sources"]), "examples": v["examples"]} for f, v in features.items()}


def handle_project_detect_genre(args: dict[str, Any]) -> dict[str, Any]:
    info = handle_project_inspect(args)
    tracks = info.get("tracks", [])
    total = len(tracks)

    with gzip.open(str(resolve_als_path(args["als_path"])), "rb") as handle:
        evidence = role_evidence(ET.fromstring(handle.read()))
    kick, snare, hat, bass, fx = (evidence[f]["count"] for f in ("kick", "snare", "hat", "bass", "fx"))

    tempo = float(info["tempo"]) if isinstance(info["tempo"], (int, float)) else 120.0

    scores: dict[str, float] = {
        "Trap": 0.0,
        "Boom Bap / Hip Hop": 0.0,
        "House": 0.0,
        "Techno": 0.0,
        "Drum & Bass": 0.0
    }

    if 130 <= tempo <= 165 and bass > 0:
        scores["Trap"] += 0.6
    if 80 <= tempo <= 100 and snare > 0:
        scores["Boom Bap / Hip Hop"] += 0.7
    if 120 <= tempo <= 128 and kick > 0 and hat > 0:
        scores["House"] += 0.7
    if 128 <= tempo <= 145 and kick > 0:
        scores["Techno"] += 0.6
    if 168 <= tempo <= 180:
        scores["Drum & Bass"] += 0.8

    predicted = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    return {
        "als_path": info["als_path"],
        "evidence": evidence,
        "note": "a heuristic over tempo and role evidence (track names, sample names, Drum Rack MIDI pitches), not a classification",
        "tempo": tempo,
        "feature_counts": {"kick": kick, "snare": snare, "hat": hat, "bass": bass, "fx": fx, "total_tracks": total},
        "genre_ranking": [{"genre": g, "confidence": round(s, 2)} for g, s in predicted if s > 0]
    }


def handle_project_analyze_mixer(args: dict[str, Any]) -> dict[str, Any]:
    # This used to count track types and return a hardcoded -6.0 dB "target".
    # AIMixMaster's own gain staging analysis reads real fader values, Utility
    # gains, routing and master-chain processors, so that is what runs now.
    # The audio-measurement half of that module needs soundfile and is not
    # used here -- this is the pure-XML analysis.
    from aimixmaster.als_io import load_als
    from aimixmaster.gain_staging import analyze_gain_staging, markdown_report

    als_path = resolve_als_path(args["als_path"])

    root = load_als(als_path).getroot()
    report = analyze_gain_staging(root)
    data = report.as_dict()

    tracks = data["tracks"]
    flagged = [
        {
            "track": t["track_name"],
            "type": t["track_type"],
            "parent_bus": t["parent_bus"],
            "fader_db": t["current_fader_db"],
            "utility_gain_db": t["utility_gain_db"],
            "reason": t["reason"],
            "warnings": t["warnings"],
        }
        for t in tracks
        if t["warnings"] or (t["current_fader_db"] is not None and t["current_fader_db"] > 0)
    ]

    info = handle_project_inspect(args)
    all_tracks = info.get("tracks", [])
    return {
        "als_path": str(als_path),
        "schema_version": data["schema_version"],
        "mixer_summary": {
            "group_buses": [t["name"] for t in all_tracks if t["type"] == "GroupTrack"],
            "return_tracks": [t["name"] for t in all_tracks if t["type"] == "ReturnTrack"],
            "audio_track_count": sum(1 for t in all_tracks if t["type"] == "AudioTrack"),
            "midi_track_count": sum(1 for t in all_tracks if t["type"] == "MidiTrack"),
        },
        "master": data["master"],
        "track_count": len(tracks),
        "tracks_needing_attention": flagged,
        "markdown": markdown_report(report),
        "note": "Gain staging here is read from the project's XML only. Peak/RMS/LUFS targets require rendered audio and the soundfile dependency.",
    }


def handle_project_analyze_clips(args: dict[str, Any]) -> dict[str, Any]:
    from aimixmaster.als_io import load_als
    from aimixmaster.clip_alignment import analyze_clip_alignment, markdown_clip_alignment

    als_path = resolve_als_path(args["als_path"])

    root = load_als(als_path).getroot()
    report = analyze_clip_alignment(
        root,
        als_path,
        limit_db=float(args.get("limit_db", 12.0)),
        threshold_db=float(args.get("threshold_db", 0.25)),
    )
    report["markdown"] = markdown_clip_alignment(report)
    report["als_path"] = str(als_path)
    return report


def handle_automation_read(args: dict[str, Any]) -> dict[str, Any]:
    # Read-only by design: nothing in the stack can write an automation
    # envelope yet (GAP-002 territory), so this reports what exists rather
    # than pretending it can change it.
    import als_automation_inspector

    als_path = resolve_als_path(args["als_path"])

    report = als_automation_inspector.collect_automation(str(als_path))
    report["als_path"] = str(als_path)
    report["write_supported"] = False
    return report


def handle_automation_write(args: dict[str, Any]) -> dict[str, Any]:
    from aimixmaster.als_io import load_als, save_als_atomic
    from aimixmaster.automation_writer import (
        normalise_points,
        read_automation,
        resolve_target,
        verify_automation,
        write_automation,
    )
    from aimixmaster.project_analyzer import iter_tracks, track_name as display_name

    als_path = resolve_als_path(args["als_path"])
    track_name = args["track"]
    parameter = args.get("parameter", "")
    pointee_id = args.get("pointee_id", "")
    if not parameter and not pointee_id:
        raise ValueError("need either 'parameter' (volume/pan) or 'pointee_id' from automation_list_targets")
    points = args["points"]
    unit = args.get("unit", "native")
    replace = bool(args.get("replace", False))
    apply_changes = bool(args.get("apply", False))

    tree = load_als(als_path)
    root = tree.getroot()
    matches = [track for track in iter_tracks(root) if display_name(track) == track_name]
    if len(matches) != 1:
        raise ValueError(f"Expected one track named {track_name!r}, found {len(matches)}")
    track = matches[0]

    target = resolve_target(track, parameter, pointee_id)
    normalised = normalise_points(points, target, unit)
    existing = read_automation(track, parameter, pointee_id)

    response: dict[str, Any] = {
        "als_path": str(als_path),
        "track": track_name,
        "parameter": target.parameter,
        "pointee_id": target.pointee_id,
        "parameter_range": [target.minimum, target.maximum],
        "current_manual_value": target.manual,
        "existing_point_count": len(existing),
        "requested_points": [{"time": time, "value": value} for time, value in normalised],
        "applied": False,
        "backup_path": None,
    }

    if not apply_changes:
        response["status"] = "READY"
        response["message"] = "Dry run only: values validated against the parameter's own range. Call again with apply=true to write."
        if existing and not replace:
            response["status"] = "BLOCKED"
            response["message"] = f"{parameter} already has {len(existing)} automation points. Pass replace=true to overwrite."
        return response

    result = write_automation(track, parameter, points, unit=unit, replace=replace, track_name=track_name, pointee_id=pointee_id)

    backup_path = als_path.with_suffix(f".mcp_backup_{int(time.time())}.als")
    shutil.copy2(als_path, backup_path)
    save_als_atomic(tree, als_path)

    reloaded = load_als(als_path).getroot()
    written_track = [t for t in iter_tracks(reloaded) if display_name(t) == track_name][0]
    verify_automation(written_track, parameter, normalised, pointee_id=pointee_id)

    response["applied"] = True
    response["replaced_existing"] = result.replaced
    response["backup_path"] = str(backup_path)
    response["written_points"] = [{"time": time, "value": value} for time, value in read_automation(written_track, parameter, pointee_id)]
    response["status"] = "WRITTEN_AND_VERIFIED"
    return response


def handle_automation_list_targets(args: dict[str, Any]) -> dict[str, Any]:
    from aimixmaster.als_io import load_als
    from aimixmaster.automation_writer import list_automatable_parameters
    from aimixmaster.project_analyzer import iter_tracks, track_name as display_name

    als_path = resolve_als_path(args["als_path"])
    track_name = args["track"]
    root = load_als(als_path).getroot()
    matches = [track for track in iter_tracks(root) if display_name(track) == track_name]
    if len(matches) != 1:
        raise ValueError(f"Expected one track named {track_name!r}, found {len(matches)}")

    parameters = list_automatable_parameters(matches[0])
    writable = [item for item in parameters if item["min"] is not None and item["max"] is not None]

    # A single EQ Eight carries 85 parameters and one track easily passes 600.
    # Returning them all truncates the response, so filtering and the limit
    # live in the tool: the caller narrows by the name summary first.
    scope = args.get("scope")
    contains = (args.get("contains") or "").lower()
    limit = int(args.get("limit", 50))

    selected = writable
    if scope:
        selected = [item for item in selected if item["scope"] == scope]
    if contains:
        selected = [item for item in selected if contains in item["tag"].lower()]

    by_tag = Counter(item["tag"] for item in writable)
    return {
        "als_path": str(als_path),
        "track": track_name,
        "total": len(parameters),
        "writable": len(writable),
        "matched": len(selected),
        "returned": min(len(selected), limit),
        "parameter_names": [{"tag": tag, "count": count} for tag, count in by_tag.most_common(40)],
        "parameters": selected[:limit],
        "note": "Names are XML tags, not Live's display names. A parameter without a declared "
                "MidiControllerRange is excluded rather than written with guessed bounds. "
                "Narrow with 'contains' or 'scope' before raising 'limit'.",
    }


def handle_drumbuss_read(args: dict[str, Any]) -> dict[str, Any]:
    from aimixmaster.als_io import load_als
    from aimixmaster.drum_buss_parameters import (
        read_drum_buss_parameter_state,
        verify_conservative_drum_buss_parameters,
    )

    als_path = resolve_als_path(args["als_path"])

    root = load_als(als_path).getroot()
    try:
        state = read_drum_buss_parameter_state(root)
    except ValueError as error:
        # DrumBussParameterError subclasses ValueError, and find_unique_track
        # raises a plain ValueError when the project simply has no DRUM BUSS
        # track. "This project has no drum buss" is an answer, not a failure.
        return {"als_path": str(als_path), "has_drum_buss": False, "detail": str(error)}

    try:
        verify_conservative_drum_buss_parameters(root)
        conservative = True
        detail = "Parameters match the conservative drum buss preset."
    except ValueError as error:
        conservative = False
        detail = str(error)

    return {
        "als_path": str(als_path),
        "has_drum_buss": True,
        "matches_conservative_preset": conservative,
        "detail": detail,
        "parameter_state": state,
    }


def _run_node(script: str, *script_args: str, env: dict[str, str] | None = None) -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node_not_found: Node.js is required to run the ArrangementGPS chain.")
    result = subprocess.run(
        [node, script, *script_args],
        cwd=str(ARRANGEMENTGPS_DIR),
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, **(env or {})},
    )
    if result.returncode != 0:
        raise RuntimeError(f"{Path(script).name} failed: {(result.stderr or result.stdout).strip()[:400]}")
    return result.stdout.strip()


# The previous version of this tool did not call ArrangementGPS at all -- it
# wrote whatever the caller handed it into a JSON file, so an LLM that passed
# no tracks produced a 349-byte "plan" with an empty track list while the real
# engine sat unused. This runs the actual chain end to end.
# Every run of the chain gets its own directory: the stages read and write
# only inside it (ARRANGEMENTGPS_OUTPUT_DIR / ARRANGEMENTGPS_BUILDS_DIR), so two
# builds started together never see each other's files, and project_build
# reads the plan of the run it started, not "the last plan".
ARRANGEMENTGPS_RUNS_DIR = OUTPUT_ROOT / "ArrangementGPS" / "engine" / "runs"
CHAIN_STEPS = [
    ("engine/run.js", "blueprint"),
    ("engine/builder/createBuildPlan.js", "build_plan"),
    ("engine/builder/createSessionPlan.js", "session_plan"),
    ("engine/ableton_builder/createProjectPackage.js", "package"),
    ("engine/ableton_builder/createActionList.js", "action_list"),
]



_KEY_NAMES = {"C": "C", "C#": "C#", "DB": "C#", "D": "D", "D#": "D#", "EB": "D#", "E": "E", "F": "F",
              "F#": "F#", "GB": "F#", "G": "G", "G#": "G#", "AB": "G#", "A": "A", "A#": "A#", "BB": "A#", "B": "B"}


def _plan_key(text: str) -> tuple[str, str]:
    """'D Minor' -> ('D', 'Minor'); anything unreadable falls back to C Minor and says so via the caller."""
    parts = (text or "").replace("-", " ").split()
    root = _KEY_NAMES.get((parts[0] if parts else "C").upper().replace("♭", "B").replace("♯", "#"), "C")
    mode = "Major" if any(p.lower().startswith("maj") for p in parts[1:]) else "Minor"
    return root, mode


def _track_plays_in(track: dict[str, Any], section: dict[str, Any]) -> bool:
    for region in track.get("mute_regions") or []:
        if section["start_bar"] >= region.get("start_bar", 1) and section["end_bar"] <= region.get("end_bar", 0):
            return False
    return True


BUILD_REQUIRED_CAPABILITIES = ("tracks", "arrangement_clips", "locators")
# Answers after which further writes are pointless or unsafe: the bridge is
# not answering, cannot be trusted, or is not one this client may mutate.
BRIDGE_DEAD_STATUSES = bridge_client.DEAD_STATUSES


def _plan_track_name(track: dict[str, Any]) -> str:
    return str(track.get("ableton_name") or track.get("display_name") or track.get("name") or "").strip()


def _validate_plan(plan: dict[str, Any]) -> list[str]:
    """Everything the build would rely on, checked before any mutation."""
    problems: list[str] = []
    project = plan.get("project")
    if not isinstance(project, dict):
        problems.append("plan has no project object")
        project = {}
    bpm = project.get("bpm")
    if bpm is not None and not (isinstance(bpm, (int, float)) and 20 <= float(bpm) <= 999):
        problems.append(f"bpm {bpm!r} is not a usable tempo")
    sections = plan.get("locators") or []
    if not sections:
        problems.append("plan has no sections (locators)")
    for index, section in enumerate(sections):
        try:
            start, end = int(section["start_bar"]), int(section["end_bar"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"section {index} lacks integer start_bar/end_bar")
            continue
        if start < 1 or end < start:
            problems.append(f"section {section.get('name', index)!r} spans bars {start}-{end}")
        if not str(section.get("name") or "").strip():
            problems.append(f"section {index} has no name")
    names: list[str] = []
    for index, track in enumerate(plan.get("tracks") or []):
        name = _plan_track_name(track)
        if not name:
            problems.append(f"track {index} has no name")
            continue
        names.append(name)
        if track.get("sensei_role") not in (None, "drum", "bass", "chord"):
            problems.append(f"track {name!r} has an unknown role {track.get('sensei_role')!r}")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        problems.append(f"duplicate track names {duplicates}: a write could not name its target")
    return problems


def _build_overall_status(writes: list[dict[str, Any]], aborted: str | None, locators_ok: bool | None, tempo_ok: bool | None) -> str:
    statuses = [w["status"] for w in writes if w["status"] != "muted_by_plan"]
    if "INDETERMINATE" in statuses or (aborted and "INDETERMINATE" in aborted):
        return "indeterminate"
    good = [s for s in statuses if s == "OK"]
    if not statuses:
        return "blocked"
    if not good:
        return "blocked" if all(s == "blocked" for s in statuses) else "failed"
    if len(good) == len(statuses) and not aborted and locators_ok is not False and tempo_ok is not False:
        return "completed"
    return "partial"


def handle_project_file_build(args: dict[str, Any]) -> dict[str, Any]:
    """The plan's tracks with their REAL presets, as a Live set file.

    The SDK cannot load a preset into an open set, so the set is built on
    disk from Live's own template: one MIDI track per plan track, the
    preset ArrangementGPS chose converted into set XML (Presetor owns the
    conversion and refuses anything that does not match what Live writes),
    the plan's tempo. The user opens the file; project_build then writes
    MIDI and locators into those tracks, the kit verified through the
    manifest written next to the set. No representative device is ever
    inserted: a track whose preset is not on this machine is not created,
    and is listed.
    """
    sys.path.insert(0, str(LOOM_DIR / "Presetor"))
    from presetor import preset_transplant as pt  # noqa: PLC0415

    if args.get("plan_path"):
        plan_path = Path(str(args["plan_path"])).expanduser()
        created = {"status": "REUSED", "plan_path": str(plan_path)}
    else:
        if not (args.get("prompt") or "").strip():
            raise ValueError("Give a prompt to build from, or a plan_path.")
        created = handle_plan_create({"prompt": args["prompt"]})
        plan_path = Path(created["plan_path"])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    project = plan.get("project") or {}
    wanted = [str(n) for n in (args.get("tracks") or [])] or [_plan_track_name(t) for t in plan.get("tracks") or [] if t.get("sensei_role")]
    answer: dict[str, Any] = {"plan": created, "template": str(pt.TEMPLATE), "tracks": [], "unresolved": [], "written": False}
    if not pt.TEMPLATE.is_file():
        return {**answer, "status": "refused", "reason": f"Live's template is not at {pt.TEMPLATE}"}
    try:
        builder = pt.SetBuilder()
        for track in plan.get("tracks") or []:
            name = _plan_track_name(track)
            if name not in wanted:
                continue
            family = str(track.get("instrument_family") or "").strip()
            path: str | None = None
            source = None
            if track.get("sensei_role") == "drum" and family:
                try:
                    path, source = resolve_kit_reference(family)["path"], "kit_catalogue"
                except Exception as error:  # noqa: BLE001
                    answer["unresolved"].append({"track": name, "family": family, "reason": f"kit_unresolved: {error}"})
                    continue
            elif family:
                entry = _catalog_entry(family)
                if entry and entry.get("path") and Path(str(entry["path"])).is_file():
                    path, source = str(entry["path"]), "identity_catalogue"
            if not path:
                answer["unresolved"].append({"track": name, "family": family or None, "reason": "no preset file for this family on this machine"})
                continue
            element = builder.add_midi_track(name)
            try:
                info = builder.place_preset(element, Path(path))
            except pt.Refused as error:
                return {**answer, "status": "refused", "stage": f"preset:{name}", "reason": str(error), "note": "Nothing was written."}
            answer["tracks"].append({"track": name, "role": track.get("sensei_role"), "family": family, "preset_path": path, "preset_source": source,
                                     "device": info["device"], "preset_name": info["name"], "preset_version": info["version"],
                                     "devices": info["carried"]["devices"], "pads": len(info["pad_notes"]), "macros": info["macros"],
                                     "choke_groups": info["choke_groups"], "version_drift": sorted(info["version_drift_between_references"])})
        if not answer["tracks"]:
            return {**answer, "status": "refused", "reason": "no plan track has a real preset on this machine; nothing to build", "note": "Nothing was written."}
        if project.get("bpm"):
            builder.set_tempo(float(project["bpm"]))
        out_dir = Path(str(args.get("out_dir") or (Path.home() / "Desktop" / "Loom Builds"))).expanduser()
        name = str(args.get("name") or project.get("name") or "Loom Build").strip() or "Loom Build"
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = out_dir / f"{name} {stamp} Project" / f"{name} {stamp}.als"
        report = builder.finish(out_path)
    except pt.Refused as error:
        return {**answer, "status": "refused", "reason": str(error), "note": "Nothing was written."}
    answer.update({"written": True, "status": "written", "artifact": report["artifact"], "manifest": report["manifest"], "bytes": report["bytes"],
                   "tempo": report.get("tempo"), "static_validation": report["static_validation"], "ids": report["ids"], "samples": report["samples"],
                   "preset_back_references": report.get("preset_back_references"),
                   "next": "open the artifact in Live (File > Open Live Set), restart the Extension Host if Live shows it stopped, "
                           "then project_build with the same plan_path, set_manifest=<manifest>, dry_run=false"})
    return answer


def _preset_file_for(track: dict[str, Any], kit_resolution: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """The real preset file the plan's instrument family names, if this machine has it."""
    if track.get("sensei_role") == "drum":
        return (kit_resolution.get("path"), kit_resolution.get("kit")) if kit_resolution else (None, None)
    family = str(track.get("instrument_family") or "").strip()
    entry = _catalog_entry(family) if family else None
    if entry and entry.get("path") and Path(str(entry["path"])).is_file():
        return str(entry["path"]), Path(str(entry["path"])).stem
    return None, None


def _os_preset_loader_available() -> tuple[bool, str]:
    """Handing a preset file to Live through the OS reaches whatever Live is
    running: allowed only against the real bridge (a test's redirected
    bridge root must never touch a live session)."""
    if os.environ.get("LOOM_BRIDGE_ROOT"):
        return False, "os preset loading is disabled while LOOM_BRIDGE_ROOT redirects the bridge (test isolation)"
    if os.environ.get("LOOM_OS_PRESET_LOAD", "1") == "0":
        return False, "os preset loading disabled by LOOM_OS_PRESET_LOAD=0"
    if sys.platform != "darwin":
        return False, "os preset loading needs macOS (open -a)"
    return True, "ok"


def _os_open(app: str, path: str) -> None:
    """Hand a preset file to Live through macOS. Replaceable by tests, which
    must never reach a running Live."""
    subprocess.run(["open", "-a", app, path], check=True, capture_output=True, timeout=20)


def _os_load_preset(path: str, track_name: str, expected_name: str, wait: float, send, expected_index: int | None = None) -> dict[str, Any]:
    """Load a real preset onto ONE track of the open set, the way a Finder
    double-click does, and prove where it landed.

    The SDK has no API for this. Live puts an opened preset on the selected
    track, which after create_midi_track is the new one (measured twice on
    2026-09-07, 1.7 s) -- but "the selected track was the right one" is not
    evidence, so the target is locked and checked on both sides:
      before: the target exists at the recorded index, with that exact name,
              and without the expected device; every track's device list is
              snapshotted;
      after:  exactly one track changed, it is the target (index and name),
              and what it gained is exactly the expected device.
    Anything else is refused with a named status and no MIDI follows.
    """
    from live_project import default_app  # noqa: PLC0415

    available, why = _os_preset_loader_available()
    if not available:
        return {"loaded": False, "verified": False, "status": "PRESET_LOAD_REQUIRED", "reason": why}
    app = default_app() or "Ableton Live 12 Beta"

    def snapshot(label: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        answer = send({"op": "get_state", "include_devices": True}, f"state:{label}:{track_name}:{int(time.time() * 10)}")
        rows = (answer.get("result") or {}).get("tracks") or []
        return answer, {str(t.get("name")): {"index": t.get("index"), "devices": [d.get("name") for d in t.get("devices") or []]} for t in rows}

    answer, before = snapshot("before_preset")
    if answer.get("status") != "OK":
        return {"loaded": False, "verified": False, "status": str(answer.get("status")), "reason": f"state before load: {answer.get('error')}"}
    target = before.get(track_name)
    if target is None:
        return {"loaded": False, "verified": False, "status": "PRESET_LOAD_REQUIRED", "reason": f"target track {track_name!r} is not in the set"}
    if expected_index is not None and target["index"] != expected_index:
        return {"loaded": False, "verified": False, "status": "PRESET_LOAD_REQUIRED",
                "reason": f"target track {track_name!r} is at index {target['index']}, create_midi_track reported {expected_index}"}
    if expected_name in target["devices"]:
        return {"loaded": True, "verified": True, "status": "OK", "via": "already_present", "track": track_name, "index": target["index"], "device": expected_name}
    started = time.time()
    try:
        _os_open(app, path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        return {"loaded": False, "verified": False, "status": "PRESET_LOAD_REQUIRED", "reason": f"open failed: {error}"}
    deadline = started + max(5.0, wait)
    after: dict[str, dict[str, Any]] = {}
    while time.time() < deadline:
        time.sleep(0.6)
        answer, after = snapshot("after_preset")
        if answer.get("status") != "OK":
            continue
        changed = [n for n in set(before) | set(after) if (before.get(n) or {}).get("devices") != (after.get(n) or {}).get("devices")]
        if not changed:
            continue
        gained_on_target = [d for d in (after.get(track_name) or {}).get("devices", []) if d not in target["devices"]]
        if changed == [track_name] and gained_on_target == [expected_name] and (after[track_name]["index"] == target["index"]):
            return {"loaded": True, "verified": True, "status": "OK", "via": "os_open", "track": track_name, "index": target["index"],
                    "device": expected_name, "seconds": round(time.time() - started, 1)}
        # Something changed and it is not exactly the target gaining exactly the preset.
        return {"loaded": True, "verified": False, "status": "PRESET_LANDED_ELSEWHERE", "via": "os_open",
                "reason": f"changed tracks {sorted(changed)}, target gained {gained_on_target}; expected only {track_name!r} to gain {expected_name!r}",
                "changed": {n: after.get(n, {}).get("devices") for n in changed}}
    return {"loaded": False, "verified": False, "status": "PRESET_LOAD_REQUIRED", "via": "os_open",
            "reason": f"{expected_name} did not appear on {track_name} within {max(5.0, wait):.0f}s", "devices_now": (after.get(track_name) or {}).get("devices")}


def _set_manifest(args: dict[str, Any]) -> dict[str, Any] | None:
    """What project_file_build put into the set the caller says is open."""
    path = args.get("set_manifest")
    if not path:
        return None
    manifest = json.loads(Path(str(path)).expanduser().read_text(encoding="utf-8"))
    if not Path(str(manifest.get("artifact") or "")).is_file():
        raise ValueError(f"set_manifest points at a set file that does not exist: {manifest.get('artifact')}")
    return manifest


def handle_project_build(args: dict[str, Any]) -> dict[str, Any]:
    dry_run = bool(args.get("dry_run", True))
    wait = float(args.get("wait_seconds", 15))
    base_seed = int(args.get("seed", 7))

    if args.get("plan_path"):
        plan_path = Path(args["plan_path"]).expanduser()
        # The key of a rebuild is the plan's contents, not its file name: two
        # different plans called ableton_session_plan.json must not share keys.
        digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()[:12] if plan_path.exists() else "missing"
        created = {"status": "REUSED", "plan_path": str(plan_path), "run_id": f"reuse-{digest}"}
    else:
        if not (args.get("prompt") or "").strip():
            raise ValueError("Give a prompt to build from, or a plan_path to rebuild from.")
        created = handle_plan_create({"prompt": args["prompt"]})
        # This run's plan, never "the last plan" some other call produced.
        plan_path = Path(created["plan_path"])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    base: dict[str, Any] = {"dry_run": dry_run, "plan": created, "trigger": bridge_client.active_bridge_label(),
                            "step_order": ["plan", "preset_resolution", "tempo", "track", "preset_load", "preset_verify", "pads",
                                           "clips", "clip_readback", "locators", "state_readback"]}
    if args.get("allow_lossy_kit"):
        base["allow_lossy_kit"] = "ignored: project_build never reconstructs a plan kit from samples; a kit that cannot be loaded is PRESET_LOAD_REQUIRED (live_command build_drum_kit is the explicit, separate op)"

    # Explicit simplification, never a silent one: which plan tracks are
    # built and which native device stands in for a preset name are the
    # caller's decisions, and the answer repeats them.
    all_names = [_plan_track_name(t) for t in plan.get("tracks") or []]
    simplification: dict[str, Any] = {"plan_tracks": all_names, "built_tracks": all_names, "dropped": [], "device_overrides": [], "reason": None}
    if args.get("tracks"):
        wanted = [str(n) for n in args["tracks"]]
        unknown = [n for n in wanted if n not in all_names]
        if unknown:
            return {**base, "status": "failed", "stage": "track_selection", "problems": [f"tracks not in the plan: {unknown}"],
                    "plan_tracks": all_names, "note": "Nothing was sent to Live."}
        plan["tracks"] = [t for t in plan.get("tracks") or [] if _plan_track_name(t) in wanted]
        simplification.update({"built_tracks": wanted, "dropped": [n for n in all_names if n not in wanted],
                               "reason": "tracks selected by the caller (project_build tracks=...)"})
    for track in plan.get("tracks") or []:
        override = (args.get("device_map") or {}).get(_plan_track_name(track))
        if override:
            simplification["device_overrides"].append({"track": _plan_track_name(track), "plan_family": track.get("instrument_family"), "device": str(override)})
            track["instrument_family"] = str(override)
    base["simplification"] = simplification

    # The drum kit: the caller's, else the plan's drum instrument_family as a
    # catalogue name. Resolved once, from the preset's own XML; the SDK
    # rebuilds pads from its samples. What that drops is in the answer.
    kits: dict[str, dict[str, Any]] = {}
    base["kits"] = {}
    manifest = _set_manifest(args)
    if manifest:
        base["set_manifest"] = {"path": str(args.get("set_manifest")), "artifact": manifest.get("artifact"),
                                "tracks": sorted(t for t, p in (manifest.get("tracks") or {}).items() if p)}
    for track in plan.get("tracks") or []:
        if track.get("sensei_role") != "drum":
            continue
        name = _plan_track_name(track)
        plan_ref = track.get("instrument_family")
        kit_ref = args.get("kit") or plan_ref
        info = {"reference": kit_ref, "plan_reference": plan_ref,
                "selection_source": "explicit_kit" if args.get("kit") else "plan", "resolved": False}
        if kit_ref and kit_ref != "Drum Rack":
            try:
                kit = resolve_kit_reference(str(kit_ref))
                kits[name] = kit
                info.update(_kit_answer(kit, include_fidelity=False))
                info.update({"resolved": True, "profile_write_safety": kit.get("profile_write_safety", "unknown"),
                             "expected_pad_notes": sorted(int(p["note"]) for p in kit["pads"]),
                             "raw_receiving_notes": sorted(int(p["raw_receiving_note"]) for p in kit["pads"] if p.get("raw_receiving_note") is not None),
                             "rebuild_blocker": None})  # project_build never rebuilds a plan kit
                placed = ((manifest or {}).get("tracks") or {}).get(name)
                if placed and placed.get("path") == kit.get("path"):
                    info.update({"native_in_set_file": True, "preset_preserved": True, "load_path": {"via": "set_file_manifest"}})
                elif args.get("preset_load", "os") == "os" and not (args.get("device_map") or {}).get(name):
                    available, why = _os_preset_loader_available()
                    info.update({"native_preset_load": "os_open" if available else None, "preset_preserved": True if available else None,
                                 "load_path": {"via": "os_open" if available else None, "note": why,
                                               "blocked_as": None if available else "PRESET_LOAD_REQUIRED"}})
                else:
                    info["load_path"] = {"via": None, "blocked_as": "PRESET_LOAD_REQUIRED", "note": "no load path for this kit"}
            except Exception as error:  # noqa: BLE001
                info["reason"] = str(error)
        base["kits"][name] = info

    problems = _validate_plan(plan)
    if problems:
        return {**base, "status": "failed", "stage": "plan_validation", "problems": problems,
                "note": "Nothing was sent to Live: the plan is not buildable as written."}

    project = plan.get("project") or {}
    sections = plan.get("locators") or []
    root, mode = _plan_key(str(project.get("key") or ""))
    genre = str(project.get("genre") or "").strip()
    genre_style = genre.lower() or None
    writers = [t for t in plan.get("tracks") or [] if t.get("sensei_role")]
    out_of_scope = [_plan_track_name(t) for t in plan.get("tracks") or [] if not t.get("sensei_role")]

    beats_per_bar, bpb_source = _beats_per_bar(args)
    steps: list[dict[str, Any]] = []
    if project.get("bpm"):
        steps.append({"kind": "tempo", "op": "set_tempo", "bpm": float(project["bpm"])})
    if root and mode:
        steps.append({"kind": "key", "op": "set_key", "root": root, "mode": mode})
    for section in sections:
        steps.append({"kind": "locator", "section": section["name"],
                      "beat": (int(section["start_bar"]) - 1) * beats_per_bar})
    summary = {"project": {"name": project.get("name"), "bpm": project.get("bpm"), "key": f"{root} {mode}",
                           "genre": genre, "sections": len(sections), "total_bars": project.get("total_bars")},
               "beats_per_bar": beats_per_bar, "beats_per_bar_source": bpb_source,
               "tracks_out_of_scope": out_of_scope}

    def write_entry(track: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
        bars = int(section["end_bar"]) - int(section["start_bar"]) + 1
        energy = (track.get("section_activity") or {}).get(section.get("id"), section.get("energy"))
        density = None if energy is None else max(0.0, min(1.0, float(energy) / 100.0))
        return {"track": _plan_track_name(track), "role": track["sensei_role"], "section": section["name"],
                "start_bar": int(section["start_bar"]), "bars": bars, "density": density, "genre_style": genre_style,
                "profile": _profile_for_role(track["sensei_role"], track.get("instrument_family"))}

    if dry_run:
        live_names: set[str] | None = None
        try:
            state = handle_live_state({})
            if state.get("is_fresh"):
                live_names = {str(tr.get("name")) for tr in state.get("tracks") or []}
        except Exception:  # noqa: BLE001 -- no session is a valid dry-run state
            live_names = None
        tracks = []
        for track in plan.get("tracks") or []:
            name = _plan_track_name(track)
            kit_resolution = kits.get(name)
            entry = {"track": name, "instrument_family": track.get("instrument_family"), "role": track.get("sensei_role")}
            if live_names is None:
                entry["status"] = "unknown_no_session"
            else:
                entry["status"] = "exists" if name in live_names else "would_create"
            preset_path, preset_name = _preset_file_for(track, kit_resolution) if args.get("preset_load", "os") == "os" else (None, None)
            if preset_path and (track.get("sensei_role") == "drum" and args.get("allow_lossy_kit") is True):
                preset_path, preset_name = None, None
            if preset_path and not (args.get("device_map") or {}).get(name):
                available, why = _os_preset_loader_available()
                entry["preset"] = {"path": preset_path, "name": preset_name, "would_load_via": "os_open" if available else None, "note": why}
            if track.get("sensei_role") == "drum":
                entry["kit"] = ("would_build %d pads from %s" % (len(kit_resolution["pads"]), kit_resolution["kit"])) if kit_resolution else "no kit resolved: an empty Drum Rack has no pads, drum writes would be blocked"
                if kit_resolution and preset_path and entry.get("preset", {}).get("would_load_via"):
                    entry["kit"] = "would load the real %s through the OS and read its pads back; nothing rebuilt" % kit_resolution["kit"]
                if kit_resolution and base["kits"][name].get("native_in_set_file"):
                    entry["kit"] = "native %s in the set file (manifest); pads read from Live, nothing rebuilt" % kit_resolution["kit"]
                if kit_resolution and (base["kits"][name].get("load_path") or {}).get("blocked_as"):
                    entry["kit"] = "%s: %s" % (base["kits"][name]["load_path"]["blocked_as"], base["kits"][name]["load_path"].get("note"))
            elif track.get("sensei_role") in ("bass", "chord") and track.get("instrument_family") and _catalog_entry(str(track["instrument_family"])):
                entry["needs_preset"] = {"family": track["instrument_family"], "note": "a catalogue preset the SDK cannot load; map it with device_map or load it in Live yourself and rebuild"}
            tracks.append(entry)
        results = []
        for track in writers:
            for section in sections:
                if not _track_plays_in(track, section):
                    results.append({"track": _plan_track_name(track), "section": section["name"], "status": "muted_by_plan"})
                else:
                    blocker = ((base["kits"].get(_plan_track_name(track)) or {}).get("load_path") or {}).get("blocked_as")
                    results.append({**write_entry(track, section), "status": "target_verification_required" if blocker else "would_write",
                                    **({"reason": blocker} if blocker else {})})
        for step in steps:
            if step["kind"] == "key":
                step["outcome"] = "UNSUPPORTED_BY_SDK"
        return {**base, **summary, "status": "dry_run", "tracks": tracks,
                "track_totals": dict(Counter(tr["status"] for tr in tracks)), "session_steps": steps,
                "writes": results, "totals": dict(Counter(r["status"] for r in results)),
                "note": "Nothing was sent to Live. Call again with dry_run=false to write."}

    # --- Live. Nothing is written before the bridge and its capabilities are known.
    check_cancelled()
    try:
        target = resolve_bridge_target()
    except BridgeUnavailable as error:
        return {**base, **summary, "status": "blocked", "stage": "bridge", "reason": str(error),
                "candidates": error.candidates, "tracks": [], "writes": [], "session_steps": steps,
                "note": "Nothing was sent to Live."}
    # The protocol gate, once for the whole build: an extension that does not
    # publish a compatible queue protocol, a session id and a fresh state gets
    # no mutation at all (each request is gated again on its way out).
    gate = bridge_client.mutation_gate(target, "write_arrangement_clip")
    if gate:
        return {**base, **summary, "status": "blocked", "stage": "bridge", "reason": gate.get("error"),
                "gate_status": gate.get("status"), "protocol": gate.get("protocol"), "outcome": gate.get("outcome"),
                "bridge": target.describe(), "tracks": [], "writes": [], "session_steps": steps, "note": "Nothing was sent to Live."}
    capabilities = target.capabilities
    missing = [c for c in BUILD_REQUIRED_CAPABILITIES if not capabilities.get(c)]
    if missing:
        return {**base, **summary, "status": "blocked", "stage": "capabilities",
                "reason": f"the running extension does not publish {missing}", "bridge": target.describe(),
                "tracks": [], "writes": [], "session_steps": steps, "note": "Nothing was sent to Live."}
    # Idempotency keys are scoped to the Live session the build targets and to
    # this plan's contents, so a key can never replay an outcome from another
    # set or another plan.
    # Keep existing keys, including interrupted builds. A changed payload on
    # the same key is refused by the journal, never silently re-applied.
    plan_digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()[:12]
    run_key = f"{target.session_id or 'nosession'}:{plan_digest}:{created.get('run_id') or 'plan'}"
    aborted: str | None = None

    def send(payload: dict[str, Any], key: str) -> dict[str, Any]:
        return bridge_client.submit_request(payload, wait, target=target, idempotency_key=f"{run_key}:{key}")

    # 1) settings
    for step in steps:
        if step["kind"] == "tempo":
            answer = send({"op": "set_tempo", "bpm": step["bpm"]}, "tempo")
            step["outcome"] = answer.get("status")
            step["bridge_outcome"] = answer.get("outcome")
            if answer.get("error"):
                step["error"] = answer.get("error")
            if answer.get("status") in BRIDGE_DEAD_STATUSES:
                aborted = f"tempo: {answer.get('status')}"
        elif step["kind"] == "key":
            # Reported, not attempted: the SDK cannot write the song key.
            step["outcome"] = "UNSUPPORTED_BY_SDK"
            step["note"] = bridge_client.SDK_UNSUPPORTED_OPS["set_key"][1]

    # 2) tracks, with the evidence each writer needs
    tracks = []
    gate: dict[str, dict[str, Any]] = {}
    needs_device_check: list[str] = []
    for track in plan.get("tracks") or []:
        if aborted:
            break
        name = _plan_track_name(track)
        kit_resolution = kits.get(name)
        kit_verified = False
        role = track.get("sensei_role")
        entry: dict[str, Any] = {"track": name, "instrument_family": track.get("instrument_family"), "role": role}
        # The real preset, when this machine has its file: the track is made
        # empty and the file is handed to Live through the OS (no native
        # stand-in, no Simpler rebuild). Without a file, the family goes to
        # the SDK as before (a native device name, or a reported refusal).
        preset_path, preset_name = _preset_file_for(track, kit_resolution) if args.get("preset_load", "os") == "os" else (None, None)
        if preset_path and (args.get("device_map") or {}).get(name):
            preset_path, preset_name = None, None  # an explicit device override wins
        # A drum track whose plan names a real kit gets NO device from the SDK:
        # the kit is loaded (verified) or the track stays empty and blocked.
        family_to_insert = None if (preset_path or (role == "drum" and kit_resolution)) else track.get("instrument_family")
        outcome = _create_midi_track_request(name, family_to_insert, wait, target=target,
                                             idempotency_key=f"{run_key}:track:{name}")
        result = outcome.get("result") or {}
        entry["status"] = outcome.get("status")
        entry["bridge_outcome"] = outcome.get("outcome")
        entry["created"] = result.get("created")
        entry["instrument"] = result.get("instrument")
        if outcome.get("error"):
            entry["error"] = outcome.get("error")
        tracks.append(entry)
        if outcome.get("status") in BRIDGE_DEAD_STATUSES:
            aborted = f"track {name}: {outcome.get('status')}"
            break
        if outcome.get("status") != "OK":
            gate[name] = {"ok": False, "reason": f"track_not_created: {outcome.get('error')}"}
            continue
        preset_loaded: dict[str, Any] | None = None
        if preset_path:
            placed = ((manifest or {}).get("tracks") or {}).get(name)
            if placed and placed.get("path") == preset_path:
                preset_loaded = {"loaded": True, "verified": True, "status": "OK", "via": "set_file_manifest", "device": preset_name}
            else:
                preset_loaded = _os_load_preset(preset_path, name, str(preset_name), wait, send, expected_index=result.get("index"))
            entry["preset"] = {"path": preset_path, "name": preset_name, **preset_loaded}
            if not preset_loaded.get("verified"):
                # No verified preset on the target: nothing is written to it.
                # Never a native stand-in, never a Simpler rebuild of the kit.
                entry["target_evidence"] = "none"
                code = preset_loaded.get("status") or "PRESET_LOAD_REQUIRED"
                gate[name] = {"ok": False, "code": code, "reason": f"{code}: {preset_loaded.get('reason')}"}
                if role == "drum" and kit_resolution:
                    entry["kit"] = {"kit": kit_resolution["kit"], "status": code, "reason": preset_loaded.get("reason"), "rebuilt": False}
                if code == "PRESET_LANDED_ELSEWHERE":
                    aborted = f"preset for {name} landed elsewhere: {preset_loaded.get('reason')}"
                    break
                continue
        if role == "drum":
            pads = send({"op": "drum_pads", "track": name}, f"pads:{name}")
            if pads.get("status") in BRIDGE_DEAD_STATUSES:
                aborted = f"drum_pads {name}: {pads.get('status')}"
                break
            pad_result = pads.get("result") or {}
            pad_notes = [int(n) for n in (pad_result.get("pad_notes") or [])]
            live_chains = {int(c["note"]): list(c.get("devices") or []) for rack in (pad_result.get("drum_racks") or []) for c in (rack.get("chains") or []) if c.get("note") is not None}
            live_chain_names = {int(c["note"]): list(c.get("device_names") or []) for rack in (pad_result.get("drum_racks") or []) for c in (rack.get("chains") or []) if c.get("note") is not None}
            expected_pads = sorted(int(p["note"]) for p in kit_resolution["pads"]) if kit_resolution else []
            if kit_resolution and pad_notes and preset_loaded and preset_loaded.get("verified"):
                if sorted(pad_notes) == expected_pads:
                    # The preset on this track is the file we read: Live reports
                    # exactly the pads the file declares (decoded ReceivingNote).
                    kit_verified = True
                    file_devices = {int(p["note"]): list(p.get("source_devices") or []) for p in kit_resolution["pads"]}
                    if live_chains and not any(file_devices.values()):
                        device_evidence = {"reported_by_extension": True, "verified": None, "live_first_devices": sorted({(live_chains.get(n) or ["?"])[0] for n in expected_pads}),
                                           "note": "the preset file declares no chain devices to compare against"}
                    elif live_chains:
                        # Compared per pad on the first chain device (the instrument). The
                        # SDK's className is "Device" for anything it does not specialise
                        # (a DrumCell included, measured 2026-09-07), so the comparison
                        # accepts either the XML class or Live's display name for it.
                        # A pad whose file entry names no device cannot be compared.
                        display = {"DrumCell": "Drum Sampler", "OriginalSimpler": "Simpler", "MultiSampler": "Sampler", "InstrumentGroupDevice": "Instrument Rack"}
                        comparable = [n for n in expected_pads if file_devices.get(n)]

                        # Live names a pad's Drum Sampler after its sample ("Kick BNYX 1",
                        # measured 2026-09-07 with extension 0.4.3), so the name proves WHICH
                        # sample sits on WHICH pad -- the file's own pairing -- while the
                        # device class stays unexposed by the SDK.
                        file_samples = {int(p["note"]): Path(str(p.get("sample") or "")).stem for p in kit_resolution["pads"]}

                        def matches(n: int) -> bool:
                            wanted = file_devices[n][0]
                            live_class = [d for d in live_chains.get(n, []) if d not in ("AudioBranchMixerDevice",)][:1]
                            live_name = [d for d in live_chain_names.get(n, []) if d not in ("AudioBranchMixerDevice",)][:1]
                            return live_class == [wanted] or live_name == [wanted] or live_name == [display.get(wanted)] or (bool(file_samples.get(n)) and live_name == [file_samples[n]])

                        agree = [n for n in comparable if matches(n)]
                        sample_named = [n for n in expected_pads if file_samples.get(n) and (live_chain_names.get(n) or [None])[0] == file_samples[n]]
                        generic = all((live_chains.get(n) or ["?"])[0] == "Device" for n in expected_pads)
                        device_evidence = {"reported_by_extension": True, "pads": len(expected_pads), "comparable_pads": len(comparable),
                                           "pads_with_matching_first_device": len(agree), "uncomparable_pads": [n for n in expected_pads if n not in comparable],
                                           "pads_whose_device_is_named_after_the_files_sample": len(sample_named),
                                           "live_first_devices": sorted({(live_chains.get(n) or ["?"])[0] for n in expected_pads}),
                                           "live_first_device_names": sorted({(live_chain_names.get(n) or ["?"])[0] for n in expected_pads}),
                                           "file_first_devices": sorted({file_devices[n][0] for n in comparable}),
                                           "verified": bool(comparable) and len(agree) == len(comparable),
                                           "device_class_verified": None if generic else (bool(comparable) and len(agree) == len(comparable)),
                                           "note": ("the SDK exposes className 'Device' for these chains: the device CLASS cannot be read; "
                                                    "what is verified is each pad's device name = the file's sample for that pad") if generic else None}
                    else:
                        device_evidence = {"reported_by_extension": False, "verified": None,
                                           "note": "the running extension does not report chain devices (needs >= 0.4.2)"}
                    entry["kit"] = {"kit": kit_resolution["kit"], "status": "native_preset_loaded", "via": preset_loaded.get("via"),
                                    "preset_preserved": True, "pads": len(pad_notes), "pad_notes": sorted(pad_notes),
                                    "device_evidence": device_evidence}
                else:
                    entry["kit"] = {"kit": kit_resolution["kit"], "status": "loaded_pads_differ", "expected": expected_pads, "live": sorted(pad_notes)}
                    gate[name] = {"ok": False, "code": "INDETERMINATE", "reason": f"INDETERMINATE: {kit_resolution['kit']} is on the track but Live reports pads {sorted(pad_notes)}, the file declares {expected_pads}"}
                    continue
            elif kit_resolution and not pad_notes:
                # A real kit was named and nothing readable is on the track:
                # the kit has to be loaded, it is not rebuilt.
                entry["kit"] = {"kit": kit_resolution["kit"], "status": "PRESET_LOAD_REQUIRED", "reason": pad_result.get("reason") or pads.get("error")}
                gate[name] = {"ok": False, "code": "PRESET_LOAD_REQUIRED", "reason": f"PRESET_LOAD_REQUIRED: {kit_resolution['kit']} is not on {name!r} ({pad_result.get('reason') or pads.get('error')})"}
                continue
            entry["pad_notes"] = pad_notes
            entry["target_evidence"] = "live_drum_rack_pads" if pad_notes else "none"
            reason = pad_result.get("reason") or pads.get("error")
            gate[name] = {"ok": bool(pad_notes), "pad_notes": pad_notes,
                          "reason": None if pad_notes else f"drum_rack_not_verified: {reason}"}
            if pad_notes:
                resolved_pads = pad_notes_resolver()(pad_notes, kit_resolution, kit_verified=kit_verified)
                gate[name]["pad_mapping"] = resolved_pads["mapping"]
                entry["pad_mapping"] = resolved_pads["mapping"]
                entry["pad_targets"] = resolved_pads["mapping"].get("targets")
                entry["pad_source"] = resolved_pads["source"]
                entry["note_semantics"] = resolved_pads.get("note_semantics")
                entry["preset_identity_verified"] = kit_verified
                entry["role_source"] = resolved_pads.get("role_source")
                if not resolved_pads["mapping"]["generate_pads"]:
                    gate[name] = {"ok": False, "code": "INDETERMINATE", "reason": "kit_roles_unverified: pad numbers alone do not identify samples or drum roles"}
        elif role in ("bass", "chord"):
            instrument = str(result.get("instrument") or "")
            if preset_loaded and preset_loaded.get("verified"):
                entry["target_evidence"] = f"native_preset_loaded: {preset_loaded.get('device')} ({preset_loaded.get('via')})"
                gate[name] = {"ok": True}
            elif instrument.startswith("inserted:"):
                entry["target_evidence"] = "native_device_inserted"
                gate[name] = {"ok": True}
            elif instrument.startswith("kept:"):
                needs_device_check.append(name)  # decided from the state below
            else:
                entry["target_evidence"] = "none"
                gate[name] = {"ok": False, "reason": f"instrument_not_loaded: {instrument or 'no instrument family in the plan'}"}
                catalogue = _catalog_entry(str(track.get("instrument_family") or ""))
                if instrument.startswith("not_loadable_in_extension"):
                    entry["needs_preset"] = {
                        "family": track.get("instrument_family"), "preset_path": catalogue.get("path") if catalogue else None,
                        "options": ["project_build(device_map={%r: '<native device, e.g. Operator / Electric / Wavetable>'})" % name,
                                    "load the preset onto the track in Live yourself, then rebuild the same plan: the track is adopted and its device is read from the state"],
                        "note": "the Extensions SDK inserts native devices with their default preset only; browser presets cannot be loaded through it"}
    if needs_device_check and not aborted:
        state = send({"op": "get_state", "include_devices": True}, "state:devices")
        if state.get("status") in BRIDGE_DEAD_STATUSES:
            aborted = f"get_state: {state.get('status')}"
        devices = {str(tr.get("name")): tr.get("devices") or [] for tr in (state.get("result") or {}).get("tracks") or []}
        for name in needs_device_check:
            present = [d.get("name") for d in devices.get(name) or []]
            for entry in tracks:
                if entry["track"] == name:
                    entry["target_evidence"] = f"devices_present: {present}" if present else "none"
            gate[name] = {"ok": bool(present), "reason": None if present else "instrument_not_loaded: the adopted track has no device"}

    # 3) clips
    results: list[dict[str, Any]] = []
    written_targets: dict[str, set[int]] = {}  # drum track -> every pad note a written clip carries
    for track_index, track in enumerate(writers):
        name = _plan_track_name(track)
        role = track["sensei_role"]
        for section_index, section in enumerate(sections):
            if not _track_plays_in(track, section):
                results.append({"track": name, "section": section["name"], "status": "muted_by_plan"})
                continue
            entry = write_entry(track, section)
            if aborted:
                results.append({**entry, "status": "aborted", "reason": aborted})
                continue
            verdict = gate.get(name) or {"ok": False, "reason": "track_not_created"}
            if not verdict.get("ok"):
                results.append({**entry, "status": "blocked", "code": verdict.get("code"), "reason": verdict.get("reason") or "target_unverified"})
                continue
            mapping = verdict.get("pad_mapping") or {"mode": "direct", "generate_pads": verdict.get("pad_notes"), "map": {}}
            generated = handle_midi_generate({
                "role": role, "genre": genre or "Trap", "bars": entry["bars"],
                "instrument_family": track.get("instrument_family"),
                "seed": base_seed + track_index * 100 + section_index,
                "density": entry["density"], "genre_style": genre_style,
                "target_root": root, "target_mode": mode, "beats_per_bar": beats_per_bar,
                "pad_notes": mapping["generate_pads"] if role == "drum" else None, "instrument_verified": role in ("bass", "chord")})
            if not generated.get("generation_safe"):
                results.append({**entry, "status": "blocked", "reason": generated.get("error")})
                continue
            if not generated.get("writable_to_live"):
                results.append({**entry, "status": "blocked", "reason": generated.get("writable_reason")})
                continue
            raw_notes = (generated.get("payload") or {}).get("notes") or []
            if mapping.get("mode") == "by_role":
                raw_notes = [{**n, "pitch": mapping["map"][int(n["pitch"])]} for n in raw_notes if int(n["pitch"]) in mapping["map"]]
            notes = [{"pitch": n["pitch"], "start": n.get("time", n.get("start", 0.0)),
                      "duration": n["duration"], "velocity": n.get("velocity", 100)} for n in raw_notes]
            if not notes:
                results.append({**entry, "status": "blocked", "reason": "no_notes_for_pads: the generated part has no note on this kit's pads"
                                + (f" (kit lacks {mapping.get('unmapped_roles')})" if mapping.get("unmapped_roles") else "")})
                continue
            if role == "drum":
                allowed = set(verdict.get("pad_notes") or [])
                outside = sorted({int(n["pitch"]) for n in notes} - allowed)
                if outside:
                    results.append({**entry, "status": "blocked", "code": "INDETERMINATE",
                                    "reason": f"notes_outside_pads: {outside} are not pads Live reported; nothing written"})
                    continue
                written_targets.setdefault(name, set()).update(int(n["pitch"]) for n in notes)
            written = write_arrangement_clip({
                "track": name, "start_bar": int(section["start_bar"]), "length_beats": entry["bars"] * beats_per_bar,
                "beats_per_bar": beats_per_bar, "name": section["name"], "notes": notes, "wait_seconds": wait,
                "on_conflict": args.get("on_conflict") or "refuse"},
                target=target, idempotency_key=f"{run_key}:clip:{name}:{section['name']}")
            status = str(written.get("status"))
            held = written.get("result") or {}
            match = held.get("verified_notes_match")
            if match is None and held.get("verified_note_count") is not None:
                match = held.get("verified_note_count") == len(notes)
            if status == "OK":
                status = "OK" if match else ("CONTENT_MISMATCH" if match is False else "OK_UNCHECKED")
            results.append({**entry, "status": status, "notes": len(notes), "verified": match,
                            "verified_note_count": held.get("verified_note_count"), "error": written.get("error"),
                            "bridge_outcome": written.get("outcome"), "replayed": bool(written.get("replayed")),
                            "pad_mapping": mapping.get("mode") if role == "drum" else None,
                            "diagnostics": {k: v for k, v in (generated.get("diagnostics") or {}).items()
                                            if k.startswith(("density", "layer_fit"))}})
            if written.get("status") in BRIDGE_DEAD_STATUSES:
                aborted = f"clip {name}/{section['name']}: {written.get('status')}"

    for track_entry in tracks:
        if track_entry["track"] in written_targets:
            pads_allowed = set((gate.get(track_entry["track"]) or {}).get("pad_notes") or [])
            track_entry["target_notes"] = sorted(written_targets[track_entry["track"]])
            track_entry["notes_outside_pads"] = sorted(written_targets[track_entry["track"]] - pads_allowed)

    # 4) locators, after the clips have given the arrangement its length
    if not aborted:
        for step in steps:
            if step["kind"] != "locator":
                continue
            answer = send({"op": "create_locator", "beat": step["beat"], "name": step["section"]}, f"locator:{step['section']}")
            step["outcome"] = answer.get("status")
            step["bridge_outcome"] = answer.get("outcome")
            if answer.get("error"):
                step["error"] = answer.get("error")
            if answer.get("status") in BRIDGE_DEAD_STATUSES:
                aborted = f"locator {step['section']}: {answer.get('status')}"
                break

    # 5) read back what Live holds now
    locators_ok: bool | None = None
    tempo_ok: bool | None = None
    readback: dict[str, Any] = {"status": "skipped"}
    if not aborted:
        state = send({"op": "get_state", "include_devices": False}, "state:readback")
        readback = {"status": state.get("status")}
        held = state.get("result") or {}
        if state.get("status") == "OK":
            cues = held.get("cue_points") or []
            for step in steps:
                if step["kind"] == "locator":
                    step["verified"] = any(abs(float(c.get("time", -1)) - step["beat"]) < 1e-6 and c.get("name") == step["section"] for c in cues)
            locator_steps = [s for s in steps if s["kind"] == "locator"]
            locators_ok = all(s.get("verified") for s in locator_steps) if locator_steps else None
            for step in steps:
                if step["kind"] == "tempo" and held.get("tempo") is not None:
                    step["verified"] = abs(float(held["tempo"]) - step["bpm"]) < 1e-6
                    tempo_ok = step["verified"]
            readback.update({"tempo": held.get("tempo"), "cue_points": len(cues), "track_count": held.get("track_count")})

    status = _build_overall_status(results, aborted, locators_ok, tempo_ok)
    # What may have happened without an answer: carried up unchanged so the
    # caller sees the side effects and the safe next step, and retries nothing.
    indeterminate = [
        {"step": label, "code": (bo or {}).get("code"), "side_effects": (bo or {}).get("side_effects"), "next_step": (bo or {}).get("next_step")}
        for label, bo in (
            [(f"{s['kind']}:{s.get('section') or ''}", s.get("bridge_outcome")) for s in steps]
            + [(f"track:{t['track']}", t.get("bridge_outcome")) for t in tracks]
            + [(f"kit:{t['track']}", t["kit"].get("bridge_outcome")) for t in tracks if isinstance(t.get("kit"), dict)]
            + [(f"clip:{w['track']}/{w.get('section')}", w.get("bridge_outcome")) for w in results]
        )
        if isinstance(bo, dict) and bo.get("kind") == "indeterminate"
    ]
    return {**base, **summary, "status": status, "aborted": aborted, "bridge": target.describe(), "indeterminate": indeterminate,
            "tracks": tracks, "track_totals": dict(Counter(tr["status"] for tr in tracks)),
            "session_steps": steps, "writes": results, "totals": dict(Counter(r["status"] for r in results)),
            "readback": readback,
            "note": "Every write reports the status Live returned: OK means Live holds the notes note-for-note; "
                    "blocked means the target could not be verified and nothing was written there; "
                    "NOT_CONSUMED means the request was withdrawn unapplied; INDETERMINATE means Live picked it up "
                    "and never answered -- read the state before retrying."}


def handle_plan_create(args: dict[str, Any]) -> dict[str, Any]:
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required: the whole chain is derived from it.")

    run_id = f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    run_dir = ARRANGEMENTGPS_RUNS_DIR / run_id
    output_dir = run_dir / "output"
    builds_dir = run_dir / "Builds"
    output_dir.mkdir(parents=True, exist_ok=True)
    env = {"ARRANGEMENTGPS_OUTPUT_DIR": str(output_dir), "ARRANGEMENTGPS_BUILDS_DIR": str(builds_dir)}

    steps = []
    for index, (script, label) in enumerate(CHAIN_STEPS):
        check_cancelled()
        report_progress(index, len(CHAIN_STEPS), label)
        script_args = (prompt,) if label == "blueprint" else ()
        steps.append({"step": label, "output": _run_node(script, *script_args, env=env)})
    report_progress(len(CHAIN_STEPS), len(CHAIN_STEPS), "done")

    session_plan_path = output_dir / "ableton_session_plan.json"
    session_plan = json.loads(session_plan_path.read_text(encoding="utf-8"))
    project = session_plan.get("project", {})
    # The package stage records where it wrote; nothing here recomputes it.
    location = json.loads((output_dir / "package_location.json").read_text(encoding="utf-8"))
    build_dir = Path(location["build_dir"])
    action_list_file = build_dir / "ableton_action_list.json"

    # A convenience mirror of the newest plan for the loom://plan/session
    # resource and plan_verify. No build stage reads it.
    mirror = OUTPUT_ROOT / "ArrangementGPS" / "engine" / "output" / "ableton_session_plan.json"
    try:
        mirror.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: two parallel plan_create calls copying into the same mirror
        # left a torn file behind once (2026-09-06), and every reader of the
        # mirror -- plan_verify, the resource, check_instrument_coverage --
        # then failed on a plan that no run had actually produced.
        bridge_client.write_atomic(mirror, session_plan_path.read_text(encoding="utf-8"))
    except OSError:
        pass

    tracks = session_plan.get("tracks", [])
    generatable = [t for t in tracks if t.get("sensei_role")]
    return {
        "status": "CREATED",
        "prompt": prompt,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "plan_path": str(session_plan_path),
        "output_dir": str(output_dir),
        "project": project,
        "build_dir": str(build_dir),
        "action_list_file": str(action_list_file) if action_list_file.exists() else None,
        "locators": len(session_plan.get("locators", [])),
        "tracks_total": len(tracks),
        "tracks_sensei_can_generate": len(generatable),
        "tracks_out_of_scope": len(tracks) - len(generatable),
        "steps": steps,
        "message": "Build it into Live with project_build(plan_path=...) -- dry run first, then dry_run=false.",
    }


def _safe_filename(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in value)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "untitled"


def _load_sensei_identities() -> list[dict[str, Any]]:
    if not SENSEI_IDENTITY_PATH.exists():
        return []
    identities = []
    with SENSEI_IDENTITY_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                identities.append(json.loads(line))
    return identities


def handle_library_search(args: dict[str, Any]) -> dict[str, Any]:
    query = (args.get("query") or "").lower()
    role = args.get("role")
    genre = args.get("genre")
    limit = int(args.get("limit", 20))

    # Sensei's identity catalog is searched first: unlike a filesystem walk it
    # already knows each preset's role and native genre tags, so a result from
    # here is one Sensei can actually generate for. A name that only turns up
    # in the filesystem fallback is loadable but unverified.
    identities = _load_sensei_identities()
    verified = []
    for entry in identities:
        if role and entry.get("role") != role:
            continue
        if genre and genre not in (entry.get("native_genres") or []):
            continue
        if query and query not in entry.get("normalized_name", ""):
            continue
        verified.append({
            "name": Path(entry.get("name", "")).stem,
            "role": entry.get("role"),
            "pack": entry.get("pack"),
            "native_genres": entry.get("native_genres") or [],
            "path": entry.get("path"),
            "sensei_verified": True,
        })
        if len(verified) >= limit:
            break

    results = list(verified)
    fallback_used = False
    if len(results) < limit and query and not role and not genre:
        fallback_used = True
        exts = {".adg": "rack", ".adv": "preset", ".alc": "clip", ".amxd": "max_device"}
        roots = [
            Path("/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Core Library"),
            Path("/Applications/Ableton Live 12 Standard.app/Contents/App-Resources/Core Library"),
            Path.home() / "Music/Ableton",
        ]
        seen = {item["name"].lower() for item in results}
        for root in roots:
            if not root.exists() or len(results) >= limit:
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for filename in filenames:
                    path = Path(dirpath) / filename
                    if path.suffix.lower() not in exts or query not in path.stem.lower():
                        continue
                    if path.stem.lower() in seen:
                        continue
                    seen.add(path.stem.lower())
                    results.append({
                        "name": path.stem,
                        "role": None,
                        "pack": None,
                        "native_genres": [],
                        "path": str(path),
                        "sensei_verified": False,
                    })
                    if len(results) >= limit:
                        break
                if len(results) >= limit:
                    break

    return {
        "query": query,
        "role": role,
        "genre": genre,
        "catalog_size": len(identities),
        "catalog_available": bool(identities),
        "catalog_note": None if identities else (
            f"Sensei's identity catalog is not present at {SENSEI_IDENTITY_PATH}. It is generated from "
            "your own Ableton library and is never shipped, so any results below come from the "
            "filesystem fallback and carry no role or genre evidence."
        ),
        "sensei_verified_count": len(verified),
        "filesystem_fallback_used": fallback_used,
        "total_found": len(results),
        "results": results,
    }


def handle_render_plan(args: dict[str, Any]) -> dict[str, Any]:
    # Previously this wrote back whatever stem list the caller invented.
    # AIMixMaster already decides, per real track, what can be rendered and
    # why -- audio renders, MIDI needs a freeze first, groups and returns are
    # excluded -- so that is what runs here.
    from aimixmaster.als_io import load_als
    from aimixmaster.render_workflow import build_render_manifest, manifest_markdown

    als_path = resolve_als_path(args["als_path"])

    tree = load_als(als_path)
    project_title = args.get("project_title") or als_path.stem
    manifest = build_render_manifest(tree.getroot(), project_title)

    renderable = [t for t in manifest["tracks"] if t["should_render"]]
    excluded = [t for t in manifest["tracks"] if not t["should_render"]]

    job_path = OUTPUT_ROOT / "Renderer" / "Jobs" / f"{int(time.time())}_{_safe_filename(project_title)}_render_job.json"
    job_path.parent.mkdir(parents=True, exist_ok=True)
    job_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "status": "CREATED",
        "als_path": str(als_path),
        "job_path": str(job_path),
        "schema_version": manifest["schema_version"],
        "track_count": len(manifest["tracks"]),
        "renderable_count": len(renderable),
        "excluded": [{"track": t["original_track_name"], "reason": t["exclusion_reason"]} for t in excluded],
        "export_filenames": [t["export_filename"] for t in renderable],
        "markdown": manifest_markdown(manifest),
    }


SCRIPTS_DIR = LOOM_DIR / "scripts"


def _load_script_module(filename: str, module_name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"script_not_loadable: {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def handle_plan_verify(_args: dict[str, Any]) -> dict[str, Any]:
    # Same check the headless suite runs: every track with a Sensei role must
    # name an instrument that resolves to exactly that one role in Sensei's
    # catalog. This is the Live-side instrument_role_unresolved failure,
    # caught before Live is ever opened.
    coverage = _load_script_module("check_instrument_coverage.py", "loom_coverage")
    result = coverage.verify_plan(plan_path=OUTPUT_ROOT / "ArrangementGPS" / "engine" / "output" / "ableton_session_plan.json")
    return {
        "ok": result["ok"],
        "catalog_size": result["catalog_size"],
        "sensei_generatable": result["supported"],
        "out_of_scope": result["out_of_scope"],
        "failures": result["failures"],
        "message": "All Sensei-role tracks resolve to a single role." if result["ok"] else "Plan would fail in Live; see failures.",
    }


def handle_project_inspect_arrangement(args: dict[str, Any]) -> dict[str, Any]:
    from aimixmaster.als_io import load_als

    als_path = resolve_als_path(args["als_path"])

    shapes = _load_script_module("extract_arrangement_shapes.py", "loom_shapes")
    project = shapes.read_project(str(als_path))
    sections, song_end = shapes.derive_sections(project["events"], project["beats_per_bar"], project["track_count"])

    root = load_als(als_path).getroot()
    locators = [
        {
            "name": (node.find("./Name").attrib.get("Value") if node.find("./Name") is not None else ""),
            "beat": float(node.find("./Time").attrib.get("Value")) if node.find("./Time") is not None else None,
        }
        for node in root.iter("Locator")
    ]

    bpb = project["beats_per_bar"]
    total_bars = round(song_end / bpb) if song_end else 0
    # Locators, when the set has them, are the author's own sections.
    named = sorted((l for l in locators if l["beat"] is not None), key=lambda l: l["beat"])
    from_locators = []
    for index, loc in enumerate(named):
        end_beat = named[index + 1]["beat"] if index + 1 < len(named) else song_end
        if end_beat > loc["beat"]:
            from_locators.append({"name": loc["name"], "start_bar": round(loc["beat"] / bpb) + 1, "end_bar": round(end_beat / bpb),
                                  "length_bars": round((end_beat - loc["beat"]) / bpb, 1)})
    return {
        "als_path": str(als_path),
        "tempo": project["tempo"],
        "beats_per_bar": bpb,
        "beats_per_bar_source": project.get("beats_per_bar_source"),
        "track_count": project["track_count"],
        "total_bars": total_bars,
        "last_clip_end_bar": round(project.get("last_clip_end_beat", song_end) / bpb) if project.get("last_clip_end_beat") else total_bars,
        # Clips after a 16-bar gap with nothing on any track: leftovers, listed, not counted in total_bars.
        "outlier_clips": project.get("outlier_clips") or [],
        "tracks": project.get("tracks") or [],
        "locators": locators,
        "sections_from_locators": from_locators,
        "section_count": len(sections),
        # Inferred from where clips start and stop across tracks (arrangement
        # clips only, by their Time attribute), for sets without locators.
        "sections_inferred_from_clips": sections,
    }


def handle_projects_arrangement_shapes(args: dict[str, Any]) -> dict[str, Any]:
    shapes = _load_script_module("extract_arrangement_shapes.py", "loom_shapes")
    roots = args.get("roots") or [str(Path.home() / "Desktop"), str(Path.home() / "Documents"), str(Path.home() / "Music" / "Ableton")]
    limit = args.get("limit")

    files = []
    for root in roots:
        # Every directory the caller supplies must sit inside an allowed root.
        for dirpath, _dirnames, filenames in os.walk(resolve_scan_root(root)):
            if "/Backup" in dirpath or "/Factory" in dirpath or "/Codex/" in dirpath:
                continue
            files.extend(os.path.join(dirpath, f) for f in filenames if f.endswith(".als"))
    files.sort()
    if limit:
        files = files[: int(limit)]

    projects = []
    skipped = []
    for index, path in enumerate(files):
        # Uzun tarama: her dosyada iptal kontrolu ve ilerleme bildirimi.
        check_cancelled()
        report_progress(index, len(files), Path(path).name)
        try:
            project = shapes.read_project(path)
        except Exception as error:
            skipped.append({"path": path, "error": str(error)})
            continue
        sections, song_end = shapes.derive_sections(project["events"], project["beats_per_bar"], project["track_count"])
        if not sections:
            continue
        projects.append({
            "name": Path(path).stem,
            "tempo": project["tempo"],
            "total_bars": round(song_end / project["beats_per_bar"]),
            "section_count": len(sections),
            "section_lengths": [item["length_bars"] for item in sections],
        })

    lengths = Counter(length for item in projects for length in item["section_lengths"])
    totals = sorted(item["total_bars"] for item in projects)
    tempos = sorted(item["tempo"] for item in projects if item["tempo"])
    return {
        "scanned": len(files),
        "with_arrangement": len(projects),
        "skipped": skipped[:10],
        "section_length_histogram": dict(lengths.most_common(12)),
        "median_total_bars": totals[len(totals) // 2] if totals else None,
        "median_tempo": tempos[len(tempos) // 2] if tempos else None,
        "projects": projects,
    }


def handle_drumbuss_build(args: dict[str, Any]) -> dict[str, Any]:
    from aimixmaster.als_io import load_als, save_als_atomic
    from aimixmaster.buss_builder import build_drum_buss
    from aimixmaster.project_analyzer import preservation_snapshot
    from aimixmaster.verification import verify_drum_buss

    als_path = resolve_als_path(args["als_path"])

    source_name = args.get("source", "KICK BUSS")
    apply_changes = bool(args.get("apply", False))

    tree = load_als(als_path)
    preservation_snapshot(tree.getroot())
    result = build_drum_buss(tree.getroot(), source_name=source_name)

    response = {
        "als_path": str(als_path),
        "source_track": source_name,
        "target_track": result.target_name,
        "inserted_devices": list(result.inserted_tags),
        "changed": result.changed,
        "applied": False,
        "backup_path": None,
    }

    if not apply_changes:
        response["status"] = "READY" if result.changed else "ALREADY_VERIFIED"
        response["message"] = "Dry run only. Call again with apply=true to write the .als."
        return response

    # Writing into someone's finished project is not undoable from here, so a
    # timestamped copy is made first, every time, before the atomic save.
    backup_path = als_path.with_suffix(f".mcp_backup_{int(time.time())}.als")
    shutil.copy2(als_path, backup_path)
    save_als_atomic(tree, als_path)
    verify_drum_buss(load_als(als_path).getroot(), target_name=result.target_name)

    response["applied"] = True
    response["backup_path"] = str(backup_path)
    response["status"] = "WRITTEN_AND_VERIFIED"
    return response


def handle_chain_plan(args: dict[str, Any]) -> dict[str, Any]:
    from aimixmaster.als_io import load_als
    from presetor.chain_planner import plan_project

    als_path = resolve_als_path(args["als_path"])

    root = load_als(als_path).getroot()
    plan = plan_project(root)
    plan["als_path"] = str(als_path)
    plan["note"] = (
        "Recommendations are counted from the user's own projects, not invented. "
        "A role with too little measured evidence returns no_evidence rather than a guess."
    )
    return plan


def handle_chain_evidence(args: dict[str, Any]) -> dict[str, Any]:
    from presetor import chain_evidence

    rows = chain_evidence.load_tracks()
    role = args.get("role")
    if role:
        recommendation = chain_evidence.recommend(role, rows)
        if recommendation is None:
            return {
                "role": role,
                "has_recommendation": False,
                "reason": "not enough measured tracks for this role",
                "min_role_sample": chain_evidence.MIN_ROLE_SAMPLE,
            }
        return {
            "role": role,
            "has_recommendation": True,
            "chain": list(recommendation.chain),
            "role_sample": recommendation.role_sample,
            "devices": [
                {"device": item.device, "presence": item.presence, "occurrences": item.occurrences}
                for item in recommendation.devices
            ],
            "top_exact_chains": [
                {"chain": list(chain), "count": count}
                for chain, count in chain_evidence.chains_for_role(role, rows)[:5]
            ],
        }
    return chain_evidence.summary(rows)


def handle_chain_apply(args: dict[str, Any]) -> dict[str, Any]:
    from aimixmaster.als_io import load_als, save_als_atomic
    from presetor.chain_builder import ChainBuildError, chain_of, find_track, transplant_chain

    als_path = resolve_als_path(args["als_path"])

    target_name = args["target_track"]
    donor_name = args["donor_track"]
    apply_changes = bool(args.get("apply", False))

    tree = load_als(als_path)
    root = tree.getroot()

    if not apply_changes:
        # Dry run: same validation, nothing touches the disk.
        try:
            result = transplant_chain(root, target_name=target_name, donor_name=donor_name)
        except ChainBuildError as error:
            return {"status": "BLOCKED", "applied": False, "detail": str(error)}
        return {
            "status": "READY" if result.changed else "ALREADY_MATCHES",
            "applied": False,
            "target_track": target_name,
            "donor_track": donor_name,
            "chain": list(result.inserted_devices),
            "message": "Dry run only. Call again with apply=true to write the .als.",
        }

    result = transplant_chain(root, target_name=target_name, donor_name=donor_name)
    if not result.changed:
        return {"status": "ALREADY_MATCHES", "applied": False, "target_track": target_name, "chain": list(result.inserted_devices)}

    backup_path = als_path.with_suffix(f".mcp_backup_{int(time.time())}.als")
    shutil.copy2(als_path, backup_path)
    save_als_atomic(tree, als_path)

    written = chain_of(find_track(load_als(als_path).getroot(), target_name))
    if written != result.inserted_devices:
        raise RuntimeError(f"post_write_verification_failed: {written!r} != {result.inserted_devices!r}")

    return {
        "status": "WRITTEN_AND_VERIFIED",
        "applied": True,
        "target_track": target_name,
        "donor_track": donor_name,
        "chain": list(written),
        "backup_path": str(backup_path),
    }


def handle_palette_read(args: dict[str, Any]) -> dict[str, Any]:
    from sounddesigner import source_evidence

    rows = source_evidence.load_tracks()
    role = args.get("role")
    if not role:
        return source_evidence.summary(rows)

    result = source_evidence.palette(role, rows)
    if result is None:
        return {
            "role": role,
            "has_palette": False,
            "reason": "not enough measured tracks for this role",
            "min_role_sample": source_evidence.MIN_ROLE_SAMPLE,
        }
    return {
        "role": role,
        "has_palette": True,
        "role_sample": result.role_sample,
        "instruments": [{"device": device, "count": count} for device, count in result.instruments],
        "samples": [
            {"sample": item.sample, "occurrences": item.occurrences, "projects": item.projects}
            for item in result.samples
        ],
        "note": "Ranked by how many separate projects a sample appears in. Bounces, freezes and reverb impulse responses are excluded -- they carry no reusable sound identity.",
    }


def handle_project_sound_sources(args: dict[str, Any]) -> dict[str, Any]:
    sources = _load_script_module("extract_sound_sources.py", "loom_sources")
    from sounddesigner import source_evidence

    als_path = resolve_als_path(args["als_path"])

    rows = sources.read_sources(str(als_path))
    with_instruments = [row for row in rows if row["instruments"]]
    identity = Counter(
        name
        for row in rows
        for name in source_evidence.identity_samples(row)
    )
    return {
        "als_path": str(als_path),
        "track_count": len(rows),
        "tracks_with_instruments": len(with_instruments),
        "instruments": Counter(d for row in rows for d in row["instruments"]).most_common(),
        "identity_samples": identity.most_common(30),
        "tracks": [
            {"track": row["track"], "role": row["role"], "instruments": row["instruments"], "samples": row["instrument_samples"][:5]}
            for row in with_instruments
        ],
    }


def handle_render_verify(args: dict[str, Any]) -> dict[str, Any]:
    """Render Live'da yapilir; burada yapilan RENDER DOGRULAMASI.

    Manifest hangi track'in hangi dosya adiyla cikmasi gerektigini soyluyordu;
    bu arac cikan dosyalari o manifeste karsi olcer. Render'in kendisi Live'in
    ses motorunu gerektirir ve buradan yapilamaz.
    """
    from aimixmaster.als_io import load_als
    from aimixmaster.render_workflow import build_render_manifest, validate_renders, validation_markdown

    # This is the one tool in the server with a third-party dependency: it
    # measures real audio files. Everything else runs on a stock Python.
    try:
        import soundfile  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            "soundfile_not_installed: measuring rendered stems needs the soundfile package. "
            "Install it with: python3 -m pip install soundfile numpy. Every other Loom tool "
            "works without it."
        ) from error

    als_path = resolve_als_path(args["als_path"])
    renders_dir = _assert_within_allowed(Path(args["renders_dir"]))
    if not renders_dir.is_dir():
        raise ValueError(f"renders_dir_not_a_directory: {renders_dir}")

    tree = load_als(als_path)
    manifest = build_render_manifest(tree.getroot(), args.get("project_title") or als_path.stem)
    validation = validate_renders(manifest, renders_dir)
    validation["als_path"] = str(als_path)
    validation["renders_dir"] = str(renders_dir)
    validation["markdown"] = validation_markdown(validation)
    return validation


def handle_setup_scan(args: dict[str, Any]) -> dict[str, Any]:
    """Build this machine's catalogues from its own Ableton install.

    Loom ships code and fixtures, never measurements. The catalogues it reasons
    with come from the stock Ableton library on the machine it runs on, read
    out of Live's own file index. Every user therefore gets their own, and
    nobody has to hand-configure a path.
    """
    setup = _load_script_module("setup_scan.py", "loom_setup_scan")
    found = setup.find_ableton()

    if args.get("check_only", True):
        catalogues = []
        for label, out, _builder in setup.STEPS:
            files = sorted(out.glob("*.jsonl")) if out.exists() else []
            catalogues.append({
                "catalogue": label,
                "present": bool(files),
                "rows": sum(1 for _ in files[0].open(encoding="utf-8")) if files else 0,
            })
        return {
            "mode": "check",
            "ableton_index": found,
            "catalogues": catalogues,
            "ready": bool(found["readable_index"]) and all(c["present"] for c in catalogues),
            "note": "Nothing was written. Call again with check_only=false to build the missing catalogues.",
        }

    if not found["readable_index"]:
        return {
            "mode": "build",
            "ableton_index": found,
            "built": [],
            "ready": False,
            "message": "No readable Ableton file index. Install Live and open it once so it indexes "
                       "the library, then run this again. Nothing was written.",
        }

    built, failed = [], []
    for label, out, builder in setup.STEPS:
        check_cancelled()
        report_progress(len(built) + len(failed), len(setup.STEPS), label)
        out.mkdir(parents=True, exist_ok=True)
        try:
            result = builder(out)
            built.append({"catalogue": label, "entries": result.get("entry_count") if isinstance(result, dict) else None})
        except Exception as error:  # noqa: BLE001
            failed.append({"catalogue": label, "error": f"{type(error).__name__}: {error}"})

    return {
        "mode": "build",
        "ableton_index": found,
        "built": built,
        "failed": failed,
        "ready": not failed,
        "note": "Tools that depend on a catalogue that failed will say so rather than guess.",
    }


# --- Mix Check (SubverseLab mix analyzer, ported 2026-09-03) ----------------
MIX_ANALYZER_DIR = LOOM_DIR / "MixAnalyzer"


def _mix_module():
    if str(MIX_ANALYZER_DIR) not in sys.path:
        sys.path.insert(0, str(MIX_ANALYZER_DIR))
    import subverse_mix  # noqa: WPS433 -- optional heavy deps (librosa, pyloudnorm)
    return subverse_mix


def _mix_path(raw: str) -> Path:
    path = Path(str(raw)).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"no audio file at {path}")
    return path


def handle_mix_measure(args: dict[str, Any]) -> dict[str, Any]:
    mix = _mix_module()
    path = _mix_path(args["path"])
    result = mix.analyze_audio(path, max_duration_seconds=float(args.get("max_duration_seconds") or mix.settings.max_analysis_seconds))
    return {"path": str(path), "engine": "subverse-mix-check", **result}


def handle_mix_profiles(_args: dict[str, Any]) -> dict[str, Any]:
    mix = _mix_module()
    store = mix.GenreProfileStore(mix.DEFAULT_PROFILES_PATH)
    return {"profiles": store.list(), "catalog": str(mix.DEFAULT_PROFILES_PATH),
            "notice": "Profiles are technical measurement distributions of released masters, not genre definitions."}


def handle_mix_analyze(args: dict[str, Any]) -> dict[str, Any]:
    mix = _mix_module()
    path = _mix_path(args["path"])
    store = mix.GenreProfileStore(mix.DEFAULT_PROFILES_PATH)
    profiles = store.all()
    genre_profile = None
    if args.get("genre"):
        genre_profile = store.get(str(args["genre"]))
        if genre_profile is None:
            raise ValueError(f"unknown genre profile {args['genre']!r}; known: {[p['id'] for p in profiles]}")
    reference = _mix_path(args["reference_path"]) if args.get("reference_path") else None
    result = mix.analyze_mix(
        path, path.name,
        reference_path=reference, reference_filename=reference.name if reference else None,
        selected_genre=args.get("genre"), genre_profile=genre_profile, genre_profiles=profiles,
        use_closest_profile=bool(args.get("use_closest_profile", False)),
        analysis_stage=str(args.get("analysis_stage") or "mix"),
        reference_stage=args.get("reference_stage"),
        max_duration_seconds=float(args.get("max_duration_seconds") or mix.settings.max_analysis_seconds),
    )
    if not args.get("include_waveform"):
        mix_features = result.get("mix")
        if isinstance(mix_features, dict) and "waveform" in mix_features:
            bins = (mix_features.get("waveform") or {}).get("bin_count")
            mix_features["waveform"] = {"omitted": True, "bin_count": bins, "note": "pass include_waveform=true for the envelope"}
    if not args.get("detail"):
        result = _compact_mix_result(result)
    return {"path": str(path), "engine": "subverse-mix-check", **result}


def _compact_mix_result(result: dict[str, Any]) -> dict[str, Any]:
    """The full Mix Check answer carries three 31-band tables and overflows
    the response limit; the compact form keeps every number that a finding
    can rest on and folds each table into one line per band."""
    compact = dict(result)
    mix = dict(compact.get("mix") or {})
    bands = mix.get("spectral_bands")
    if isinstance(bands, list):
        mix["spectral_bands"] = {str(b.get("center_hz")): b.get("loudness_relative_db") for b in bands if isinstance(b, dict)}
        mix["spectral_bands_note"] = "center_hz -> loudness-relative dB; detail=true for absolute levels per band"
    mono = mix.get("mono_compatibility")
    if isinstance(mono, dict):
        mono = dict(mono)
        mono_bands = mono.pop("bands", None)
        if isinstance(mono_bands, list):
            threshold = float(getattr(_mix_module().mix_analyzer, "MONO_LOSS_DETECTION_THRESHOLD_DB", -4.0))
            mono["bands_with_material_loss"] = [
                {"center_hz": b.get("center_hz"), "mono_loss_db": b.get("mono_loss_db")}
                for b in mono_bands if isinstance(b, dict) and b.get("active_for_detection") and (b.get("mono_loss_db") or 0) <= threshold
            ]
            mono["loss_threshold_db"] = threshold
            mono["band_count"] = len(mono_bands)
        mix["mono_compatibility"] = mono
    compact["mix"] = mix
    comparison = compact.get("comparison")
    if isinstance(comparison, dict):
        comparison = dict(comparison)
        deltas = comparison.pop("spectral_deltas", None)
        if isinstance(deltas, list):
            comparison["spectral_deltas"] = {str(d.get("center_hz")): d.get("delta_db") for d in deltas if isinstance(d, dict)}
        compact["comparison"] = comparison
    compact["compact"] = True
    return compact



# --- Crate agent (SubverseLab sample-reader + Sampler, ported 2026-09-03) ----
SAMPLE_AGENT_DIR = LOOM_DIR / "SampleAgent"


def _crate_module():
    if str(SAMPLE_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(SAMPLE_AGENT_DIR))
    import crate_agent  # noqa: WPS433 -- librosa, soundfile, yt-dlp/ffmpeg at call time
    return crate_agent


def handle_crate_fetch(args: dict[str, Any]) -> dict[str, Any]:
    return _crate_module().fetch(args["source"], start=args.get("start"), end=args.get("end"), workdir=args.get("workdir"))


def handle_crate_read(args: dict[str, Any]) -> dict[str, Any]:
    return _crate_module().read(args["path"])


def handle_crate_spots(args: dict[str, Any]) -> dict[str, Any]:
    return _crate_module().spots(args["path"], top=int(args.get("top") or 6), video_id=args.get("video_id"))


def handle_crate_chop(args: dict[str, Any]) -> dict[str, Any]:
    return _crate_module().chop(args["path"], **{k: v for k, v in args.items() if k != "path"})


def handle_crate_agent(args: dict[str, Any]) -> dict[str, Any]:
    return _crate_module().run(args["source"], **{k: v for k, v in args.items() if k != "source"})


def handle_crate_to_live(args: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(args["path"])).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"no audio file at {path}")
    payload: dict[str, Any] = {"op": "import_audio_clip", "path": str(path), "track": args["track"]}
    for key in ("slot", "start_beat", "duration_beats", "warped", "name"):
        if key in args:
            payload[key] = args[key]
    answer = bridge_client.submit_request(payload, float(args.get("wait_seconds", 20)))
    error = str(answer.get("error") or "")
    if (answer.get("status") == "FAILED_IN_LIVE" and (answer.get("outcome") or {}).get("code") == "import_into_project_failed"
            and args.get("import", True)):
        # Live's copy step failed before any clip existed (kind=failed,
        # applied=false; typically an unsaved set with no project folder):
        # reference the file where it is and say so. An indeterminate answer
        # is never retried here.
        retry = bridge_client.submit_request({**payload, "import": False}, float(args.get("wait_seconds", 20)))
        retry["import_fallback"] = {"reason": error[:200], "note": "the clip references the file in place; save the set to a project and re-run to let Live copy it"}
        return retry
    return answer


def handle_mix_from_live(args: dict[str, Any]) -> dict[str, Any]:
    rendered = bridge_client.submit_request({"op": "render_pre_fx", "track": args["track"],
                                             "start_beat": float(args["start_beat"]), "end_beat": float(args["end_beat"])},
                                            float(args.get("wait_seconds", 60)))
    result = rendered.get("result") or {}
    if rendered.get("status") != "OK" or not result.get("path"):
        return {"render": rendered, "measurement": None, "note": "no render came back; see render.status and render.outcome"}
    path = Path(str(result["path"]))
    if not path.is_file():
        return {"render": rendered, "measurement": None, "error": f"Live reported a render at {path} but the MCP cannot read it"}
    if args.get("analysis") == "analyze":
        measurement = handle_mix_analyze({"path": str(path), "analysis_stage": args.get("analysis_stage") or "mix"})
    else:
        measurement = handle_mix_measure({"path": str(path)})
    return {"render": rendered, "measurement": measurement}


# --- Live playback capture (Core Audio process tap) --------------------------
LIVETAP_DIR = LOOM_DIR / "MixAnalyzer" / "livetap"
LIVETAP_SRC = LIVETAP_DIR / "main.swift"
LIVETAP_PLIST = LIVETAP_DIR / "Info.plist"
LIVETAP_APP = LIVETAP_DIR / "LiveTap.app"
LIVETAP_BIN = LIVETAP_APP / "Contents" / "MacOS" / "livetap"
MIX_CAPTURE_DIR = LOOM_DIR / "Sessions" / "MixCaptures"


def _livetap_binary() -> Path:
    """Build the tap tool on first use as a signed app bundle. macOS only
    prompts for System Audio Recording when the caller is an app whose
    Info.plist carries NSAudioCaptureUsageDescription; a bare binary is
    refused silently and never appears in Privacy & Security."""
    fresh = LIVETAP_BIN.exists() and LIVETAP_BIN.stat().st_mtime >= max(LIVETAP_SRC.stat().st_mtime, LIVETAP_PLIST.stat().st_mtime)
    if fresh:
        return LIVETAP_BIN
    LIVETAP_BIN.parent.mkdir(parents=True, exist_ok=True)
    build = subprocess.run(["swiftc", "-O", "-o", str(LIVETAP_BIN), str(LIVETAP_SRC)], capture_output=True, text=True, timeout=300)
    if build.returncode != 0:
        raise RuntimeError(f"livetap build failed: {build.stderr.strip()[-800:]}")
    shutil.copy2(LIVETAP_PLIST, LIVETAP_APP / "Contents" / "Info.plist")
    subprocess.run(["codesign", "--force", "--sign", "-", str(LIVETAP_APP)], capture_output=True, text=True, timeout=120)
    return LIVETAP_BIN


def _live_pid() -> int:
    out = subprocess.run(["pgrep", "-f", "Ableton Live.*Contents/MacOS/Live"], capture_output=True, text=True)
    pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    if not pids:
        raise RuntimeError("no running Ableton Live process")
    if len(pids) > 1:
        raise RuntimeError(f"more than one Live is running ({pids}); close the other one")
    return pids[0]


def _measure_capture(path: Path, args: dict[str, Any]) -> dict[str, Any]:
    if args.get("analysis") == "measure":
        return handle_mix_measure({"path": str(path)})
    return handle_mix_analyze({"path": str(path), "analysis_stage": args.get("analysis_stage") or "master",
                               "genre": args.get("genre"), "use_closest_profile": bool(args.get("use_closest_profile", True))})


def _mix_capture_resample(_args: dict[str, Any]) -> dict[str, Any]:
    """Live recording itself needed record mode, resampling routing and arming,
    none of which the Extensions SDK exposes. Reported, not emulated."""
    return {"status": "UNSUPPORTED_BY_SDK", "method": "resample", "capability": "recording",
            "error": "unsupported_by_sdk: resample capture needs record mode, resampling input routing and arming, "
                     "which the Extensions SDK does not expose; use method='tap'"}


def handle_mix_capture(args: dict[str, Any]) -> dict[str, Any]:
    if (args.get("method") or "tap") == "resample":
        return _mix_capture_resample(args)
    if args.get("follow_transport"):
        return {"status": "UNSUPPORTED_BY_SDK", "capability": "transport",
                "error": "unsupported_by_sdk: follow_transport needs the transport state, which the Extensions SDK "
                         "does not expose; start playback and call again with a fixed 'seconds'"}
    binary = _livetap_binary()
    pid = _live_pid()
    seconds = float(args.get("seconds") or 8)
    MIX_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = MIX_CAPTURE_DIR / f"live-{stamp}.wav"
    started = time.time()
    # Launched through LaunchServices, not as a child process: macOS charges
    # the System Audio Recording permission to the RESPONSIBLE process, and a
    # child of the MCP is charged to whatever host runs the MCP (Claude,
    # Terminal, an IDE). Measured 2026-09-06: as a child the tap captured
    # silence with LiveTap's own permission granted; via `open -a` it captured
    # Live at peak 0.25. So the permission belongs to "LiveTap", once.
    with tempfile.TemporaryDirectory(prefix="loom_livetap_") as scratch:
        out_path, err_path = Path(scratch) / "stdout.txt", Path(scratch) / "stderr.txt"
        launch = subprocess.run(["open", "-W", "--stdout", str(out_path), "--stderr", str(err_path), "-a", str(binary.parents[2]),
                                 "--args", "--pid", str(pid), "--seconds", f"{seconds:g}", "--out", str(path)],
                                capture_output=True, text=True, timeout=seconds + 30)
        stdout = out_path.read_text(encoding="utf-8", errors="ignore") if out_path.exists() else ""
        stderr = err_path.read_text(encoding="utf-8", errors="ignore") if err_path.exists() else launch.stderr
    report: dict[str, Any] = {}
    if stdout.strip():
        try:
            report = json.loads(stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            report = {"raw": stdout[-400:]}
    result: dict[str, Any] = {"pid": pid, "path": str(path), "capture": report, "seconds_requested": seconds,
                              "elapsed_seconds": round(time.time() - started, 2), "launched_via": "LaunchServices (open -a LiveTap.app)"}
    if launch.returncode != 0 or not report or not path.exists():
        result["status"] = "CAPTURE_FAILED"
        result["error"] = (stderr.strip() or launch.stderr.strip() or "the tap wrote no report")[-600:]
        result["note"] = "No frames came back. In System Settings > Privacy & Security > Screen & System Audio Recording, 'LiveTap' must be allowed; then try again."
        return result
    if report.get("peak", 0) == 0:
        result["status"] = "SILENT"
        result["note"] = (report.get("permission_hint") or "the capture is silent") + " -- the permission that matters is the 'LiveTap' entry, and Live must be playing (the SDK cannot tell)."
        return result
    result["status"] = "OK"
    result["method"] = "tap"
    result["measurement"] = _measure_capture(path, args)
    if not args.get("keep", True):
        path.unlink(missing_ok=True)
        result["path"] = None
    return result

def _open_set_info() -> dict[str, Any]:
    """Which set is open (name/path), from Live's log and window title: the SDK
    has no such field. Disabled under a redirected bridge root so tests never
    read the real Live's log or drive System Events."""
    if os.environ.get("LOOM_BRIDGE_ROOT"):
        return {"kind": "unknown", "sources": [], "note": "disabled while LOOM_BRIDGE_ROOT redirects the bridge (test isolation)"}
    try:
        import live_project as lp  # noqa: PLC0415  (mcp_server is not a package)

        return lp.open_set()
    except Exception as error:  # noqa: BLE001 -- the set name is information, never a blocker
        return {"kind": "unknown", "sources": [], "error": str(error)}


def handle_live_bridge_status(_args: dict[str, Any]) -> dict[str, Any]:
    """The one connection diagnosis: which extension bridge would be used, why,
    what it can do, and what is waiting in its queue."""
    report: dict[str, Any] = {"endpoint": "loom_extension", "fallback": None, "open_set": _open_set_info(),
                              "supported_protocols": list(bridge_client.SUPPORTED_BRIDGE_PROTOCOLS),
                              "bridge_candidates": bridge_client.bridge_candidates(),
                              "unsupported_by_sdk": {op: cap for op, (cap, _why) in bridge_client.SDK_UNSUPPORTED_OPS.items()}}
    try:
        target = resolve_bridge_target()
    except BridgeUnavailable as error:
        report.update({"available": False, "mutations_allowed": False, "bridge_root": None, "bridge_root_source": error.status,
                       "error": str(error), "candidates": error.candidates, "legacy_bridges": bridge_client.legacy_report(None)})
        return report
    protocol = target.protocol_report()
    report["legacy_bridges"] = bridge_client.legacy_report(target)
    report.update({
        "available": target.state is not None,
        "mutations_allowed": protocol["compatible"],
        "protocol": protocol,
        "bridge_root": str(target.root),
        "bridge_root_source": target.source,
        "bridge": target.describe(),
        "pending_requests": sorted(p.name for p in target.requests.glob("*.json")) if target.requests.exists() else [],
        "in_flight": sorted(p.name for p in target.processing.glob("*.json")) if target.processing.exists() else [],
        "recent_done": sorted([p.name for p in target.done.glob("*.json")], reverse=True)[:5] if target.done.exists() else [],
        "recent_errors": sorted([p.name for p in target.errors.glob("*.json")], reverse=True)[:5] if target.errors.exists() else [],
    })
    return report


def handle_gap_record(args: dict[str, Any]) -> dict[str, Any]:
    GAP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing_content = GAP_LOG_PATH.read_text(encoding="utf-8") if GAP_LOG_PATH.exists() else "# Ableton Live Missing Controls & Gap Log\n\n"

    count = existing_content.count("### GAP-")
    gap_id = f"GAP-{count + 1:03d}"
    timestamp = datetime.datetime.now().astimezone().isoformat()

    entry = f"""
### {gap_id}
- **Timestamp**: {timestamp}
- **Category**: {args['category']}
- **Description**: {args['description']}
- **Observed Behavior**: {args['observed_behavior']}
- **Required Implementation**: {args['required_implementation']}
- **Status**: OPEN
"""
    GAP_LOG_PATH.write_text(existing_content + entry, encoding="utf-8")
    return {
        "status": "RECORDED",
        "gap_id": gap_id,
        "file": str(GAP_LOG_PATH),
        "entry": entry.strip()
    }


def dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    handlers = {
        "part_suggest": handle_part_suggest,
    "genre_evidence": handle_genre_evidence,
    "midi_generate": handle_midi_generate,
        "midi_write_to_live": handle_midi_write_to_live,
    "midi_write_arrangement": handle_midi_write_arrangement,
        "project_inspect": handle_project_inspect,
        "project_detect_genre": handle_project_detect_genre,
        "project_analyze_mixer": handle_project_analyze_mixer,
        "project_build": handle_project_build,
        "project_file_build": handle_project_file_build,
    "plan_create": handle_plan_create,
        "library_search": handle_library_search,
        "render_plan": handle_render_plan,
        "live_bridge_status": handle_live_bridge_status,
        "mix_measure": handle_mix_measure,
        "mix_analyze": handle_mix_analyze,
        "mix_profiles": handle_mix_profiles,
        "crate_fetch": handle_crate_fetch,
        "crate_read": handle_crate_read,
        "crate_spots": handle_crate_spots,
        "crate_chop": handle_crate_chop,
        "crate_agent": handle_crate_agent,
        "crate_to_live": handle_crate_to_live,
        "mix_from_live": handle_mix_from_live,
        "mix_capture": handle_mix_capture,
        "setup_scan": handle_setup_scan,
        "gap_record": handle_gap_record,
        "plan_verify": handle_plan_verify,
        "project_inspect_arrangement": handle_project_inspect_arrangement,
        "drumbuss_build": handle_drumbuss_build,
        "projects_arrangement_shapes": handle_projects_arrangement_shapes,
        "project_analyze_clips": handle_project_analyze_clips,
        "automation_read": handle_automation_read,
        "drumbuss_read": handle_drumbuss_read,
        "automation_write": handle_automation_write,
        "automation_list_targets": handle_automation_list_targets,
        "render_verify": handle_render_verify,
        "live_state": handle_live_state,
        "live_command": handle_live_command,
        "live_project": handle_live_project,
        "chain_plan": handle_chain_plan,
        "chain_evidence": handle_chain_evidence,
        "chain_apply": handle_chain_apply,
        "palette_read": handle_palette_read,
        "project_sound_sources": handle_project_sound_sources,
    }
    if name not in handlers:
        raise ValueError(f"Unknown tool: {name}")
    return handlers[name](args)


# --- 4) Long work: concurrency, progress, cancellation ---------------------
# The previous version was one serial loop: a 20-project scan blocked
# everything, ping included, for a measured 7 seconds. Tool calls now run on a
# pool and stdout is written behind a single lock.
MAX_CONCURRENT_TOOL_CALLS = 4
# The longest a tool may keep the client waiting. This is NOT a hard limit:
# a Python thread cannot be killed from outside. When the time is up the client
# gets a timeout error and the request is marked cancelled -- a tool that calls
# check_cancelled() stops at once, one that does not keeps running in the
# background until it finishes and its result is discarded. Stated in the
# README as such.
DEFAULT_TOOL_TIMEOUT_SECONDS = 300
# Tarama araclari tum kutuphaneyi dolasabilir; olculen tam tarama ~200 sn.
TOOL_TIMEOUT_OVERRIDES = {
    "projects_arrangement_shapes": 900,
    "plan_create": 600,
    "project_file_build": 600,
}
_stdout_lock = threading.Lock()
_cancelled_requests: set[Any] = set()
_cancel_lock = threading.Lock()

_current_request = contextvars.ContextVar("current_request", default=None)
_current_progress_token = contextvars.ContextVar("current_progress_token", default=None)


def write_message(message: dict[str, Any]) -> None:
    with _stdout_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def mark_cancelled(request_id: Any) -> None:
    with _cancel_lock:
        _cancelled_requests.add(request_id)


def _clear_cancelled(request_id: Any) -> None:
    with _cancel_lock:
        _cancelled_requests.discard(request_id)


class ToolCancelled(RuntimeError):
    pass


def check_cancelled() -> None:
    """Long loops call this. Cancellation is cooperative -- a Python thread
    cannot be killed from outside, but a loop can stop itself."""
    request_id = _current_request.get()
    if request_id is None:
        return
    with _cancel_lock:
        if request_id in _cancelled_requests:
            raise ToolCancelled(f"cancelled_by_client: request {request_id}")


def report_progress(progress: float, total: float | None = None, message: str | None = None) -> None:
    token = _current_progress_token.get()
    if token is None:
        return
    params: dict[str, Any] = {"progressToken": token, "progress": progress}
    if total is not None:
        params["total"] = total
    if message:
        params["message"] = message
    write_message({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})


# The bridge client refuses to start a mutation for a cancelled call and
# reports its waiting as progress; it gets both primitives from here.
bridge_client.bind(check_cancelled=check_cancelled, report_progress=report_progress, cancelled_type=ToolCancelled)


# --- 1) Notifications ------------------------------------------------------
# JSON-RPC 2.0: a message with no "id" is a notification and is NEVER answered.
# The previous version replied to unknown notifications with
# {"id": null, "error": ...}; a strict client can treat that as a protocol
# violation and drop the connection.

# --- 7) Protocol version negotiation ---------------------------------------
# If the version the client asks for is supported it is returned, otherwise the
# newest one we support. The previous version ignored the request entirely and
# wrote a hardcoded value.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

SERVER_VERSION = "2.0.0"


def negotiate_protocol_version(requested: Any) -> str:
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return LATEST_PROTOCOL_VERSION


# --- Pagination ------------------------------------------------------------
# tools/list measured 12.6 KB (~3.2K tokens) on every call. The spec defines
# cursor-based pagination; the cursor is only an offset, so it is encoded into
# an opaque string -- clients must not rely on its contents.
PAGE_SIZE = 10


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


def _decode_cursor(cursor: Any) -> int:
    if cursor is None:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(str(cursor).encode("ascii")).decode("ascii")))
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"invalid_cursor: {cursor}") from error


def paginate(items: list[dict[str, Any]], cursor: Any, key: str) -> dict[str, Any]:
    offset = _decode_cursor(cursor)
    page = items[offset : offset + PAGE_SIZE]
    result: dict[str, Any] = {key: page}
    if offset + PAGE_SIZE < len(items):
        result["nextCursor"] = _encode_cursor(offset + PAGE_SIZE)
    return result


def _error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


def _tool_result(req_id: Any, payload: Any) -> dict[str, Any]:
    text, truncation = render_tool_text(payload)
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
    if isinstance(payload, dict):
        result["structuredContent"] = payload
    if truncation:
        result["content"].append({"type": "text", "text": truncation})
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _tool_error(req_id: Any, tool_name: str, detail: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "content": [{"type": "text", "text": f"Error executing tool '{tool_name}': {detail}"}],
            "isError": True,
        },
    }


def handle_request(req: dict[str, Any]) -> dict[str, Any] | None:
    """Bir istegi isler. Bildirimlerde (id yok) None doner -- yanit yazilmaz."""
    is_notification = "id" not in req
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if is_notification:
        if method == "notifications/cancelled":
            target = (params or {}).get("requestId")
            mark_cancelled(target)
            log_debug(f"cancellation requested for {target}")
        else:
            log_debug(f"notification: {method}")
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": negotiate_protocol_version(params.get("protocolVersion")),
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": "loom-mcp", "version": SERVER_VERSION},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": paginate(TOOLS, params.get("cursor"), "tools")}

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": paginate(list_resources(), params.get("cursor"), "resources")}

    if method == "resources/read":
        try:
            return {"jsonrpc": "2.0", "id": req_id, "result": {"contents": read_resource(params.get("uri"))}}
        except KeyError as error:
            return _error(req_id, -32002, str(error))
        except Exception as error:  # noqa: BLE001
            return _error(req_id, -32603, f"resource read failed: {error}")

    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": paginate(list_prompts(), params.get("cursor"), "prompts")}

    if method == "prompts/get":
        try:
            return {"jsonrpc": "2.0", "id": req_id, "result": get_prompt(params.get("name"), params.get("arguments") or {})}
        except KeyError as error:
            return _error(req_id, -32602, str(error))

    if method == "tools/call":
        tool_name = params.get("name")
        raw_args = params.get("arguments") or {}
        schema = TOOL_SCHEMAS.get(tool_name)
        if schema is None:
            return _tool_error(req_id, str(tool_name), f"Unknown tool: {tool_name}")
        try:
            arguments = validate_arguments(tool_name, raw_args, schema)
        except ToolArgumentError as error:
            return _tool_error(req_id, tool_name, str(error))
        token = (params.get("_meta") or {}).get("progressToken")
        request_token = _current_request.set(req_id)
        progress_token = _current_progress_token.set(token)
        try:
            return _tool_result(req_id, dispatch_tool(tool_name, arguments))
        except ToolCancelled as error:
            return _tool_error(req_id, tool_name, str(error))
        except Exception as error:  # noqa: BLE001
            log_debug(f"Tool error in {tool_name}: {error}")
            return _tool_error(req_id, tool_name, f"{type(error).__name__}: {error}")
        finally:
            _current_request.reset(request_token)
            _current_progress_token.reset(progress_token)
            _clear_cancelled(req_id)

    return _error(req_id, -32601, f"Method '{method}' not found")


def main() -> None:
    log_debug(f"Starting Loom MCP stdio server v{SERVER_VERSION}")
    # Arac cagrilari havuzda; initialize/ping/list gibi ucuz metotlar satir
    # icinde. Boylece uzun bir tarama surerken sunucu yanit vermeye devam eder.
    executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_TOOL_CALLS, thread_name_prefix="mcp-tool")
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as err:
                log_debug(f"JSON decode error: {err}")
                write_message(_error(None, -32700, "Parse error"))
                continue

            if not isinstance(req, dict):
                write_message(_error(None, -32600, "Invalid Request"))
                continue

            if req.get("method") == "tools/call" and "id" in req:
                _dispatch_with_timeout(req, executor)
                continue

            # Satir ici metotlar da hata firlatabilir (orn. bozuk imlec);
            # yakalanmazsa dongu olur ve istemci yanitsiz kalir.
            try:
                resp = handle_request(req)
            except ValueError as error:
                resp = _error(req.get("id"), -32602, str(error))
            except Exception as error:  # noqa: BLE001
                log_debug(f"unhandled error: {error}")
                resp = _error(req.get("id"), -32603, f"internal error: {error}")
            if resp is not None:
                write_message(resp)
    finally:
        executor.shutdown(wait=True)
        with _watchers_lock:
            pending = list(_watchers)
        for thread in pending:
            thread.join(timeout=5)


def _run_and_reply(req: dict[str, Any]) -> None:
    try:
        resp = handle_request(req)
    except Exception as error:  # noqa: BLE001
        log_debug(f"unhandled error: {error}")
        resp = _error(req.get("id"), -32603, f"internal error: {error}")
    if resp is not None:
        write_message(resp)


# Gozcu is parcaciklari yaniti future bittikten SONRA yazar. stdin kapaninca
# hemen cikilirsa yazilmamis yanitlar kaybolur -- kapanista bunlar beklenir.
_watchers: list[threading.Thread] = []
_watchers_lock = threading.Lock()


def _dispatch_with_timeout(req: dict[str, Any], executor: ThreadPoolExecutor) -> None:
    """Aracı havuzda başlatır ve süresi dolarsa istemciyi bekletmez."""
    request_id = req.get("id")
    tool_name = ((req.get("params") or {}).get("name")) or ""
    timeout = TOOL_TIMEOUT_OVERRIDES.get(tool_name, DEFAULT_TOOL_TIMEOUT_SECONDS)
    future = executor.submit(handle_request, req)

    def watcher() -> None:
        try:
            resp = future.result(timeout=timeout)
        except FuturesTimeout:
            # Isbirlikci iptali tetikle; cevabi simdi ver, is arkada bitsin.
            mark_cancelled(request_id)
            log_debug(f"tool timeout after {timeout}s: {tool_name} (request {request_id})")
            write_message(_tool_error(
                request_id, tool_name,
                f"tool_timeout: exceeded {timeout}s. The call was marked cancelled; "
                f"a tool that does not poll for cancellation may still be finishing in the background.",
            ))
            return
        except Exception as error:  # noqa: BLE001
            log_debug(f"unhandled error: {error}")
            write_message(_error(request_id, -32603, f"internal error: {error}"))
            return
        if resp is not None:
            write_message(resp)

    thread = threading.Thread(target=watcher, daemon=True, name=f"mcp-watch-{request_id}")
    with _watchers_lock:
        _watchers.append(thread)
    thread.start()


if __name__ == "__main__":
    main()
