#!/usr/bin/env python3
"""Loom Full Suite MCP Server for Ableton Live Integration.

Connects all Loom capabilities:
- Sensei: Dataset-pinned MIDI variation engine & safe target resolver
- AIMixMaster: Ableton Live Set (.als) project inspection, genre detection, mixer & routing analysis, gain staging
- ArrangementGPS: Arrangement timeline action lists & Ableton library indexing
- Presetor / AISoundDesigner: Preset discovery & device chain templating
- Renderer: Stem export manifests & render job structuring
- Bridge & Telemetry: SenseiV2Bridge status & gap tracking ledger
"""

from __future__ import annotations

import datetime
import glob
import gzip
import json
import base64
import contextvars
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

LOOM_DIR = Path(__file__).resolve().parents[1]
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

# The bridge directory is overridable so a test can talk to a bridge of its own.
# It used to be fixed, so the live-bridge test had to exercise the real one -- and
# with Live actually running, the session picked up the test's requests and its
# tempo really changed. A test must not be able to reach the user's session.
BRIDGE_ROOT = Path(os.environ.get("LOOM_BRIDGE_ROOT")
                   or Path.home() / "Documents" / "SenseiV2Bridge")
REQUEST_DIR = BRIDGE_ROOT / "requests"
DONE_DIR = BRIDGE_ROOT / "done"
ERROR_DIR = BRIDGE_ROOT / "errors"
PROCESSED_DIR = BRIDGE_ROOT / "processed"
DEFAULT_SURFACE_ROOT = Path.home() / "Documents" / "SenseiV2Bridge"
STATE_FRESH_SECONDS = 10.0


def _bind_bridge_root(root: Path) -> None:
    """Point every bridge path at `root`. Called once at import and again by
    _select_bridge_root when a live extension bridge is found."""
    global BRIDGE_ROOT, REQUEST_DIR, DONE_DIR, ERROR_DIR, PROCESSED_DIR, STATE_DIR, STATE_FILE
    BRIDGE_ROOT = root
    REQUEST_DIR = root / "requests"
    DONE_DIR = root / "done"
    ERROR_DIR = root / "errors"
    PROCESSED_DIR = root / "processed"
    STATE_DIR = root / "state"
    STATE_FILE = STATE_DIR / "live_state.json"


def _state_freshness(root: Path) -> tuple[float | None, str | None]:
    """(age in seconds, surface_version) of the state a bridge root last
    published, or (None, None) if it never did."""
    state_file = root / "state" / "live_state.json"
    if not state_file.exists():
        return None, None
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        captured = float(state.get("captured_at") or 0)
    except Exception:  # noqa: BLE001
        return None, None
    return (time.time() - captured if captured else None), state.get("surface_version")


def _extension_bridge_roots() -> list[Path]:
    data = Path.home() / "Library" / "Application Support" / "Ableton" / "Extensions Data"
    if not data.exists():
        return []
    return sorted(p / "bridge" for p in data.iterdir() if (p / "bridge" / "state" / "live_state.json").exists())


def _select_bridge_root() -> str:
    """Which Live-side endpoint to talk to, decided per call.

    LOOM_BRIDGE_ROOT (or a test rebinding the paths) wins outright. Otherwise
    a *fresh* extension bridge is preferred -- that is the one-install path,
    the user only added the .ablx -- and the control surface's root is the
    fallback. Returns why, for the status tool."""
    if os.environ.get("LOOM_BRIDGE_ROOT"):
        return "LOOM_BRIDGE_ROOT"
    if BRIDGE_ROOT not in (DEFAULT_SURFACE_ROOT, *_extension_bridge_roots()):
        return "rebound_by_caller"
    for root in _extension_bridge_roots():
        age, version = _state_freshness(root)
        if age is not None and age < STATE_FRESH_SECONDS and str(version or "").startswith("loom-extension"):
            if root != BRIDGE_ROOT:
                _bind_bridge_root(root)
            return "fresh_extension_bridge"
    if BRIDGE_ROOT != DEFAULT_SURFACE_ROOT:
        _bind_bridge_root(DEFAULT_SURFACE_ROOT)
    return "control_surface_default"

GAP_LOG_PATH = DOCS_DIR / "MISSING_CONTROLS_LOG.md"


def log_debug(msg: str) -> None:
    sys.stderr.write(f"[loom-mcp] {msg}\n")
    sys.stderr.flush()


def ensure_bridge_dirs() -> None:
    for d in (REQUEST_DIR, DONE_DIR, ERROR_DIR, PROCESSED_DIR, DOCS_DIR):
        d.mkdir(parents=True, exist_ok=True)


ROOT_MAP = {
    "0": "C", "1": "C#", "2": "D", "3": "D#", "4": "E", "5": "F",
    "6": "F#", "7": "G", "8": "G#", "9": "A", "10": "A#", "11": "B"
}

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


TOOLS = [
    # 1. Sensei Tools
    {
        "name": "part_suggest",
        "description": "Write a chord progression or bass line FOR A SPECIFIC PROJECT. Reads the project's own key, scale and tempo first, walks a chord sequence through transitions measured from 909 annotated songs, and returns notes already in that project's key and beats -- so the part belongs to the session rather than having to be bent to fit it. This is the thing a prompt-driven generator cannot do: it does not know your key or your tempo. Returns what it read from the project and what it counted, so every choice is traceable. A layer with no measured evidence is refused, not guessed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "als_path": {"type": "string", "description": "The .als to read the musical context from. Omit to use the running Live session."},
                "layer": {"type": "string", "enum": ["chord", "bass"], "description": "Which part to write."},
                "bars": {"type": "integer", "description": "Length in bars.", "default": 8},
                "chords_per_bar": {"type": "integer", "description": "Harmonic rhythm. 1 is one chord a bar; 2 is half-bar changes.", "default": 1},
                "seed": {"type": "integer", "description": "Same seed and same project give the same part.", "default": 7},
                "octave": {"type": "integer", "description": "Octave for the chord voicing.", "default": 3}
            },
            "required": ["layer"]
        }
    },
    {
        "name": "genre_evidence",
        "description": "Musical evidence measured from open corpora of real performances, served one layer at a time. 'drum' returns where each drum part falls on the bar for a style, counted from 1,150 human drummer takes. 'bass' returns how the bass sits against the chord and how far it moves. 'chord' returns degree transitions and melodic intervals. 'arrangement' returns song-level shape -- chord counts, loop lengths, modes -- which is what ArrangementGPS needs to build a project rather than write notes. Layers are kept apart on purpose: a bass line judged by a kick pattern answers the drum question, not the bass one. A style that was never measured returns nothing instead of an approximation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "enum": ["drum", "bass", "chord", "arrangement"], "description": "Which layer's evidence to return."},
                "style": {"type": "string", "description": "For the drum layer: rock, funk, jazz, hiphop, latin, reggae, soul, country, punk, gospel, afrobeat, afrocuban, neworleans, pop. Trap/rap map to hiphop, r&b to soul."},
                "song_maps": {"type": "integer", "description": "For the arrangement layer: how many per-song maps to include (0 for the summary only)."}
            },
            "required": ["layer"]
        }
    },
    {
        "name": "midi_generate",
        "description": "Generate evidenced MIDI variations using Sensei's locked dataset and variation runtime for a verified target role/preset.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "preset_path": {"type": "string", "description": "Optional path or name of the Suite native instrument preset."},
                "explicit_profile_id": {"type": "string", "description": "Explicit instrument profile ID (e.g. 'ableton.bass.808.v1', 'ableton.bass.synth.v1', 'ableton.chord.piano.v1', 'ableton.chord.pad.v1')."},
                "role": {"type": "string", "enum": ["bass", "chord", "drum"], "description": "Musical role of the target track."},
                "genre": {"type": "string", "description": "Native Ableton genre (e.g. 'Trap', 'Hip Hop', 'House', 'Techno', 'Ambient').", "default": "Trap"},
                "bars": {"type": "integer", "description": "Number of bars to generate (e.g. 2, 4, 8).", "default": 4},
                "seed": {"type": "integer", "description": "Random seed for deterministic generation.", "default": 42},
                "variation_amount": {"type": "number", "description": "Variation intensity (0.0 to 1.0).", "default": 0.35},
                "genre_style": {"type": "string", "description": "Rank candidates by how well they match drum patterns measured from real performances of this style (rock, funk, jazz, hiphop, latin, reggae, soul, country, punk, gospel, afrobeat, afrocuban, neworleans, pop). Trap/rap/boom bap map to hiphop, r&b to soul. A style with no measured pattern is reported back untouched rather than approximated."},
                "density": {"type": "number", "description": "How busy the part should be, 0.0 sparse to 1.0 busy -- an intro against a final hook. Selects a pattern from the corpus that already has that note count for the role; notes are never dropped from a denser one. Omit to leave the whole pool in play. Reported back under diagnostics.density_applied, which is false when the pool was too small to band."},
                "target_root": {"type": "string", "description": "Key root note (e.g. 'C', 'D#', 'F', 'A').", "default": "C"},
                "target_mode": {"type": "string", "enum": ["Major", "Minor"], "description": "Scale mode.", "default": "Minor"},
                "auto_write_to_live": {"type": "boolean", "description": "If true, immediately queues generated notes to Ableton Live via SenseiV2Bridge.", "default": False}
            }
        }
    },
    {
        "name": "midi_write_arrangement",
        "description": "Write MIDI notes into the ARRANGEMENT of a running Live session -- a named track, a bar position, a length -- through the Loom control surface. This is the single Live-side trigger: the same surface install.py installs handles it, so nothing else has to be loaded into Live. A clip of the same name overlapping the range is replaced, not stacked, so rebuilding a section is safe. Waits for Live to consume the request and reports the note count Live actually holds; NOT_CONSUMED means Live did not answer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track": {"type": "string", "description": "Track name in the session. Omit to use the selected track."},
                "start_bar": {"type": "integer", "description": "1-based bar the clip starts on, converted with the session's own time signature."},
                "start_beat": {"type": "number", "description": "Alternative to start_bar: absolute start in beats."},
                "beats_per_bar": {"type": "number", "description": "Beats in a bar. Omit to take it from the running session's own time signature (or the .als if als_path is given); 4/4 is assumed only when neither is available, and the response says which."},
                "length_beats": {"type": "number", "description": "Clip length in beats.", "default": 16},
                "name": {"type": "string", "description": "Clip name, e.g. the section: Intro, Verse 1, Hook.", "default": "Loom"},
                "notes": {"type": "array", "items": {"type": "object", "properties": {"pitch": {"type": "integer"}, "start": {"type": "number"}, "duration": {"type": "number"}, "velocity": {"type": "integer"}}, "required": ["pitch", "start", "duration"]}, "description": "Notes relative to the clip start, in beats."},
                "wait_seconds": {"type": "number", "description": "How long to wait for Live to consume the request.", "default": 15}
            },
            "required": ["notes"]
        }
    },
    {
        "name": "midi_write_to_live",
        "description": "Write MIDI notes into Ableton Live through SenseiV2Bridge and wait for Live to actually consume the request. Reports WRITTEN_TO_LIVE, REJECTED_BY_LIVE or NOT_CONSUMED -- it does not just queue and claim success.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the generated clip in Ableton Live."},
                "notes": {
                    "type": "array",
                    "description": "List of note objects with pitch, start, duration, velocity.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pitch": {"type": "integer", "description": "MIDI pitch (0-127)."},
                            "start": {"type": "number", "description": "Start position in beats."},
                            "duration": {"type": "number", "description": "Duration in beats."},
                            "velocity": {"type": "integer", "description": "Velocity (1-127)."}
                        },
                        "required": ["pitch", "start", "duration", "velocity"]
                    }
                },
                "length_beats": {"type": "number", "description": "Total length of the clip in beats (default 16.0 = 4 bars in 4/4).", "default": 16.0},
                "prompt": {"type": "string", "description": "Optional descriptive text or prompt string for audit trail."},
                "wait_seconds": {"type": "number", "description": "How long to wait for Live to actually consume the request before reporting. 0 queues blindly without verifying anything.", "default": 15}
            },
            "required": ["name", "notes"]
        }
    },
    # 2. AIMixMaster Tools
    {
        "name": "project_inspect",
        "description": "Inspect an Ableton Live Set (.als) file in detail: tempo, key, scale, Camelot code, track breakdown, mute states, and active devices.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "als_path": {"type": "string", "description": "Absolute or relative path to the .als project file."}
            },
            "required": ["als_path"]
        }
    },
    {
        "name": "project_detect_genre",
        "description": "Analyze track names, arrangement density, and instrument presence in an .als file to predict multi-label genre tags.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "als_path": {"type": "string", "description": "Path to the .als file."}
            },
            "required": ["als_path"]
        }
    },
    {
        "name": "project_analyze_mixer",
        "description": "Full gain-staging and mixer analysis of an .als -- real fader values, Utility gains, routing kind, send routes, and master-chain limiter/clipper/compressor detection, with a markdown report. XML only; peak/RMS/LUFS targets need rendered audio.",
        "inputSchema": {
            "type": "object",
            "properties": {"als_path": {"type": "string", "description": "Absolute path to the .als file."}},
            "required": ["als_path"]
        }
    },
    # 3. ArrangementGPS Tools
    {
        "name": "project_build",
        "description": "THE single trigger: build a whole project into the running Live session from one prompt. Runs plan_create, then for every section and every track Sensei can write (drum, bass, chord) generates a part in the project's own key and tempo -- the section's energy as density, the plan's genre as genre_style -- and writes it into the Arrangement through the Loom control surface, with a locator per section. Before writing it creates every track the plan names that the set does not have yet (loading the plan's instrument family from the browser) and sets the song key, so an empty set really is built from scratch; an existing track of the same name is adopted, never duplicated. Everything goes through the one surface install.py installs; no extension, nothing else to load into Live. Dry run by default: it reports exactly what it would write, per track and section, and touches Live only with dry_run=false. Parts Sensei cannot write (melody, vocal, fx lanes) are reported as out of scope, not as failures.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Musical brief, e.g. 'dark rolling tech house, 126 bpm, in F minor'. Omit when plan_path is given."},
                "plan_path": {"type": "string", "description": "Use an existing session plan instead of running plan_create -- for a rebuild, or to test the write list without the Node chain."},
                "dry_run": {"type": "boolean", "description": "true: report the write list, change nothing in Live. false: write.", "default": True},
                "wait_seconds": {"type": "number", "description": "Per request, how long to wait for Live to consume it.", "default": 15},
                "seed": {"type": "integer", "description": "Base seed; each track and section derives its own from it.", "default": 7},
                "beats_per_bar": {"type": "number", "description": "Beats in a bar. Omit to read the running session's time signature; 4/4 is assumed only as a last resort and reported as such."}
            }
        }
    },
    {
        "name": "plan_create",
        "description": "Build a project from scratch: run the real pipeline from a text prompt (blueprint -> build plan -> session plan -> package -> action list) and write a build directory ArrangementGPSBuilder picks up in Live. Tempo, key, mode, genre and instrument choice are derived from the prompt. Each section carries a 0-100 energy and the plan carries a genre; when the Live-side writer fills the sections it hands Sensei that energy as density (an intro is written from a pattern that is already sparse, a final hook from one already busy) and the genre as genre_style (candidates ranked against drum patterns measured from real performances). Every part is written in the project's own key and tempo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Free-text musical brief, e.g. 'dark rolling tech house, 126 bpm, hypnotic bassline'. State a tempo as '<n> bpm' and a key as 'in F minor' to have them honoured."}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "library_search",
        "description": "Search Sensei's preset identity catalog (role- and genre-tagged, so a hit is something Sensei can actually generate for) with an optional filesystem fallback for name-only lookups.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Substring of the preset name (lowercase match)."},
                "role": {"type": "string", "enum": ["drum", "bass", "chord"], "description": "Restrict to presets Sensei resolves to this role."},
                "genre": {"type": "string", "description": "Restrict to presets carrying this native Ableton genre tag, e.g. 'House', 'Hip Hop'."},
                "limit": {"type": "integer", "description": "Maximum results.", "default": 20}
            }
        }
    },
    # 4. Renderer Tools
    {
        "name": "render_plan",
        "description": "Build a per-track stem export manifest from a real .als, deciding from the project itself which tracks can be rendered and why the others cannot (MIDI needs a freeze, groups and returns are excluded).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "als_path": {"type": "string", "description": "Absolute path to the .als file."},
                "project_title": {"type": "string", "description": "Optional title; defaults to the .als filename."}
            },
            "required": ["als_path"]
        }
    },
    # 5. Telemetry & Gap Tracking
    {
        "name": "live_bridge_status",
        "description": "Inspect the status of SenseiV2Bridge queues, remote scripts, and recent requests.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "gap_record",
        "description": "Log an identified missing control, untested path, or desired Live API feature into docs/MISSING_CONTROLS_LOG.md.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["Transport", "MIDI", "Device", "Routing", "Arrangement", "Session", "Telemetry"]
                },
                "description": {"type": "string", "description": "Detailed description of what operation was attempted or needed."},
                "observed_behavior": {"type": "string", "description": "What occurred or why current tools were insufficient."},
                "required_implementation": {"type": "string", "description": "Recommended remote script, M4L, OSC, or bridge implementation."}
            },
            "required": ["category", "description", "observed_behavior", "required_implementation"]
        }
    },
    {
        "name": "plan_verify",
        "description": "Check the current session plan against Sensei's catalog -- every track with a Sensei role must name an instrument that resolves to exactly that role. Catches the Live-side instrument_role_unresolved failure without opening Live.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "project_inspect_arrangement",
        "description": "Read an .als arrangement -- tempo, time signature, locators, and the section boundaries inferred from where clips start and stop across tracks.",
        "inputSchema": {
            "type": "object",
            "properties": {"als_path": {"type": "string", "description": "Absolute path to the .als file."}},
            "required": ["als_path"]
        }
    },
    {
        "name": "drumbuss_build",
        "description": "Build the native EQ Eight -> Glue -> Utility drum buss chain in an .als. Dry run by default; applying writes a timestamped backup first and verifies the result after saving.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "als_path": {"type": "string", "description": "Absolute path to the .als file."},
                "source": {"type": "string", "description": "Source track name to build the buss from.", "default": "KICK BUSS"},
                "apply": {"type": "boolean", "description": "Write the .als. Leave false to preview only.", "default": False}
            },
            "required": ["als_path"]
        }
    },
    {
        "name": "projects_arrangement_shapes",
        "description": "Scan a library of .als projects and report how the user actually arranges -- section lengths, section counts, song lengths and tempos, inferred from clip boundaries. Evidence for arrangement templates instead of guesswork.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "roots": {"type": "array", "items": {"type": "string"}, "description": "Directories to scan. Defaults to ~/Desktop, ~/Documents and ~/Music/Ableton."},
                "limit": {"type": "integer", "description": "Maximum number of .als files to read."}
            }
        }
    },
{
        "name": "project_analyze_clips",
        "description": "Check arrangement clips for gain/fade/automation alignment problems -- clip gain outside the allowed range, missing fades, and clip vs track volume automation conflicts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "als_path": {"type": "string", "description": "Absolute path to the .als file."},
                "limit_db": {"type": "number", "description": "Maximum absolute clip gain in dB before it is flagged.", "default": 12.0},
                "threshold_db": {"type": "number", "description": "Clip gain below this is treated as unity.", "default": 0.25}
            },
            "required": ["als_path"]
        }
    },
    {
        "name": "automation_read",
        "description": "List every automation envelope in an .als, resolving each PointeeId to the owning device and parameter. Read-only -- nothing in this stack can write automation yet.",
        "inputSchema": {
            "type": "object",
            "properties": {"als_path": {"type": "string", "description": "Absolute path to the .als file."}},
            "required": ["als_path"]
        }
    },
    {
        "name": "drumbuss_read",
        "description": "Read the drum buss device parameters out of an .als and report whether they match the conservative preset. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"als_path": {"type": "string", "description": "Absolute path to the .als file."}},
            "required": ["als_path"]
        }
    },
{
        "name": "chain_evidence",
        "description": "What device chains the user actually builds, counted from their own projects. With no role, returns the whole measured summary; with a role, the evidence-backed chain and each device's presence rate. A role with too little data returns no recommendation instead of a guess.",
        "inputSchema": {
            "type": "object",
            "properties": {"role": {"type": "string", "description": "e.g. kick, snare, hat, bass, sub, keys, pad, lead, perc, sample, bus, fx."}}
        }
    },
    {
        "name": "live_project",
        "description": "Open, inspect or close an Ableton Live project. Live's own scripting cannot open or close a set, so this drives it from outside and then reads Live's own log to say whether the set really loaded -- whether it was corrupt, how many clips Live had to repair, and which audio files it could not open. Opening another set while Live is running can raise Live's unsaved-changes dialog, which only the person at the keyboard can answer; nothing is discarded automatically.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["status", "open", "quit"], "description": "What to do."},
                "als_path": {"type": "string", "description": "Project to open, for op=open."},
                "allow_switch": {"type": "boolean", "description": "Open a set even though Live is already running. A set switch also kills an installed Extension (Extension Host crashes inside the SDK), though control surfaces survive it.", "default": False},
                "wait_seconds": {"type": "number", "description": "How long to give Live before reading the verdict.", "default": 30}
            },
            "required": ["op"]
        }
    },
    {
        "name": "chain_plan",
        "description": "For every track in an .als, report its role, its current device chain, the evidence-backed chain for that role, and which track in the same project could donate it. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"als_path": {"type": "string", "description": "Absolute path to the .als file."}},
            "required": ["als_path"]
        }
    },
    {
        "name": "chain_apply",
        "description": "Copy a device chain from one track to another inside the same .als. Device XML is never synthesised -- it is cloned from a real device with fresh Pointee ids. Refuses to overwrite a track that already has a chain. Dry run by default; applying writes a timestamped backup first and re-reads the file to verify.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "als_path": {"type": "string", "description": "Absolute path to the .als file."},
                "target_track": {"type": "string", "description": "Track that should receive the chain. Must currently be empty."},
                "donor_track": {"type": "string", "description": "Track whose chain is copied."},
                "apply": {"type": "boolean", "description": "Write the .als. Leave false to preview only.", "default": False}
            },
            "required": ["als_path", "target_track", "donor_track"]
        }
    },
    {
        "name": "palette_read",
        "description": "The user's measured sound palette -- which sample sources and instrument devices actually recur, per role, ranked by how many separate projects each appears in. Bounces, freezes and reverb impulse responses are excluded.",
        "inputSchema": {
            "type": "object",
            "properties": {"role": {"type": "string", "description": "e.g. kick, snare, hat, bass, keys, pad, fx. Omit for the full summary."}}
        }
    },
    {
        "name": "project_sound_sources",
        "description": "List one project's sound sources -- instrument devices per track and the samples they load.",
        "inputSchema": {
            "type": "object",
            "properties": {"als_path": {"type": "string", "description": "Absolute path to the .als file."}},
            "required": ["als_path"]
        }
    },
{
        "name": "automation_write",
        "description": "Write an automation envelope onto a track's mixer parameter in an .als. The envelope targets the parameter's own AutomationTarget id -- nothing is invented -- and values are checked against that parameter's real range. Dry run by default; applying writes a timestamped backup, saves atomically, then reloads the file and compares every point.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "als_path": {"type": "string", "description": "Absolute path to the .als file."},
                "track": {"type": "string", "description": "Track name exactly as Live shows it."},
                "parameter": {"type": "string", "enum": ["volume", "pan"], "description": "Mixer parameter to automate. Use pointee_id instead for device parameters."},
                "pointee_id": {"type": "string", "description": "Automation target id of a device parameter, from automation_list_targets."},
                "points": {
                    "type": "array",
                    "description": "Breakpoints in time order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "time": {"type": "number", "description": "Position in beats from the start of the arrangement."},
                            "value": {"type": "number", "description": "Target value. Native units unless unit is 'db'."}
                        },
                        "required": ["time", "value"]
                    }
                },
                "unit": {"type": "string", "enum": ["native", "db"], "description": "'db' is accepted for volume only and is converted to Live's linear gain.", "default": "native"},
                "replace": {"type": "boolean", "description": "Overwrite an envelope that already exists on this parameter.", "default": False},
                "apply": {"type": "boolean", "description": "Write the .als. Leave false to validate only.", "default": False}
            },
            "required": ["als_path", "track", "points"]
        }
    },
{
        "name": "render_verify",
        "description": "Measure exported stems against the project's own render manifest -- which expected file is missing, which is silent, which has the wrong channel count. Rendering itself needs Live's audio engine and cannot be done from here; this checks the result afterwards.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "als_path": {"type": "string", "description": "Absolute path to the .als the stems were rendered from."},
                "renders_dir": {"type": "string", "description": "Directory holding the exported .wav/.aif stems."},
                "project_title": {"type": "string", "description": "Optional title; defaults to the .als filename."}
            },
            "required": ["als_path", "renders_dir"]
        }
    },
{
        "name": "live_state",
        "description": "Read Ableton Live's current state -- tempo, transport position, selected track, every track's mixer values and devices, and the song's locators. Published by the Loom control surface running inside Live. Always reports how old the snapshot is and whether it is fresh; if Live is not running it says so instead of returning stale data as if it were live.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {"type": "boolean", "description": "Ask Live for a fresh dump before reading.", "default": True},
                "include_devices": {"type": "boolean", "description": "Include each track's device list.", "default": True},
                "wait_seconds": {"type": "number", "description": "How long to wait for the refresh.", "default": 3},
                "max_age_seconds": {"type": "number", "description": "Above this age the snapshot is reported as not fresh.", "default": 10}
            }
        }
    },
    {
        "name": "live_command",
        "description": "Make a live change inside a running Ableton Live: set tempo, mixer volume/pan/mute/solo, a device parameter, transport, a locator, a new MIDI track (with an instrument family from the browser), the song key, an audio file imported into the project as a clip (extension bridge), or a pre-effects render of an audio track range (extension bridge). Runs through the Loom control surface, which writes the real before/after values back -- so the answer is what Live actually did, not what was requested. Every value is checked against the parameter's own range inside Live before it is applied.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["get_state", "set_tempo", "set_mixer", "set_device_parameter", "list_device_parameters", "transport", "create_locator", "create_midi_track", "set_key", "import_audio_clip", "render_pre_fx", "capture_prepare", "capture_route", "capture_arm", "capture_record", "capture_stop"], "description": "Operation to run inside Live."},
                "track": {"type": "string", "description": "Track name, exactly as Live shows it. Must match exactly one track."},
                "device": {"type": "string", "description": "Device name on that track."},
                "parameter": {"type": "string", "description": "Parameter name on that device."},
                "value": {"type": "number", "description": "New value for set_device_parameter."},
                "bpm": {"type": "number", "description": "Tempo for set_tempo."},
                "volume": {"type": "number", "description": "Mixer volume, in Live's own parameter range."},
                "pan": {"type": "number", "description": "Mixer pan, -1 to 1."},
                "mute": {"type": "boolean", "description": "Mute state."},
                "solo": {"type": "boolean", "description": "Solo state."},
                "action": {"type": "string", "enum": ["play", "stop", "continue"], "description": "Transport action."},
                "position": {"type": "number", "description": "Playhead position in beats."},
                "beat": {"type": "number", "description": "Locator position in beats."},
                "name": {"type": "string", "description": "Locator name, or the exact name of the MIDI track to create (create_midi_track adopts an existing MIDI track of that name rather than duplicating it)."},
                "instrument_family": {"type": "string", "description": "For create_midi_track: a browser search term (e.g. 'Drum Rack', 'Basic Analog Bass'); the first loadable match is loaded onto the new track and the outcome is reported, never assumed."},
                "path": {"type": "string", "description": "For import_audio_clip: the audio file to import (Live copies it into the project)."},
                "slot": {"type": "integer", "description": "For import_audio_clip: session clip slot index; omitted = first empty slot; omitted together with start_beat = arrangement."},
                "start_beat": {"type": "number", "description": "For import_audio_clip: arrangement position in beats (instead of a slot). For render_pre_fx: range start."},
                "end_beat": {"type": "number", "description": "For render_pre_fx: range end in beats."},
                "duration_beats": {"type": "number", "description": "For import_audio_clip in the arrangement: clip length in beats (default: the sample's natural length)."},
                "warped": {"type": "boolean", "description": "For import_audio_clip: warp the clip (default true)."},
                "root": {"type": "string", "description": "For set_key: root note, e.g. 'F' or 'A#'."},
                "mode": {"type": "string", "description": "For set_key: Live scale name, e.g. 'Minor'."},
                "include_devices": {"type": "boolean", "description": "For get_state."},
                "wait_seconds": {"type": "number", "description": "How long to wait for Live to process it. 0 queues without verifying.", "default": 15}
            },
            "required": ["op"]
        }
    },
    {
        "name": "automation_list_targets",
        "description": "List every parameter on a track whose automation can be written -- mixer and device alike -- with its automation target id and declared range. A parameter that does not declare a range is left out rather than written with guessed bounds. Filter before raising the limit: a single EQ Eight carries 85 parameters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "als_path": {"type": "string", "description": "Absolute path to the .als file."},
                "track": {"type": "string", "description": "Track name exactly as Live shows it."},
                "scope": {"type": "string", "enum": ["mixer", "device"], "description": "Restrict to mixer or device parameters."},
                "contains": {"type": "string", "description": "Case-insensitive substring of the parameter tag, e.g. 'gain', 'freq'."},
                "limit": {"type": "integer", "description": "Maximum parameters returned.", "default": 50}
            },
            "required": ["als_path", "track"]
        }
    },
    {
        "name": "mix_measure",
        "description": "Direct signal measurement of one audio file (a stem, a bounce, a master): duration, sample rate, channels, sample peak dBFS, RMS dBFS, crest factor, ITU-R BS.1770 integrated loudness through pyloudnorm, and per-channel peak/RMS/DC offset. Silence and too-short files come back with null levels and a status, never a made-up floor. No true-peak guesses, no custom loudness range. The SubverseLab Mix Check engine, running locally.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Audio file to measure (wav/aiff/flac/mp3...). A rendered stem from render_plan is the usual input."},
                "max_duration_seconds": {"type": "number", "description": "Refuse files longer than this instead of measuring them (default 360)."}
            },
            "required": ["path"]
        }
    },
    {
        "name": "mix_analyze",
        "description": "Full Mix Check of one audio file: the measurements of mix_measure plus one-third-octave spectrum, tonal map with key candidate, noise floor, section summaries, mono fold-down compatibility, and evidence-backed findings. Optionally compared against a reference file or one of the stored Genre Profiles (measured from released masters: electronic, hiphop, jazz, metal, pop, rock); use_closest_profile ranks the track by technical proximity, which is not a genre classification and is labelled as such. Findings only appear when a measurement is actually outside the comparison range; limitations are listed with every result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The mix or master to analyse."},
                "analysis_stage": {"type": "string", "enum": ["mix", "master"], "description": "What the file is. Master-only metrics (loudness, peak, crest) are compared only for a master.", "default": "mix"},
                "reference_path": {"type": "string", "description": "Optional reference file to compare against."},
                "reference_stage": {"type": "string", "enum": ["mix", "master"], "description": "Required when reference_path is given: what the reference is."},
                "genre": {"type": "string", "description": "Optional stored Genre Profile id to compare against (see mix_profiles)."},
                "use_closest_profile": {"type": "boolean", "description": "With no genre and no reference: compare against the technically nearest stored profile and say so.", "default": False},
                "max_duration_seconds": {"type": "number", "description": "Refuse files longer than this (default 360)."},
                "include_waveform": {"type": "boolean", "description": "Include the 1200-bin waveform envelope in the answer (large). Default false.", "default": False},
                "detail": {"type": "boolean", "description": "Return every per-band table (31 one-third-octave bands for spectrum, mono fold-down and comparison deltas) instead of the compact form. The full answer exceeds the response limit and is then written to a file whose path is returned.", "default": False}
            },
            "required": ["path"]
        }
    },
    {
        "name": "mix_profiles",
        "description": "List the stored Genre Profiles mix_analyze can compare against: id, name, how many released masters each was measured from, and the measurement contract version.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "crate_fetch",
        "description": "Bring a source into the crate: a YouTube URL (yt-dlp + ffmpeg) or a local audio/video file, optionally trimmed, decoded to a 44.1 kHz stereo WAV in the crate's work directory. Returns the WAV path and the source metadata (title, video id, duration, trim). The SubverseLab Sampler's fetch stage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "YouTube URL or a local file path."},
                "start": {"type": "string", "description": "Trim start, e.g. '1:12' or '72'."},
                "end": {"type": "string", "description": "Trim end."},
                "workdir": {"type": "string", "description": "Where to keep the decoded WAV (default: the crate work directory under Sessions)."}
            },
            "required": ["source"]
        }
    },
    {
        "name": "crate_read",
        "description": "Measure the audio itself, not its file name: level, noise floor, silence share, stereo width, tempo (from loop length + autocorrelation octave choice, 82% measured accuracy vs 31% for beat tracking) with its chop-range fold, onset rate, key with confidence, brightness and harmonic ratio. Says why when it cannot answer (one_shot, no_plausible_bar_count, ambiguous key, ableton_compressed). The SubverseLab sample-reader.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Audio file to read."}
            },
            "required": ["path"]
        }
    },
    {
        "name": "crate_spots",
        "description": "Find chop candidates inside a longer recording: the top N windows ranked by harmonic content, onset density and level, each with a bar count, a score and the reason, plus the beat grid the ranking used. With a YouTube video id, the watch URLs that loop each spot. The sample-reader's spots stage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Audio file to scan."},
                "top": {"type": "integer", "description": "How many candidates (default 6).", "default": 6},
                "video_id": {"type": "string", "description": "YouTube video id, to return loop URLs for each spot."}
            },
            "required": ["path"]
        }
    },
    {
        "name": "crate_chop",
        "description": "Slice a recording into a sample pack the way the Sampler CLI does: modes transient, bars, fixed, silence, leftover or all; WAV slices per mode with fades, optional normalisation, a manifest that records how the slices were really produced (a given --bpm writes that grid, not librosa's estimate). Returns the pack directory, per-mode slice counts and the analysis.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Decoded WAV (from crate_fetch) or any local audio file."},
                "modes": {"type": "array", "items": {"type": "string", "enum": ["transient", "bars", "fixed", "silence", "leftover", "all"]}, "description": "Chop modes (default ['transient'])."},
                "out_dir": {"type": "string", "description": "Pack root (default: Sessions/SamplePacks under Loom)."},
                "name": {"type": "string", "description": "Pack folder name (default: from the source title)."},
                "bpm": {"type": "number", "description": "Known tempo; overrides the estimate and writes that grid."},
                "grid_offset": {"type": "string", "description": "Where the bar grid starts, as a timestamp, with bpm."},
                "bars": {"type": "integer", "description": "Bars per slice in bars mode (default 2).", "default": 2},
                "beats_per_bar": {"type": "integer", "description": "Default 4.", "default": 4},
                "seconds": {"type": "number", "description": "Slice length in fixed mode (default 2.0).", "default": 2.0},
                "min_len": {"type": "number", "description": "Shortest slice in seconds (default 0.08).", "default": 0.08},
                "max_len": {"type": "number", "description": "Longest slice in seconds."},
                "tail": {"type": "number", "description": "Extra seconds after each transient slice.", "default": 0.0},
                "top_db": {"type": "number", "description": "Silence threshold below peak for silence/leftover modes (default 30).", "default": 30.0},
                "fade_ms": {"type": "number", "description": "Fade at slice edges in ms (default 5).", "default": 5.0},
                "normalize_dbfs": {"type": "number", "description": "Peak-normalise every slice to this dBFS (omit for none)."},
                "bit_depth": {"type": "integer", "enum": [16, 24, 32], "description": "Default 24.", "default": 24},
                "max_slices": {"type": "integer", "description": "Per-mode cap (default 200).", "default": 200},
                "keep_source": {"type": "boolean", "description": "Copy the decoded source into the pack as _source.wav (default true).", "default": True},
                "source_meta": {"type": "object", "description": "Metadata from crate_fetch, recorded in the manifest."}
            },
            "required": ["path"]
        }
    },
    {
        "name": "crate_agent",
        "description": "THE crate trigger: from one source (YouTube URL or file) to a measured sample pack in one call. Fetches, reads the audio (tempo, key, quality), finds the chop spots, picks the chop mode from the evidence -- bars on the reader's own grid when the tempo is measured and inside the chop range, transients otherwise -- slices, and writes a pack whose manifest carries the reading, the spots and the reason for every choice. Nothing is guessed: a tempo the reader could not measure is reported as such and the pack falls back to transients. Dry run by default reports the plan without writing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "YouTube URL or local file."},
                "start": {"type": "string", "description": "Trim start."},
                "end": {"type": "string", "description": "Trim end."},
                "out_dir": {"type": "string", "description": "Pack root (default: Sessions/SamplePacks under Loom)."},
                "name": {"type": "string", "description": "Pack folder name."},
                "modes": {"type": "array", "items": {"type": "string"}, "description": "Force these chop modes instead of choosing from the reading."},
                "bpm": {"type": "number", "description": "Known tempo, wins over the reading."},
                "top_spots": {"type": "integer", "description": "How many chop spots to rank (default 6).", "default": 6},
                "dry_run": {"type": "boolean", "description": "true: fetch, read and plan only; false: also slice and write the pack.", "default": True}
            },
            "required": ["source"]
        }
    },
    {
        "name": "crate_to_live",
        "description": "Put a crate slice (or any audio file) into the running Live set as an audio clip: Live imports the file into the project folder (its own managed copy) and creates the clip in a session slot or at an arrangement position on the named audio track. Needs the extension bridge; the control surface cannot import audio. The answer carries the imported path Live chose and the clip it made.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Audio file, e.g. a slice from a crate pack."},
                "track": {"type": "string", "description": "Audio track name, exactly as Live shows it."},
                "slot": {"type": "integer", "description": "Session slot index; omitted = first empty slot."},
                "start_beat": {"type": "number", "description": "Arrangement position in beats instead of a slot."},
                "duration_beats": {"type": "number", "description": "Arrangement clip length in beats."},
                "warped": {"type": "boolean", "description": "Warp the clip (default true).", "default": True},
                "name": {"type": "string", "description": "Clip name."},
                "wait_seconds": {"type": "number", "description": "How long to wait for Live.", "default": 20}
            },
            "required": ["path", "track"]
        }
    },
    {
        "name": "mix_from_live",
        "description": "Measure what a track in the running Live set actually sounds like before its effects: the extension bridge renders the audio track's arrangement range pre-fx into its temp directory, then Mix Check measures the file (mix_measure) or analyses it (mix_analyze). One call from Live to numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track": {"type": "string", "description": "Audio track name."},
                "start_beat": {"type": "number", "description": "Range start in beats."},
                "end_beat": {"type": "number", "description": "Range end in beats."},
                "analysis": {"type": "string", "enum": ["measure", "analyze"], "description": "measure = direct signal values; analyze = full Mix Check (compact).", "default": "measure"},
                "analysis_stage": {"type": "string", "enum": ["mix", "master"], "default": "mix"},
                "wait_seconds": {"type": "number", "description": "How long to wait for the render.", "default": 60}
            },
            "required": ["track", "start_beat", "end_beat"]
        }
    },
    {
        "name": "mix_capture",
        "description": "Measure the mix from Live's own playback, no render. Two capture methods: 'resample' (default) makes Live record itself -- an audio track named 'Loom Capture' is created, routed to Resampling, armed, and record mode goes on with the transport -- four separate Live ticks, then after N seconds the recorded clip's file is measured; no OS permission, works on release Live through the control surface. 'tap' uses a Core Audio process tap on the Live process (macOS 14.2+, needs the System Audio Recording permission for the app running Loom; captures silence until granted). Then Mix Check measures or analyses the capture. follow_transport=true waits for Live to start playing first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["resample", "tap"], "description": "resample = Live records itself (default); tap = Core Audio process tap.", "default": "resample"},
                "position": {"type": "number", "description": "resample: arrangement position in beats to start playing from (default: where the playhead is)."},
                "seconds": {"type": "number", "description": "How long to capture (default 8).", "default": 8},
                "analysis": {"type": "string", "enum": ["measure", "analyze"], "description": "measure = direct signal values; analyze = full Mix Check (compact).", "default": "analyze"},
                "analysis_stage": {"type": "string", "enum": ["mix", "master"], "default": "master"},
                "genre": {"type": "string", "description": "Optional stored Genre Profile id to compare against."},
                "use_closest_profile": {"type": "boolean", "default": True},
                "follow_transport": {"type": "boolean", "description": "Wait for Live to start playing, capture while it plays.", "default": False},
                "max_seconds": {"type": "number", "description": "Cap for follow_transport captures (default 60).", "default": 60},
                "keep": {"type": "boolean", "description": "Keep the WAV (default true; the path is returned).", "default": True}
            }
        }
    },
{
        "name": "setup_scan",
        "description": "First-run setup: build Loom's catalogues from the stock Ableton library on THIS machine, read out of Live's own file index. Loom ships code and fixtures but never measurements, so each user generates their own. Reports state by default and writes nothing until asked.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "check_only": {"type": "boolean", "description": "Report which catalogues exist and whether Ableton's index is readable, without writing anything.", "default": True}
            }
        }
    },
]


# --- 2) Argument validation ------------------------------------------------
# inputSchema was declared but never enforced anywhere: a missing required
# field turned into a KeyError inside the handler and reached the client raw.
# What is validated here is the subset of JSON Schema the tools actually use.

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
        Path.home() / "Desktop",
        Path.home() / "Documents",
        Path.home() / "Music",
        BRIDGE_ROOT,
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
OVERFLOW_DIR = LOOM_DIR / "mcp_server" / "responses"


def render_tool_text(payload: Any) -> tuple[str, str | None]:
    text = json.dumps(payload, indent=2, default=str)
    if len(text) <= MAX_RESPONSE_CHARS:
        return text, None

    OVERFLOW_DIR.mkdir(parents=True, exist_ok=True)
    overflow_path = OVERFLOW_DIR / f"response_{int(time.time())}_{uuid.uuid4().hex[:6]}.json"
    overflow_path.write_text(text, encoding="utf-8")
    head = text[:MAX_RESPONSE_CHARS]
    notice = (
        f"[truncated] Full response was {len(text)} characters, over the {MAX_RESPONSE_CHARS} limit. "
        f"Complete JSON written to {overflow_path}"
    )
    return head, notice


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
        "path": ARRANGEMENTGPS_DIR / "engine" / "output" / "ableton_session_plan.json",
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

    target_context: dict[str, Any] = {}
    if args.get("preset_path"):
        target_context["loaded_preset_path"] = args["preset_path"]
    if args.get("explicit_profile_id"):
        target_context["explicit_profile_id"] = args["explicit_profile_id"]
    elif args.get("role") in ("bass", "chord"):
        target_context["explicit_profile_id"] = _profile_for_role(args["role"], args.get("instrument_family"))
    elif args.get("role") == "drum":
        target_context["device_classes"] = ["DrumGroupDevice"]
        target_context["verified_pad_map"] = True
        target_context["verified_pad_notes"] = [36, 38, 42, 46]

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
    )

    if args.get("auto_write_to_live") and result.get("generation_safe") and result.get("payload"):
        payload = result["payload"]
        clip_name = f"Sensei {args.get('genre', 'Var')} {args.get('role', '')}".strip()
        write_res = handle_midi_write_to_live({
            "name": clip_name,
            "notes": payload.get("notes", []),
            "length_beats": float(payload.get("length_beats", args.get("bars", 4) * 4.0)),
            "prompt": f"Auto-write variation: {args.get('genre')} {args.get('role')}"
        })
        result["bridge_write_status"] = write_res

    return result


def handle_midi_write_to_live(args: dict[str, Any]) -> dict[str, Any]:
    ensure_bridge_dirs()
    req_id = f"req_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    filename = f"{req_id}.json"
    target_file = REQUEST_DIR / filename

    payload = {
        "id": req_id,
        "name": args.get("name", "Sensei MCP Clip"),
        "notes": args.get("notes", []),
        "length_beats": float(args.get("length_beats", 16.0)),
        "prompt": args.get("prompt", "Generated by Loom MCP"),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    target_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result: dict[str, Any] = {
        "request_id": req_id,
        "request_file": str(target_file),
        "note_count": len(payload["notes"]),
        "length_beats": payload["length_beats"],
    }

    # This tool used to drop the file, say "QUEUED" and return -- it never knew
    # whether Live picked it up. SenseiRemote moves the request into done/ or
    # errors/ under the same filename, so the outcome is genuinely readable.
    wait_seconds = float(args.get("wait_seconds", 15))
    if wait_seconds <= 0:
        result["status"] = "QUEUED"
        result["consumed"] = None
        result["message"] = "Queued without waiting. Live may or may not pick it up; nothing here verifies it."
        return result

    done_file = DONE_DIR / filename
    error_file = ERROR_DIR / filename
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        check_cancelled()
        if done_file.exists():
            result["status"] = "WRITTEN_TO_LIVE"
            result["consumed"] = True
            result["result_file"] = str(done_file)
            result["waited_seconds"] = round(wait_seconds - (deadline - time.monotonic()), 2)
            return result
        if error_file.exists():
            result["status"] = "REJECTED_BY_LIVE"
            result["consumed"] = True
            result["result_file"] = str(error_file)
            try:
                result["error_detail"] = json.loads(error_file.read_text(encoding="utf-8")).get("error")
            except Exception:  # noqa: BLE001
                result["error_detail"] = None
            return result
        report_progress(wait_seconds - (deadline - time.monotonic()), wait_seconds, "waiting for Live")
        time.sleep(0.25)

    result["status"] = "NOT_CONSUMED"
    result["consumed"] = False
    result["waited_seconds"] = wait_seconds
    result["message"] = (
        f"Live did not pick the request up within {wait_seconds}s. The file is still in "
        f"{REQUEST_DIR}. Usual cause: Live is not running, or the Loom control surface is not enabled "
        f"as a Control Surface."
    )
    return result


STATE_DIR = BRIDGE_ROOT / "state"
STATE_FILE = STATE_DIR / "live_state.json"
_bind_bridge_root(BRIDGE_ROOT)


def _submit_bridge_request(payload: dict[str, Any], wait_seconds: float) -> dict[str, Any]:
    """Kopruye istek birakir ve SenseiRemote'un sonucunu geri okur.

    v1'de istek birakilir ve "kuyruga alindi" denirdi. SenseiRemote v2 sonucu
    result into the request itself before moving it to done/ or errors/, so the
    caller can actually learn what happened.
    """
    selection = _select_bridge_root()
    response = _submit_bridge_request_to(BRIDGE_ROOT, payload, wait_seconds)
    response["bridge_selection"] = selection
    error = str(response.get("error") or "")
    if "unsupported_in_extension" in error and BRIDGE_ROOT != DEFAULT_SURFACE_ROOT:
        # The extension cannot do this one (transport, key write, preset
        # loading). If the control surface is also alive, hand it over and say
        # so; otherwise the refusal stands.
        age, _version = _state_freshness(DEFAULT_SURFACE_ROOT)
        if age is not None and age < STATE_FRESH_SECONDS:
            fallback = _submit_bridge_request_to(DEFAULT_SURFACE_ROOT, payload, wait_seconds)
            fallback["bridge_selection"] = selection
            fallback["fallback"] = {"from": str(BRIDGE_ROOT), "to": str(DEFAULT_SURFACE_ROOT), "reason": error}
            return fallback
        response["fallback"] = {"available": False, "reason": "control surface not fresh"}
    return response


def _submit_bridge_request_to(root: Path, payload: dict[str, Any], wait_seconds: float) -> dict[str, Any]:
    request_dir, done_dir, error_dir = root / "requests", root / "done", root / "errors"
    for d in (request_dir, done_dir, error_dir, root / "processed", root / "state"):
        d.mkdir(parents=True, exist_ok=True)
    request_id = f"req_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    filename = f"{request_id}.json"
    request_file = request_dir / filename
    body = dict(payload)
    body["id"] = request_id
    body["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    request_file.write_text(json.dumps(body, indent=2), encoding="utf-8")

    response: dict[str, Any] = {"request_id": request_id, "request_file": str(request_file)}
    if wait_seconds <= 0:
        response["status"] = "QUEUED"
        response["consumed"] = None
        return response

    done_file = done_dir / filename
    error_file = error_dir / filename
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        check_cancelled()
        for path, status in ((done_file, "OK"), (error_file, "FAILED_IN_LIVE")):
            if path.exists():
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    record = {}
                response["status"] = status
                response["consumed"] = True
                response["result"] = record.get("result")
                response["error"] = record.get("error")
                response["result_file"] = str(path)
                return response
        report_progress(wait_seconds - (deadline - time.monotonic()), wait_seconds, "waiting for Live")
        time.sleep(0.2)

    response["status"] = "NOT_CONSUMED"
    response["consumed"] = False
    response["message"] = (
        f"Live did not process the request within {wait_seconds}s. Usual cause: Live is not running, "
        "or the Loom control surface is not enabled as a Control Surface in Settings -> Link/MIDI."
    )
    return response


def handle_live_state(args: dict[str, Any]) -> dict[str, Any]:
    """Live'in o anki durumu. SenseiRemote periyodik olarak yaziyor."""
    max_age = float(args.get("max_age_seconds", 10))
    _select_bridge_root()
    if args.get("refresh", True):
        # Ask Live for a fresh dump; if Live is closed, whatever is on disk is
        # read instead and its staleness is stated outright.
        answer = _submit_bridge_request({"op": "get_state", "include_devices": bool(args.get("include_devices", True))},
                                        float(args.get("wait_seconds", 3)))
        fresh = answer.get("result") if isinstance(answer.get("result"), dict) else None
        if fresh and fresh.get("tracks") is not None:
            # The answer itself is the freshest state there is; no need to
            # race the bridge's own timer for the file.
            fresh = dict(fresh)
            fresh["available"] = True
            fresh["state_file"] = str(STATE_FILE)
            fresh["state_source"] = "get_state_answer"
            fresh["age_seconds"] = 0.0
            fresh["is_fresh"] = True
            return fresh

    if not STATE_FILE.exists():
        return {
            "available": False,
            "state_file": str(STATE_FILE),
            "reason": "no_state_published_yet",
            "message": "SenseiRemote v2 has never published state here. Live may not be running, or the "
                       "installed remote script is still v1.",
        }
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    captured_at = float(state.get("captured_at") or 0)
    age = time.time() - captured_at if captured_at else None
    state["available"] = True
    state["state_file"] = str(STATE_FILE)
    state["age_seconds"] = round(age, 2) if age is not None else None
    state["is_fresh"] = bool(age is not None and age <= max_age)
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

    The Extensions SDK never exposed a song time signature, which is why every
    bar-to-beat conversion assumed 4/4 (GAP-003). The control surface does see
    it -- Song.signature_numerator/denominator are in every state it publishes
    -- and the .als carries it too. So: an explicit value wins, then the running
    session's own signature, then the project file, and only then 4/4, which is
    reported as an assumption rather than passed off as a reading.
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
    # The extension bridge publishes no signature (the SDK has none). If the
    # control surface is alive too, its state carries the real one.
    try:
        surface_state = DEFAULT_SURFACE_ROOT / "state" / "live_state.json"
        if BRIDGE_ROOT != DEFAULT_SURFACE_ROOT and surface_state.exists():
            state = json.loads(surface_state.read_text(encoding="utf-8"))
            age = time.time() - float(state.get("captured_at") or 0)
            numerator = state.get("signature_numerator")
            denominator = state.get("signature_denominator") or 4
            if age < 120 and numerator:
                return float(numerator) * 4.0 / float(denominator), "live_session_via_surface"
    except Exception:  # noqa: BLE001
        pass
    if args.get("als_path"):
        try:
            return float(handle_project_inspect_arrangement({"als_path": args["als_path"]})["beats_per_bar"]), "als"
        except Exception:  # noqa: BLE001
            pass
    return 4.0, "assumed_4_4"


def handle_midi_write_arrangement(args: dict[str, Any]) -> dict[str, Any]:
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
    response = _submit_bridge_request(payload, float(args.get("wait_seconds", 15)))
    response["beats_per_bar"] = beats_per_bar
    response["beats_per_bar_source"] = bpb_source
    return response



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


def _create_midi_track_request(name: str, instrument_family: str | None, wait: float) -> dict[str, Any]:
    """create_midi_track through whichever bridge is active. The extension can
    make the track but not load a preset; when it says so and the control
    surface is alive, the surface adopts the track and loads the family, and
    the answer records which side did what."""
    outcome = _submit_bridge_request({"op": "create_midi_track", "name": name,
                                      "instrument_family": instrument_family}, wait)
    instrument = str((outcome.get("result") or {}).get("instrument") or "")
    if instrument.startswith("not_loadable_in_extension") and BRIDGE_ROOT != DEFAULT_SURFACE_ROOT:
        age, _version = _state_freshness(DEFAULT_SURFACE_ROOT)
        if age is not None and age < STATE_FRESH_SECONDS:
            handed = _submit_bridge_request_to(DEFAULT_SURFACE_ROOT, {
                "op": "create_midi_track", "name": name,
                "instrument_family": instrument_family, "load_instrument_on_adopt": True}, wait)
            outcome["instrument_via_surface"] = (handed.get("result") or {}).get("instrument") or handed.get("error")
            outcome["fallback"] = {"from": str(BRIDGE_ROOT), "to": str(DEFAULT_SURFACE_ROOT), "reason": instrument}
        else:
            outcome["instrument_via_surface"] = "control surface not fresh; preset not loaded"
    return outcome


def handle_live_command(args: dict[str, Any]) -> dict[str, Any]:
    operation = args["op"]
    wait = float(args.get("wait_seconds", 15))
    if operation == "create_midi_track":
        return _create_midi_track_request(str(args.get("name") or ""), args.get("instrument_family"), wait)
    payload: dict[str, Any] = {"op": operation}
    for key in ("track", "device", "parameter", "value", "bpm", "volume", "pan", "mute", "solo",
                "action", "position", "beat", "name", "include_devices", "instrument_family", "root", "mode",
                "path", "slot", "start_beat", "end_beat", "duration_beats", "warped"):
        if key in args:
            payload[key] = args[key]
    return _submit_bridge_request(payload, wait)


def handle_project_inspect(args: dict[str, Any]) -> dict[str, Any]:
    als_path = resolve_als_path(args["als_path"])

    with gzip.open(str(als_path), "rb") as f:
        root = ET.parse(f).getroot()

    creator = root.attrib.get("Creator", "Unknown")

    # Tempo
    tempo = "120.0"
    tempo_node = root.find(".//MasterTrack//Tempo/Manual") or root.find(".//Tempo/Manual")
    if tempo_node is not None:
        tempo = tempo_node.attrib.get("Value", tempo)

    # Scale / Key
    scale_root_raw = (root.find(".//ScaleInformation/Root") or ET.Element("")).attrib.get("Value", "")
    scale_name = (root.find(".//ScaleInformation/Name") or ET.Element("")).attrib.get("Value", "")
    root_name = ROOT_MAP.get(scale_root_raw, scale_root_raw)
    scale_title = scale_name.capitalize() if scale_name else "Major"
    camelot = CAMELOT_MAP.get((root_name, scale_title), "Unknown")

    # Track breakdown
    track_tags = ["AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack", "MainTrack"]
    track_counts = Counter()
    tracks_list = []

    for elem in root.iter():
        if elem.tag in track_tags:
            track_counts[elem.tag] += 1
            uname = elem.find("./Name/UserName")
            tname = uname.attrib.get("Value", "") if uname is not None else ""
            if not tname:
                eff_name = elem.find("./Name/EffectiveName")
                tname = eff_name.attrib.get("Value", "") if eff_name is not None else elem.tag

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
                "is_muted": is_muted,
                "device_count": len(devices),
                "devices": devices[:5]
            })

    return {
        "als_path": str(als_path),
        "creator": creator,
        "tempo": float(tempo) if tempo.replace(".", "", 1).isdigit() else tempo,
        "key_root": root_name or "Unknown",
        "scale": scale_title,
        "camelot": camelot,
        "track_counts": dict(track_counts),
        "total_tracks": sum(track_counts.values()),
        "tracks": tracks_list
    }


def handle_project_detect_genre(args: dict[str, Any]) -> dict[str, Any]:
    info = handle_project_inspect(args)
    tracks = info.get("tracks", [])

    kick = snare = hat = bass = fx = 0
    total = len(tracks)

    for t in tracks:
        lname = t["name"].lower()
        if "kick" in lname or "bd" in lname: kick += 1
        if "snare" in lname or "clap" in lname or "sd" in lname: snare += 1
        if "hat" in lname or "hh" in lname: hat += 1
        if "bass" in lname or "sub" in lname or "808" in lname: bass += 1
        if "fx" in lname or "glitch" in lname: fx += 1

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
    from aimixmaster.project_analyzer import iter_tracks

    sys.path.insert(0, str(SCRIPTS_DIR)) if str(SCRIPTS_DIR) not in sys.path else None
    from extract_device_chains import display_name

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
    from aimixmaster.project_analyzer import iter_tracks

    sys.path.insert(0, str(SCRIPTS_DIR)) if str(SCRIPTS_DIR) not in sys.path else None
    from extract_device_chains import display_name

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


def _run_node(script: str, *script_args: str) -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node_not_found: Node.js is required to run the ArrangementGPS chain.")
    result = subprocess.run(
        [node, script, *script_args],
        cwd=str(ARRANGEMENTGPS_DIR),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{Path(script).name} failed: {(result.stderr or result.stdout).strip()[:400]}")
    return result.stdout.strip()


# The previous version of this tool did not call ArrangementGPS at all -- it
# wrote whatever the caller handed it into a JSON file, so an LLM that passed
# no tracks produced a 349-byte "plan" with an empty track list while the real
# engine sat unused. This runs the actual chain end to end.
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


def handle_project_build(args: dict[str, Any]) -> dict[str, Any]:
    dry_run = bool(args.get("dry_run", True))
    wait = float(args.get("wait_seconds", 15))
    base_seed = int(args.get("seed", 7))

    if args.get("plan_path"):
        plan_path = Path(args["plan_path"]).expanduser()
        created = {"status": "REUSED", "plan_path": str(plan_path)}
    else:
        if not (args.get("prompt") or "").strip():
            raise ValueError("Give a prompt to build from, or a plan_path to rebuild from.")
        created = handle_plan_create({"prompt": args["prompt"]})
        plan_path = ARRANGEMENTGPS_DIR / "engine" / "output" / "ableton_session_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    project = plan.get("project") or {}
    sections = plan.get("locators") or []
    root, mode = _plan_key(str(project.get("key") or ""))
    genre = str(project.get("genre") or "").strip()
    genre_style = genre.lower() or None
    writers = [t for t in plan.get("tracks") or [] if t.get("sensei_role")]
    out_of_scope = [t.get("ableton_name") or t.get("display_name") for t in plan.get("tracks") or [] if not t.get("sensei_role")]

    beats_per_bar, bpb_source = _beats_per_bar(args)
    steps: list[dict[str, Any]] = []
    if project.get("bpm"):
        steps.append({"kind": "tempo", "op": "set_tempo", "bpm": float(project["bpm"])})
    if root and mode:
        steps.append({"kind": "key", "op": "set_key", "root": root, "mode": mode})
    for section in sections:
        steps.append({"kind": "locator", "section": section["name"],
                      "beat": (int(section["start_bar"]) - 1) * beats_per_bar})

    # Tracks first: an empty set has none of the plan's tracks, and every
    # write below addresses a track by name. A dry run compares against the
    # running session's track list when the bridge has a fresh one.
    live_names: set[str] | None = None
    if dry_run:
        try:
            state = handle_live_state({})
            if state.get("is_fresh"):
                live_names = {str(tr.get("name")) for tr in state.get("tracks") or []}
        except Exception:  # noqa: BLE001 -- no session is a valid dry-run state
            live_names = None
    tracks: list[dict[str, Any]] = []
    for track in plan.get("tracks") or []:
        name = track.get("ableton_name") or track.get("display_name") or track.get("name")
        entry = {"track": name, "instrument_family": track.get("instrument_family"),
                 "role": track.get("sensei_role")}
        if dry_run:
            if live_names is None:
                entry["status"] = "unknown_no_session"
            else:
                entry["status"] = "exists" if name in live_names else "would_create"
            tracks.append(entry)
            continue
        outcome = _create_midi_track_request(str(name), track.get("instrument_family"), wait)
        result = outcome.get("result") or {}
        entry["status"] = outcome.get("status")
        entry["created"] = result.get("created")
        entry["instrument"] = result.get("instrument")
        if outcome.get("instrument_via_surface"):
            entry["instrument_via_surface"] = outcome["instrument_via_surface"]
        if outcome.get("error"):
            entry["error"] = outcome.get("error")
        tracks.append(entry)

    results: list[dict[str, Any]] = []
    for track_index, track in enumerate(writers):
        name = track.get("ableton_name") or track.get("display_name") or track.get("name")
        role = track["sensei_role"]
        activity = track.get("section_activity") or {}
        for section_index, section in enumerate(sections):
            if not _track_plays_in(track, section):
                results.append({"track": name, "section": section["name"], "status": "muted_by_plan"})
                continue
            bars = int(section["end_bar"]) - int(section["start_bar"]) + 1
            energy = activity.get(section.get("id"), section.get("energy"))
            density = None if energy is None else max(0.0, min(1.0, float(energy) / 100.0))
            request = {"role": role, "genre": genre or "Trap", "bars": bars,
                       "instrument_family": track.get("instrument_family"),
                       "seed": base_seed + track_index * 100 + section_index,
                       "density": density, "genre_style": genre_style,
                       "target_root": root, "target_mode": mode}
            entry = {"track": name, "role": role, "section": section["name"],
                     "start_bar": int(section["start_bar"]), "bars": bars,
                     "density": density, "genre_style": genre_style,
                     "profile": _profile_for_role(role, track.get("instrument_family"))}
            if dry_run:
                results.append({**entry, "status": "would_write"})
                continue
            generated = handle_midi_generate(request)
            if not generated.get("generation_safe"):
                results.append({**entry, "status": "blocked", "reason": generated.get("error")})
                continue
            notes = [{"pitch": n["pitch"], "start": n.get("time", n.get("start", 0.0)),
                      "duration": n["duration"], "velocity": n.get("velocity", 100)}
                     for n in (generated.get("payload") or {}).get("notes") or []]
            written = handle_midi_write_arrangement({
                "track": name, "start_bar": int(section["start_bar"]), "length_beats": bars * beats_per_bar,
                "beats_per_bar": beats_per_bar,
                "name": section["name"], "notes": notes, "wait_seconds": wait})
            results.append({**entry, "status": written.get("status"),
                            "notes": len(notes), "verified": (written.get("result") or {}).get("verified_note_count"),
                            "diagnostics": {k: v for k, v in (generated.get("diagnostics") or {}).items()
                                            if k.startswith(("density", "layer_fit"))}})

    if not dry_run:
        # Tempo and key first, locators last: an empty arrangement is a few
        # beats long and Live refuses a cue past its end, so the section
        # markers can only be placed once the clips have extended it.
        ordered = [s for s in steps if s["kind"] != "locator"] + [s for s in steps if s["kind"] == "locator"]
        for step in ordered:
            if step["kind"] == "tempo":
                step["outcome"] = _submit_bridge_request({"op": "set_tempo", "bpm": step["bpm"]}, wait).get("status")
            elif step["kind"] == "key":
                step["outcome"] = _submit_bridge_request({"op": "set_key", "root": step["root"],
                                                          "mode": step["mode"]}, wait).get("status")
            else:
                step["outcome"] = _submit_bridge_request({"op": "create_locator", "beat": step["beat"],
                                                          "name": step["section"]}, wait).get("status")
        # The surface may answer a locator before Live refreshes its cue list;
        # the arrangement is the truth, so read it back once at the end.
        state = _submit_bridge_request({"op": "get_state"}, wait)
        cues = (state.get("result") or {}).get("cue_points") or []
        for step in steps:
            if step["kind"] == "locator":
                step["verified"] = any(abs(float(c.get("time", -1)) - step["beat"]) < 1e-6
                                       and c.get("name") == step["section"] for c in cues)

    counts = Counter(r["status"] for r in results)
    return {
        "dry_run": dry_run,
        "plan": created,
        "project": {"name": project.get("name"), "bpm": project.get("bpm"), "key": f"{root} {mode}",
                    "genre": genre, "sections": len(sections), "total_bars": project.get("total_bars")},
        "trigger": _active_bridge_label(),
        "beats_per_bar": beats_per_bar,
        "beats_per_bar_source": bpb_source,
        "tracks": tracks,
        "track_totals": dict(Counter(tr["status"] for tr in tracks)),
        "session_steps": steps,
        "writes": results,
        "totals": dict(counts),
        "tracks_out_of_scope": out_of_scope,
        "note": ("Nothing was sent to Live. Call again with dry_run=false to write." if dry_run
                 else "Every write reports the status Live returned; NOT_CONSUMED means Live did not answer."),
    }


def handle_plan_create(args: dict[str, Any]) -> dict[str, Any]:
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required: the whole chain is derived from it.")

    steps = []
    for index, (script, label) in enumerate(CHAIN_STEPS):
        check_cancelled()
        report_progress(index, len(CHAIN_STEPS), label)
        script_args = (prompt,) if label == "blueprint" else ()
        steps.append({"step": label, "output": _run_node(script, *script_args)})
    report_progress(len(CHAIN_STEPS), len(CHAIN_STEPS), "done")

    session_plan_path = ARRANGEMENTGPS_DIR / "engine" / "output" / "ableton_session_plan.json"
    session_plan = json.loads(session_plan_path.read_text(encoding="utf-8"))
    project = session_plan.get("project", {})

    safe_name = "".join(c if c.isalnum() else "_" for c in project.get("name", "ArrangementGPS_Project"))
    while "__" in safe_name:
        safe_name = safe_name.replace("__", "_")
    build_dir = ARRANGEMENTGPS_DIR / "Builds" / safe_name.strip("_")
    action_list_file = build_dir / "ableton_action_list.json"

    tracks = session_plan.get("tracks", [])
    generatable = [t for t in tracks if t.get("sensei_role")]
    return {
        "status": "CREATED",
        "prompt": prompt,
        "project": project,
        "build_dir": str(build_dir),
        "action_list_file": str(action_list_file) if action_list_file.exists() else None,
        "locators": len(session_plan.get("locators", [])),
        "tracks_total": len(tracks),
        "tracks_sensei_can_generate": len(generatable),
        "tracks_out_of_scope": len(tracks) - len(generatable),
        "steps": steps,
        "message": "Run ArrangementGPSBuilder in Live to build the tracks, then 'Sensei: Build Arrangement (ArrangementGPS Plan)'.",
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

    job_path = LOOM_DIR / "Renderer" / "Jobs" / f"{int(time.time())}_{_safe_filename(project_title)}_render_job.json"
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
    result = coverage.verify_plan()
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

    return {
        "als_path": str(als_path),
        "tempo": project["tempo"],
        "beats_per_bar": project["beats_per_bar"],
        "track_count": project["track_count"],
        "total_bars": round(song_end / project["beats_per_bar"]) if song_end else 0,
        "locators": locators,
        "section_count": len(sections),
        # Inferred from where clips start and stop across tracks, not read
        # from locators -- locators are usually absent in these projects.
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


def _active_bridge_label() -> str:
    """Which Live-side endpoint the active bridge root is talking to, from
    the surface_version its last state dump carries."""
    state_file = BRIDGE_ROOT / "state" / "live_state.json"
    version = None
    if state_file.exists():
        try:
            version = json.loads(state_file.read_text(encoding="utf-8")).get("surface_version")
        except Exception:  # noqa: BLE001
            version = None
    if version and str(version).startswith("loom-extension"):
        return f"Loom extension bridge ({version}) at {BRIDGE_ROOT}"
    if version:
        return f"Loom control surface ({version}) at {BRIDGE_ROOT}"
    return f"bridge at {BRIDGE_ROOT} (no state published yet)"


def _bridge_candidates() -> list[dict[str, Any]]:
    """Every bridge root on this machine that has ever published state: the
    Loom control surface's, and each installed extension's (they may only
    write inside their own storage directory, see GAP-008). The active root is
    LOOM_BRIDGE_ROOT or the surface's; this list is how a caller finds the
    extension's and switches."""
    roots = [Path.home() / "Documents" / "SenseiV2Bridge"]
    extensions_data = Path.home() / "Library" / "Application Support" / "Ableton" / "Extensions Data"
    if extensions_data.exists():
        roots.extend(sorted(p / "bridge" for p in extensions_data.iterdir() if (p / "bridge").is_dir()))
    if BRIDGE_ROOT not in roots:
        roots.insert(0, BRIDGE_ROOT)
    found = []
    for root in roots:
        state_file = root / "state" / "live_state.json"
        entry: dict[str, Any] = {"root": str(root), "active": root == BRIDGE_ROOT, "state": "never_published"}
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                captured = float(state.get("captured_at") or 0)
                entry.update({
                    "state": "fresh" if captured and time.time() - captured < 10 else "stale",
                    "age_seconds": round(time.time() - captured, 1) if captured else None,
                    "surface_version": state.get("surface_version"),
                    "capabilities": state.get("capabilities"),
                })
            except Exception as error:  # noqa: BLE001
                entry.update({"state": "unreadable", "error": str(error)})
        found.append(entry)
    return found



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
    answer = _submit_bridge_request(payload, float(args.get("wait_seconds", 20)))
    if "unsupported_in_extension" not in str(answer.get("error") or "") and answer.get("status") == "FAILED_IN_LIVE" \
            and "unknown op" in str(answer.get("error") or ""):
        answer["note"] = "The active bridge is the control surface, which cannot import audio; the extension bridge is needed."
    return answer


def handle_mix_from_live(args: dict[str, Any]) -> dict[str, Any]:
    rendered = _submit_bridge_request({"op": "render_pre_fx", "track": args["track"],
                                       "start_beat": float(args["start_beat"]), "end_beat": float(args["end_beat"])},
                                      float(args.get("wait_seconds", 60)))
    result = rendered.get("result") or {}
    if rendered.get("status") != "OK" or not result.get("path"):
        return {"render": rendered, "measurement": None,
                "note": "no render came back; the extension bridge is needed for render_pre_fx" if "unknown op" in str(rendered.get("error") or "") else None}
    path = Path(str(result["path"]))
    if not path.is_file():
        return {"render": rendered, "measurement": None, "error": f"Live reported a render at {path} but the MCP cannot read it"}
    if args.get("analysis") == "analyze":
        measurement = handle_mix_analyze({"path": str(path), "analysis_stage": args.get("analysis_stage") or "mix"})
    else:
        measurement = handle_mix_measure({"path": str(path)})
    return {"render": rendered, "measurement": measurement}


# --- Live playback capture (Core Audio process tap) --------------------------
LIVETAP_SRC = LOOM_DIR / "MixAnalyzer" / "livetap" / "main.swift"
LIVETAP_BIN = LOOM_DIR / "MixAnalyzer" / "livetap" / "livetap"
MIX_CAPTURE_DIR = LOOM_DIR / "Sessions" / "MixCaptures"


def _livetap_binary() -> Path:
    """Build the tap tool on first use (swiftc ships with Xcode CLT)."""
    if LIVETAP_BIN.exists() and LIVETAP_BIN.stat().st_mtime >= LIVETAP_SRC.stat().st_mtime:
        return LIVETAP_BIN
    build = subprocess.run(["swiftc", "-O", "-o", str(LIVETAP_BIN), str(LIVETAP_SRC)], capture_output=True, text=True, timeout=300)
    if build.returncode != 0:
        raise RuntimeError(f"livetap build failed: {build.stderr.strip()[-800:]}")
    return LIVETAP_BIN


def _live_pid() -> int:
    out = subprocess.run(["pgrep", "-f", "Ableton Live.*Contents/MacOS/Live"], capture_output=True, text=True)
    pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    if not pids:
        raise RuntimeError("no running Ableton Live process")
    if len(pids) > 1:
        raise RuntimeError(f"more than one Live is running ({pids}); close the other one")
    return pids[0]


def _is_playing_now(wait: float = 3.0) -> bool | None:
    """Transport state comes from the control surface only (the SDK has no
    transport), so ask that root directly when it is alive."""
    age, _version = _state_freshness(DEFAULT_SURFACE_ROOT)
    if age is None or age > STATE_FRESH_SECONDS:
        return None
    answer = _submit_bridge_request_to(DEFAULT_SURFACE_ROOT, {"op": "get_state", "include_devices": False}, wait)
    result = answer.get("result") or {}
    return bool(result.get("is_playing")) if answer.get("status") == "OK" else None


def _measure_capture(path: Path, args: dict[str, Any]) -> dict[str, Any]:
    if args.get("analysis") == "measure":
        return handle_mix_measure({"path": str(path)})
    return handle_mix_analyze({"path": str(path), "analysis_stage": args.get("analysis_stage") or "master",
                               "genre": args.get("genre"), "use_closest_profile": bool(args.get("use_closest_profile", True))})


def _mix_capture_resample(args: dict[str, Any]) -> dict[str, Any]:
    """Live records its own output: capture_start arms a Resampling track and
    starts recording, capture_stop returns the recorded clip's file."""
    seconds = float(args.get("seconds") or 8)
    wait = 15.0
    age, _version = _state_freshness(DEFAULT_SURFACE_ROOT)
    if age is None or age > STATE_FRESH_SECONDS:
        return {"status": "NO_CONTROL_SURFACE", "note": "resample capture needs the Loom control surface alive (record mode and input routing are not in the Extensions SDK)"}
    # One request per Live tick: create, route, arm, record. Live 12.4.15b1
    # segfaulted when all of it ran inside one control-surface tick.
    steps: list[dict[str, Any]] = []
    for op, extra, gap in (("capture_prepare", {}, 0.6), ("capture_route", {}, 0.6), ("capture_arm", {}, 0.6),
                           ("capture_record", ({"position": float(args["position"])} if args.get("position") is not None else {}), 0.0)):
        answer = _submit_bridge_request_to(DEFAULT_SURFACE_ROOT, {"op": op, **extra}, wait)
        steps.append({"op": op, "status": answer.get("status"), "result": answer.get("result"), "error": answer.get("error")})
        if answer.get("status") != "OK":
            return {"status": "CAPTURE_FAILED", "stage": op, "error": answer.get("error"), "steps": steps}
        time.sleep(gap)
    t0 = time.time()
    while time.time() - t0 < seconds:
        check_cancelled()
        time.sleep(0.2)
    stopped = _submit_bridge_request_to(DEFAULT_SURFACE_ROOT, {"op": "capture_stop"}, wait)
    result: dict[str, Any] = {"method": "resample", "seconds_requested": seconds, "steps": steps, "stop": stopped.get("result")}
    if stopped.get("status") != "OK":
        result.update({"status": "CAPTURE_FAILED", "stage": "capture_stop", "error": stopped.get("error")})
        return result
    file_path = (stopped.get("result") or {}).get("file_path")
    if not file_path:
        result.update({"status": "NO_FILE", "note": "Live recorded a clip but reported no file path"})
        return result
    path = Path(str(file_path))
    deadline = time.time() + 10
    while not path.is_file() and time.time() < deadline:
        time.sleep(0.3)
    if not path.is_file():
        result.update({"status": "FILE_NOT_FOUND", "path": str(path), "note": "Live named a recording the MCP cannot see"})
        return result
    result["path"] = str(path)
    result["status"] = "OK"
    result["measurement"] = _measure_capture(path, args)
    return result


def handle_mix_capture(args: dict[str, Any]) -> dict[str, Any]:
    if (args.get("method") or "resample") == "resample":
        if args.get("follow_transport"):
            deadline = time.monotonic() + float(args.get("max_seconds") or 60)
            playing = _is_playing_now()
            if playing is None:
                return {"status": "NO_TRANSPORT_STATE", "note": "follow_transport needs the control surface's state"}
            while not playing and time.monotonic() < deadline:
                check_cancelled()
                time.sleep(0.5)
                playing = _is_playing_now(1.0)
            if not playing:
                return {"status": "NOT_PLAYING", "note": "Live did not start playing in time"}
        return _mix_capture_resample(args)
    binary = _livetap_binary()
    pid = _live_pid()
    seconds = float(args.get("seconds") or 8)
    follow = bool(args.get("follow_transport", False))
    max_seconds = float(args.get("max_seconds") or 60)
    MIX_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = MIX_CAPTURE_DIR / f"live-{stamp}.wav"
    waited = 0.0
    if follow:
        # Wait for the transport; the surface reports is_playing, the
        # extension bridge cannot, so this needs the control surface alive.
        deadline = time.monotonic() + max_seconds
        playing = _is_playing_now()
        if playing is None:
            return {"status": "NO_TRANSPORT_STATE", "note": "follow_transport needs the control surface's state (is_playing); the extension bridge does not expose transport"}
        while not playing and time.monotonic() < deadline:
            check_cancelled()
            time.sleep(0.5)
            waited += 0.5
            playing = _is_playing_now(1.0)
        if not playing:
            return {"status": "NOT_PLAYING", "waited_seconds": waited, "note": f"Live did not start playing within {max_seconds:g}s"}
        seconds = max_seconds
    started = time.time()
    proc = subprocess.run([str(binary), "--pid", str(pid), "--seconds", f"{seconds:g}", "--out", str(path)],
                          capture_output=True, text=True, timeout=seconds + 30)
    report: dict[str, Any] = {}
    if proc.stdout.strip():
        try:
            report = json.loads(proc.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            report = {"raw": proc.stdout[-400:]}
    result: dict[str, Any] = {"pid": pid, "path": str(path), "capture": report, "seconds_requested": seconds,
                              "elapsed_seconds": round(time.time() - started, 2), "waited_for_transport_seconds": waited if follow else None}
    if proc.returncode != 0:
        result["status"] = "CAPTURE_FAILED"
        result["error"] = proc.stderr.strip()[-600:]
        if proc.returncode == 3:
            result["note"] = "No frames came back. Grant System Audio Recording to the app running Loom (System Settings > Privacy & Security > Screen & System Audio Recording) and try again."
        return result
    if report.get("peak", 0) == 0:
        result["status"] = "SILENT"
        result["note"] = report.get("permission_hint") or "the capture is silent"
        return result
    result["status"] = "OK"
    result["method"] = "tap"
    result["measurement"] = _measure_capture(path, args)
    if not args.get("keep", True):
        path.unlink(missing_ok=True)
        result["path"] = None
    return result

def handle_live_bridge_status(_args: dict[str, Any]) -> dict[str, Any]:
    selection = _select_bridge_root()
    ensure_bridge_dirs()
    pending = [p.name for p in REQUEST_DIR.glob("*.json")]
    done = sorted([p.name for p in DONE_DIR.glob("*.json")], reverse=True)[:5]
    errors = sorted([p.name for p in ERROR_DIR.glob("*.json")], reverse=True)[:5]
    processed = sorted([p.name for p in PROCESSED_DIR.glob("*.json")], reverse=True)[:5]

    remote_scripts_dir = Path.home() / "Music" / "Ableton" / "User Library" / "Remote Scripts"
    installed_scripts = [d.name for d in remote_scripts_dir.iterdir() if d.is_dir()] if remote_scripts_dir.exists() else []

    return {
        "bridge_root": str(BRIDGE_ROOT),
        "bridge_root_source": selection,
        "bridge_candidates": _bridge_candidates(),
        "pending_requests": pending,
        "recent_done": done,
        "recent_errors": errors,
        "recent_processed": processed,
        "installed_remote_scripts": installed_scripts
    }


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
