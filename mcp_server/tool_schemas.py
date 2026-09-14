# © 2026 Şenol Şahan / SubverseLab · Loom · https://subverselab.com/loom
"""The Loom MCP's public tool surface: names, descriptions and input schemas.

Nothing here runs. server.py registers every tool listed below in
dispatch_tool() and validates call arguments against these schemas; the
test suite checks that the two lists agree.
"""
from __future__ import annotations

from typing import Any

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
                "octave": {"type": "integer", "description": "Octave for the chord voicing.", "default": 3},
                "beats_per_bar": {"type": "number", "description": "Beats in a bar (Live beats = quarter notes): 4 for 4/4, 3 for 3/4 and 6/8.", "default": 4},
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
                "auto_write_to_live": {"type": "boolean", "description": "If true, writes the generated notes into a session clip through the Loom extension -- only when the part is writable_to_live (drum: pad_notes given; bass/chord: instrument_verified); otherwise the answer says BLOCKED.", "default": False},
                "pad_notes": {"type": "array", "items": {"type": "integer"}, "description": "For role drum: the pad notes read from the Drum Rack in Live (live_command op drum_pads). Without them the part is an offline suggestion on the General MIDI map and is not writable to Live."},
                "instrument_verified": {"type": "boolean", "description": "For bass/chord: the caller has verified an instrument device on the target track in Live.", "default": False},
                "beats_per_bar": {"type": "number", "description": "Beats in a bar (Live beats = quarter notes): 4 for 4/4, 3 for 3/4 and 6/8.", "default": 4},
                "track": {"type": "string", "description": "For auto_write_to_live: the MIDI track to write into."}
            }
        }
    },
    {
        "name": "midi_write_arrangement",
        "description": "Write MIDI notes into the ARRANGEMENT of a running Live session -- a named track, a bar position, a length -- through the Loom extension. Every note, length and target is validated in Live before anything changes. A clip in the range that Loom did not write is never touched (the write is refused); Loom's own clip there is replaced only with on_conflict=replace_owned, so rebuilding a section is safe and a user's clip is safe. Reports the note count Live holds and whether the notes match note-for-note; NOT_CONSUMED means the request was withdrawn unapplied, INDETERMINATE that Live picked it up and never answered.",
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
                "on_conflict": {"type": "string", "enum": ["refuse", "replace_owned"], "description": "When the range already holds a clip: refuse (default), or replace it if Loom wrote it. A clip Loom did not write is never touched either way.", "default": "refuse"},
                "idempotency_key": {"type": "string", "description": "Optional key for a retry: the extension answers a repeated key with the stored outcome instead of writing again."},
                "wait_seconds": {"type": "number", "description": "How long to wait for Live to consume the request.", "default": 15}
            },
            "required": ["notes"]
        }
    },
    {
        "name": "midi_write_to_live",
        "description": "Write MIDI notes into a session clip in Ableton Live through the Loom extension bridge (op write_clip on the common protocol) and wait for Live to answer. Reports WRITTEN_TO_LIVE, REJECTED_BY_LIVE, NOT_CONSUMED (withdrawn, nothing applied) or INDETERMINATE (Live picked it up, no answer). An occupied slot is never overwritten unless the clip is Loom's own and on_conflict=replace_owned.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the generated clip in Ableton Live."},
                "track": {"type": "string", "description": "MIDI track name, exactly as Live shows it. Omitted: the first MIDI track."},
                "slot": {"type": "integer", "description": "Session slot index. Omitted: the first empty slot."},
                "on_conflict": {"type": "string", "enum": ["refuse", "replace_owned"], "description": "What to do when the slot holds a clip: refuse (default), or replace it if Loom wrote it.", "default": "refuse"},
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
        "description": "THE single trigger: build a whole project into the running Live session from one prompt, through the Loom extension. Runs plan_create (its own run directory), validates the plan, checks the extension's published capabilities, then in order: tempo -> tracks (created or adopted, a native instrument inserted when the SDK can, Drum Rack pads read back as evidence) -> a part per section and writable track (drum, bass, chord; the section's energy as density, the plan's genre as genre_style, in the project's key and tempo) -> a locator per section -> a read-back of the state. Song key is reported UNSUPPORTED_BY_SDK, never faked. A track whose instrument or pads could not be verified gets blocked writes, not silent ones; when the bridge stops answering the remaining writes are aborted. The answer's status is completed / partial / blocked / failed / indeterminate. Dry run by default: it reports exactly what it would write and touches Live only with dry_run=false.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Musical brief, e.g. 'dark rolling tech house, 126 bpm, in F minor'. Omit when plan_path is given."},
                "plan_path": {"type": "string", "description": "Use an existing session plan instead of running plan_create -- for a rebuild, or to test the write list without the Node chain."},
                "preset_load": {"type": "string", "enum": ["os", "none"], "default": "os", "description": "How a plan track gets its REAL preset in the open set. 'os' (default): the track is created empty and the preset file (.adg/.adv the family names, found on this machine) is handed to Live through macOS like a Finder double-click; it lands on the newly created (selected) track and is verified from Live's state -- device name on that track, and for a kit the pads Live reports equal the file's -- or the track's writes are blocked. The SDK cannot load presets; no native stand-in and no Simpler rebuild happen on this path. 'none': the old behaviour (native device by family name, or explicit kit rebuild with allow_lossy_kit)."},
                "set_manifest": {"type": "string", "description": "The manifest project_file_build wrote next to the set that is now open in Live. A drum track whose kit is in the manifest and whose pads Live reports exactly is verified as that preset (nothing rebuilt, no consent needed); other tracks are adopted with the devices the set file gave them."},
                "dry_run": {"type": "boolean", "description": "true: report the write list, change nothing in Live. false: write.", "default": True},
                "wait_seconds": {"type": "number", "description": "Per request, how long to wait for Live to consume it.", "default": 15},
                "seed": {"type": "integer", "description": "Base seed; each track and section derives its own from it.", "default": 7},
                "beats_per_bar": {"type": "number", "description": "Beats in a bar. Omit to read the running session's time signature; 4/4 is assumed only as a last resort and reported as such."},
                "tracks": {"type": "array", "items": {"type": "string"}, "description": "Build only these plan tracks (by ableton_name). An explicit simplification: the answer lists what was dropped and why. Omit to build every plan track."},
                "device_map": {"type": "object", "description": "Per-track native device to insert instead of the plan's preset name, e.g. {'Main Bass': 'Operator', 'Keys': 'Electric'}. The SDK cannot load presets; without a mapping a preset-named track is created empty and its writes are blocked with needs_preset."},
                "allow_lossy_kit": {"type": "boolean", "default": False, "description": "IGNORED by project_build (reported as such): a plan kit is never reconstructed from samples here; a kit that cannot be loaded is PRESET_LOAD_REQUIRED. The explicit, separate op is live_command build_drum_kit."},
                "kit": {"type": "string", "description": "Explicit user override for the drum tracks' preset references. Omit to resolve each track's own planned kit. Selection provenance and original plan references are reported. Reconstruction requires allow_lossy_kit=true."}
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
        "description": "The single connection diagnosis: which Loom extension bridge the MCP talks to (and why), its freshness, session and published capabilities, the operations the SDK cannot do, and what is waiting in the queue. Says whether this MCP may send mutations to the running extension (protocol verdict: OK / UPGRADE_REQUIRED / PROTOCOL_MISMATCH / STALE_STATE / NO_STATE) and the state of its replay journal. There is no fallback endpoint.",
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
        "name": "project_file_build",
        "description": "Build the plan's tracks WITH THEIR REAL PRESETS as a Live set file. The Extensions SDK cannot load a preset into an open set (insertDevice: built-in device, default preset only), so the set is written on disk from Live's own template: one MIDI track per plan track, the preset ArrangementGPS chose (a Drum Rack .adg with its DrumCells, macros and choke groups; an Instrument Rack; an .adv) converted into set XML by Presetor and refused whenever a produced node does not match, tag for tag and attribute for attribute, what Live itself writes; the plan's tempo. No representative device is ever inserted: a track whose preset is not on this machine is not created and is listed under unresolved. Writes a manifest next to the set; open the set in Live, then project_build(plan_path=..., set_manifest=...) writes MIDI and locators into those tracks with the kit verified against the manifest. Measured 2026-09-07 on Live 12.4.15b1: the first such set loaded with 0 repairs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Musical brief for plan_create. Omit when plan_path is given."},
                "plan_path": {"type": "string", "description": "An existing session plan (the same one project_build will use afterwards)."},
                "tracks": {"type": "array", "items": {"type": "string"}, "description": "Plan tracks to build; default: every track Sensei can write (drum, bass, chord)."},
                "out_dir": {"type": "string", "description": "Where the project folder is created. Default: ~/Desktop/Loom Builds."},
                "name": {"type": "string", "description": "Set name; default: the plan's project name."},
            },
        },
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
                "allow_switch": {"type": "boolean", "description": "Open a set even though Live is already running. A set switch also kills an installed Extension (Extension Host crashes inside the SDK): the Loom bridge is gone until Live is restarted.", "default": False},
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
        "description": "Read Ableton Live's current state -- which set is open (name, path, saved or not; from Live's log and window title, the SDK has no such field), tempo, transport position, selected track, every track's mixer values and devices, and the song's locators. Published by the Loom extension running inside Live (the SDK exposes no transport or time signature; those are null). Always reports how old the snapshot is and whether it is fresh; if Live is not running it says so instead of returning stale data as if it were live.",
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
        "description": "Make a live change inside a running Ableton Live through the Loom extension: set tempo, mixer volume/pan/mute/solo, a device parameter, a locator, a new MIDI track (with a native instrument device when the SDK can insert it), an audio file imported into the project as a clip, a pre-effects render of an audio track range, or read a Drum Rack's pad notes (drum_pads); delete_track / delete_locator remove what an earlier build left behind (a track only when it holds no clip and its devices are named exactly). The extension writes the real before/after values back, so the answer is what Live actually did. Operations the Extensions SDK has no API for -- transport, set_key, preset loading, the capture_* recording steps -- are answered UNSUPPORTED_BY_SDK before anything is sent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["get_state", "set_tempo", "set_mixer", "set_device_parameter", "list_device_parameters", "transport", "create_locator", "delete_locator", "create_midi_track", "delete_track", "set_key", "import_audio_clip", "render_pre_fx", "drum_pads", "build_drum_kit", "journal_import", "capture_prepare", "capture_route", "capture_arm", "capture_record", "capture_stop", "capture_result"], "description": "Operation to run inside Live. build_drum_kit assembles a kit on the track's Drum Rack from sample files (one chain + Simpler per pad; presets still cannot be loaded). transport, set_key and capture_* are listed so that they can be refused with the capability they would need; the SDK has none of them."},
                "pads": {"type": "array", "items": {"type": "object"}, "description": "For build_drum_kit: [{note: 36, sample: '/abs/path/kick.wav', name: 'Kick'}, ...]. Existing pads are never replaced; the pad notes are read back from the device afterwards."},
                "allow_lossy_kit": {"type": "boolean", "default": False, "description": "For build_drum_kit with kit: explicitly accept sample reconstruction instead of preserving the preset. Missing samples are refused even with consent."},
                "kit": {"type": "string", "description": "For build_drum_kit instead of pads: a Drum Rack preset -- a .adg path or a kit name from Sensei's catalogue (e.g. 'Boom Bap Kit'). Its pads, notes and sample files are read from the preset's own XML and rebuilt as chains + Simplers; the answer says which pads resolved, which samples are missing, and what the SDK path drops (per-pad effects, macros, choke groups, Simpler parameters)."},
                "source": {"type": "string", "description": "For journal_import: the bridge root of an earlier Loom extension id to carry the replay journal from. Omit to import every earlier id found on this machine."},
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
                "name": {"type": "string", "description": "Locator name, or the exact name of the MIDI track to create (create_midi_track adopts an existing MIDI track of that name rather than duplicating it). For delete_track: the exact track name (must match one track). For delete_locator: optional, refused if the cue at that beat is named otherwise."},
                "index": {"type": "integer", "description": "For delete_track: the track's index as live_state reports it; refused (index_mismatch) if the named track is elsewhere."},
                "expected_devices": {"type": "array", "items": {"type": "string"}, "description": "For delete_track: exactly the device names the track holds, in order. A track with devices is refused (track_has_devices) unless they are named; a track holding any session or arrangement clip is refused (track_has_clips) regardless."},
                "instrument_family": {"type": "string", "description": "For create_midi_track: a native device name (e.g. 'Drum Rack', 'Operator'); the SDK inserts it with its default preset. A browser preset name is not loadable and the outcome says not_loadable_in_extension."},
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
        "description": "Full Mix Check of one audio file: the measurements of mix_measure plus one-third-octave spectrum, tonal map with key candidate, noise floor, section summaries, mono fold-down compatibility, and evidence-backed findings. Optionally compared against a reference file or one of the stored Genre Profiles (hiphop, trap, electronic, pop, rock; each built from 20 of the most widely known released masters of the genre, measured locally, audio not retained); use_closest_profile ranks the track by technical distance in dB, which is not a genre classification, and uses the nearest genre profile as a correction target only when it leads the runner-up by at least 1 dB (closest_profile_status); otherwise the track is compared with the pooled released-masters profile (mode: pooled). Findings only appear when a measurement is actually outside the comparison range; limitations are listed with every result.",
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
        "description": "Put a crate slice (or any audio file) into the running Live set as an audio clip: Live imports the file into the project folder (its own managed copy) and creates the clip in a session slot or at an arrangement position on the named audio track. The answer carries the imported path Live chose and the clip it made.",
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
        "description": "Measure the mix from Live's own playback, no render, with a Core Audio process tap on the Live process (method 'tap': macOS 14.2+, needs the System Audio Recording permission for the app running Loom; captures silence until granted -- experimental, the permission target is unverified). Then Mix Check measures or analyses the capture. method 'resample' (Live recording itself) and follow_transport need record mode and transport state, which the Extensions SDK does not expose: both answer UNSUPPORTED_BY_SDK.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["tap", "resample"], "description": "tap = Core Audio process tap (default); resample is unsupported by the SDK and answers so.", "default": "tap"},
                "position": {"type": "number", "description": "resample: arrangement position in beats to start playing from (default: where the playhead is)."},
                "seconds": {"type": "number", "description": "How long to capture (default 8).", "default": 8},
                "analysis": {"type": "string", "enum": ["measure", "analyze"], "description": "measure = direct signal values; analyze = full Mix Check (compact).", "default": "analyze"},
                "analysis_stage": {"type": "string", "enum": ["mix", "master"], "default": "master"},
                "genre": {"type": "string", "description": "Optional stored Genre Profile id to compare against."},
                "use_closest_profile": {"type": "boolean", "default": True},
                "follow_transport": {"type": "boolean", "description": "Unsupported by the SDK (no transport state); true answers UNSUPPORTED_BY_SDK.", "default": False},
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
