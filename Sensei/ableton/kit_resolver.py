"""The one reader of a drum kit: which pads exist, what each one is, and
which sample file it plays.

Also the one place the pad vocabulary lives. Before 2026-09-06 a pad note
could come from four readers -- the preset's XML, the live device through the
SDK, the plan's instrument name, and an unwritten General MIDI assumption
inside the generator -- and only three of them said so. The GM map is now a
*named source* like the others (`assumed_general_midi`), never a silent
default, and a part generated against it is not writable to Live.

The Extensions SDK cannot load a preset, but it can insert chains into a Drum
Rack and put a Simpler with a sample file on each. So a kit the user already
owns is read here -- pads, receiving notes, labels, sample files -- from the
preset's own XML (profile_exporter does the reading) and handed to the
extension's ``build_drum_kit`` as {note, sample} pairs.

What is carried: the pad notes, the pad names, the sample file of each pad
(the primary sample when a pad layers several). What is NOT carried, and is
reported as such: per-pad effects, macros, choke groups, Simpler parameters
(envelopes, filters, warp), return chains. That is the honest fidelity of the
SDK path; the preset itself stays exactly what it was.

Sample paths in a preset point at the machine the pack was built on
(``/Volumes/data/tmp/trunk/...`` for Ableton's own packs). The file is found
through the ``RelativePath`` Live also stores, walked up from the preset's own
folder until it resolves -- the same way Live's library layout works.
"""
from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .inspector.profile_exporter import build_kit_profile

AUDIO_SUFFIXES = {".wav", ".aif", ".aiff", ".mp3", ".ogg", ".flac", ".mp4", ".m4a"}
MAX_WALK_UP = 8

# Where a pad note can come from, best evidence first. `live_drum_rack` is the
# device itself; `preset_xml` is the .adg the user picked; `assumed_general_midi`
# is a guess and is marked unwritable wherever it is used.
PAD_SOURCES = ("live_drum_rack", "preset_xml", "assumed_general_midi")

# The General MIDI drum map, as a source with a name. Sensei's drum corpus is
# pitched to it, so a kit whose pads sit elsewhere needs the role mapping
# below -- without it the corpus writes notes onto pads that do not exist and
# the clip comes back empty but "OK" (measured 2026-09-06).
GM_PAD_ROLES: dict[int, str] = {
    35: "kick", 36: "kick", 37: "rim", 38: "snare", 40: "snare", 39: "clap",
    42: "closed_hat", 44: "closed_hat", 46: "open_hat", 54: "shaker",
    41: "tom", 43: "tom", 45: "tom", 47: "tom", 48: "tom", 50: "tom",
    49: "crash", 57: "crash", 51: "ride", 53: "ride", 59: "ride",
    56: "perc", 60: "perc", 61: "perc", 62: "perc", 63: "perc", 64: "perc",
}
# The four a General MIDI kit always answers on, and the order the corpus
# leans on when a kit is mapped by role.
GM_CORE_PADS = (36, 38, 42, 46)
GM_MAPPING_ORDER = (36, 38, 42, 46, 39, 37, 49, 51, 41, 43, 45, 47)

# Pad roles the generator can address, in the order a label is matched.
_LABEL_ROLES = (
    ("open_hat", ("open hat", "openhat", "ohat", "hihat open", "hat open", "open hh")),
    ("closed_hat", ("closed hat", "closedhat", "chat", "hihat closed", "hat closed", "hihat", "hi-hat", "hi hat", "hh")),
    ("kick", ("kick", "bd", "bass drum")),
    ("snare", ("snare", "sd", "snr")),
    ("clap", ("clap",)),
    ("rim", ("rim", "sidestick", "side stick")),
    ("crash", ("crash",)),
    ("ride", ("ride",)),
    ("tom", ("tom",)),
    ("shaker", ("shaker",)),
    ("perc", ("perc", "conga", "bongo", "block", "cowbell", "tamb")),
)


def role_from_label(label: str, profiled: str | None = None, sample: str | None = None) -> str:
    return resolve_pad_role(label, profiled, sample)["value"]


def resolve_pad_role(label: str, profiled: str | None = None, sample: str | None = None) -> dict[str, str]:
    """Profile, explicit label, then sample basename; classification is inference."""
    if profiled and profiled not in ("unknown_pad", "unknown", ""):
        return {"value": profiled, "source": "profile", "confidence": "inferred"}
    for source, value in (("pad_label", label), ("sample_filename", Path(sample or "").stem)):
        text = (value or "").lower().replace("_", " ")
        for role, words in _LABEL_ROLES:
            if any(re.search(r"\b" + re.escape(word) + r"\b", text) for word in words):
                return {"value": role, "source": source, "confidence": "inferred"}
    return {"value": "unknown_pad", "source": "missing", "confidence": "missing"}


def pad_mapping(pads: list[dict[str, Any]]) -> dict[str, Any]:
    """How General-MIDI drum notes reach these pads.

    `direct` when the kit answers on the GM pads already; otherwise a
    GM-note -> kit-pad table built from each pad's role, plus the roles this
    kit has no pad for. A role with no pad is dropped and named, never moved
    onto a different pad.
    """
    notes = {int(pad["note"]) for pad in pads}
    if set(GM_CORE_PADS[:3]) <= notes and all(
        p.get("role") == GM_PAD_ROLES.get(int(p["note"])) for p in pads
    ):
        return {"mode": "direct", "generate_pads": sorted(notes), "map": {},
                "targets": [{"gm_note": n, "role": GM_PAD_ROLES.get(n), "target_note": n} for n in sorted(notes)],
                "target_notes": sorted(notes), "unmapped_roles": []}
    by_role: dict[str, list[int]] = {}
    for pad in pads:
        by_role.setdefault(str(pad.get("role") or "unknown_pad"), []).append(int(pad["note"]))
    mapping: dict[int, int] = {}
    generate: list[int] = []
    for gm_note in GM_MAPPING_ORDER:
        targets = by_role.get(GM_PAD_ROLES.get(gm_note, ""))
        if targets:
            mapping[gm_note] = sorted(targets)[0]
            generate.append(gm_note)
    return {"mode": "by_role", "generate_pads": generate, "map": mapping,
            # the same table with its meaning spelled out: corpus (GM) note -> role -> the pad it lands on
            "targets": [{"gm_note": gm, "role": GM_PAD_ROLES.get(gm), "target_note": target} for gm, target in mapping.items()],
            "target_notes": sorted(set(mapping.values())),
            "unmapped_roles": sorted({GM_PAD_ROLES[n] for n in GM_CORE_PADS} - set(by_role)),
            "kit_roles": {role: sorted(notes) for role, notes in by_role.items()}}


def resolve_pad_notes(live_pads: list[int] | None = None, kit: dict[str, Any] | None = None,
                      *, kit_verified: bool = False) -> dict[str, Any]:
    """The pad notes to write against, and where they came from.

    Precedence: the live Drum Rack, then the preset the user named, then the
    General MIDI assumption. Only the first two are writable; the third exists
    so an offline suggestion can be generated without pretending it has a target.
    """
    if live_pads:
        pads = sorted({int(note) for note in live_pads if 0 <= int(note) <= 127})
        if pads:
            known = [p for p in (kit or {}).get("pads", []) if int(p["note"]) in pads] if kit_verified else []
            described = known or [{"note": n, "role": GM_PAD_ROLES.get(n, "unknown_pad")} for n in pads]
            return {"value": pads, "source": "live_drum_rack", "confidence": "read", "writable": True,
                    "note_semantics": "midi_note: SDK chain.receivingNote is the pad's MIDI note (measured 2026-09-07)",
                    "mapping": pad_mapping(described),
                    "role_source": "verified_kit_build" if known else "assumed_general_midi",
                    "preset_identity_verified": bool(known)}
    if kit and kit.get("pads"):
        pads = sorted(int(p["note"]) for p in kit["pads"])
        return {"value": pads, "source": "preset_xml", "confidence": "read", "writable": True,
                "note_semantics": "midi_note: decoded from the preset's ReceivingNote (128 - raw)",
                "mapping": pad_mapping(kit["pads"]),
                "alternatives": {"preset": kit.get("kit"), "missing_samples": len(kit.get("missing") or [])}}
    return {"value": list(GM_CORE_PADS), "source": "assumed_general_midi", "confidence": "assumed", "writable": False,
            "reason": "no Drum Rack in Live and no preset given; the General MIDI map is an offline assumption",
            "mapping": {"mode": "direct", "generate_pads": list(GM_CORE_PADS), "map": {}, "unmapped_roles": []}}


class KitResolveError(ValueError):
    pass


def _relative_paths(adg_path: Path) -> dict[str, str]:
    """absolute Path -> RelativePath for every FileRef in the preset."""
    with gzip.open(adg_path, "rb") as stream:
        root = ET.fromstring(stream.read())
    pairs: dict[str, str] = {}
    for ref in root.iter("FileRef"):
        path_el, rel_el = ref.find("Path"), ref.find("RelativePath")
        absolute = path_el.attrib.get("Value", "") if path_el is not None else ""
        relative = rel_el.attrib.get("Value", "") if rel_el is not None else ""
        if absolute and relative:
            pairs[absolute] = relative
    return pairs


# How a sample reference resolved. These are separate states on purpose: an
# absolute hit and a pack-relative hit are not the same evidence, and a
# reference that resolves to nothing is not silently dropped.
SAMPLE_STATES = ("absolute", "pack_relative", "missing")


def decode_receiving_note(raw: str | int | float) -> int:
    """The MIDI note a DrumBranch's ReceivingNote value means.

    Live stores it inverted: ``ReceivingNote Value="92"`` is pad C1 = 36.
    Measured 2026-09-07 on Live 12.4.15b1: BNYX Boot Kit.adg carries 77..92,
    the loaded rack reports pads 36..51 through the SDK (chain.receivingNote),
    and every sample lands where General MIDI expects it (92 -> 36 Kick,
    89 -> 39 Clap, 86 -> 42 Closed Hat, 82 -> 46 Open Hat). Reading the raw
    value as the note put kits on the wrong pads and made every Core Library
    kit look non-GM.
    """
    value = int(float(raw))
    note = 128 - value
    if not 0 <= note <= 127:
        raise ValueError(f"ReceivingNote {raw!r} decodes to {note}, outside 0..127")
    return note


def _resolve_sample(adg_path: Path, declared: str, relative: str | None) -> tuple[Path | None, str, str | None]:
    """(existing file, state, where it was found) -- state is one of SAMPLE_STATES."""
    if declared and Path(declared).is_file():
        return Path(declared), "absolute", str(Path(declared).parent)
    if relative:
        rel = Path(relative)
        ancestor = adg_path.parent
        for _ in range(MAX_WALK_UP):
            candidate = ancestor / rel
            if candidate.is_file():
                return candidate, "pack_relative", str(ancestor)
            if ancestor.parent == ancestor:
                break
            ancestor = ancestor.parent
    return None, "missing", None


def find_kit(reference: str, catalog: list[dict[str, Any]] | None = None) -> Path:
    """An absolute .adg path, or a kit name looked up in Sensei's identity
    catalogue (role drum); the first catalogue path that exists wins."""
    candidate = Path(str(reference)).expanduser()
    if candidate.suffix.lower() == ".adg":
        if not candidate.is_file():
            raise KitResolveError(f"no preset file at {candidate}")
        return candidate
    wanted = str(reference).strip().lower().removesuffix(".adg")
    for entry in catalog or []:
        if entry.get("role") != "drum":
            continue
        name = str(entry.get("normalized_name") or entry.get("name") or "").lower().removesuffix(".adg")
        if name == wanted and entry.get("path") and Path(str(entry["path"])).is_file():
            return Path(str(entry["path"]))
    raise KitResolveError(f"no Drum Rack preset named {reference!r} in the catalogue on this machine; give the .adg path")


# Keys of a pad's device_profile that describe the reading, not the sound.
_PROFILE_META = {"device_type", "parse_source", "warnings", "decay_raw", "release_raw", "time_unit", "source_path"}


def fidelity_summary(kit: dict[str, Any]) -> dict[str, Any]:
    """The kit's fidelity, shrunk for a tool answer.

    resolve_kit() keeps every device parameter it read per pad; a 16-pad kit
    carries ~11 KB of them, and an MCP answer that repeats the block per drum
    track passes the response limit. Nothing is hidden: every loss category
    stays named (which device is replaced on which pads, which parameter
    names were read and not applied, effects, macros, layers); the per-pad
    values live in the full resolution the caller writes to its overflow file.
    """
    fidelity = kit.get("fidelity") or {}
    dropped = fidelity.get("dropped") or {}
    kinds: dict[str, list[int]] = {}
    for item in dropped.get("device_replacements") or []:
        kinds.setdefault(f"{'+'.join(item.get('source') or ['unknown'])} -> {item.get('target')}", []).append(int(item.get("note", -1)))
    parameters = dropped.get("device_parameters") or {}
    names: set[str] = set()
    for profile in parameters.values():
        if isinstance(profile, dict):
            names.update(k for k, v in profile.items() if k not in _PROFILE_META and v not in (None, [], {}))
    effects = dropped.get("per_pad_effects") or {}
    return {
        "preset_preserved": bool(fidelity.get("preset_preserved", False)),
        "rebuild_mode": fidelity.get("mode"),
        "carried": list(fidelity.get("carried") or []),
        "dropped_categories": {
            "device_replacements": {"pads": sum(len(v) for v in kinds.values()), "kinds": {k: sorted(v) for k, v in kinds.items()}},
            "device_parameters": {"pads": len(parameters), "read_not_applied": sorted(names)},
            "per_pad_effects": {"pads": len(effects), "effects": sorted({e for es in effects.values() for e in es})},
            "macros": {"count": len(dropped.get("macros") or []), "names": list(dropped.get("macros") or [])},
            "layered_samples_beyond_primary": {"pads": len(dropped.get("layered_samples_beyond_primary") or [])},
            "always": list(dropped.get("always") or []),
        },
        "note": fidelity.get("note"),
    }


def resolve_kit(reference: str, catalog: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    path = find_kit(reference, catalog)
    profile = build_kit_profile(path)
    relatives = _relative_paths(path)
    pads: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    dropped_effects: dict[str, list[str]] = {}
    replacements: list[dict[str, Any]] = []
    parameters: dict[str, Any] = {}
    for note_str, pad in sorted(profile["pads"].items(), key=lambda kv: int(kv[0])):
        note = int(note_str)
        sample = pad.get("primary_sample") or {}
        declared = str(sample.get("path") or "")
        label = str(pad.get("label") or "").strip() or f"Pad {note}"
        chain = (profile.get("device_chain_summary") or {}).get(note_str, {})
        devices = list(chain.get("devices") or [])
        instruments = [d for d in devices if d in {"DrumCell", "MultiSampler", "OriginalSimpler", "InstrumentGroupDevice"}]
        if instruments != ["OriginalSimpler"]:
            replacements.append({"note": note, "source": instruments or devices or ["unknown"], "target": "Simpler"})
        if pad.get("device_profile"):
            parameters[note_str] = pad["device_profile"]
        effects = list(pad.get("effects") or [])
        if effects:
            dropped_effects[label] = effects
        if not declared:
            missing.append({"note": note, "name": label, "reason": "no sample in this pad's chain"})
            continue
        resolved, state, found_in = _resolve_sample(path, declared, relatives.get(declared))
        if resolved is None:
            missing.append({"note": note, "name": label, "declared": declared, "relative": relatives.get(declared),
                            "state": state, "reason": "sample file not found on this machine"})
            continue
        role = resolve_pad_role(label, pad.get("normalized_role"), str(resolved))
        raw = pad.get("raw_receiving_note")
        pads.append({"note": note, "name": label, "sample": str(resolved),
                     # the two values are different facts and are never mixed up again:
                     "raw_receiving_note": int(float(raw)) if raw not in (None, "") else 128 - note,
                     "decoded_receiving_note": note, "note_source": "preset_xml", "note_confidence": "read",
                     "role": role["value"], "role_source": role["source"], "role_confidence": role["confidence"],
                     "source_devices": devices,
                     "layered_samples": len(pad.get("sample_files") or []),
                     # which XML tier declared it, and how the file was found
                     "reference_state": state, "found_in": found_in, "declared": declared})
    return {
        "kit": profile.get("kit_name"),
        "path": str(path),
        "pads": pads,
        "missing": missing,
        "pad_count": profile.get("pad_count"),
        "pad_source": "preset_xml",
        "profile_write_safety": profile.get("kit_write_safety", "unknown"),
        "sample_states": {state: sum(1 for p in pads if p["reference_state"] == state) for state in SAMPLE_STATES[:2]}
                         | {"missing": len(missing)},
        "fidelity": {
            "preset_preserved": False,
            "mode": "sample_reconstruction",
            "carried": ["pad_notes", "one sample file per pad"],
            "dropped": {
                "device_replacements": replacements,
                "device_parameters": parameters,
                "per_pad_effects": dropped_effects,
                "macros": list(profile.get("active_macros") or []),
                "layered_samples_beyond_primary": [p["name"] for p in pads if p["layered_samples"] > 1],
                "always": ["pad names (reported metadata only)", "choke groups", "Simpler parameters (envelopes, filter, warp, gain)", "return chains", "mixer settings per pad"],
            },
            "note": "chain + default Simpler + primary sample only; XML device parameters are reported but not applied. Preset identity, sound, and save/reopen fidelity are not verified.",
        },
    }
