"""The one reader of an Ableton Live Set: tracks, names, device chains, and
the project's musical context.

This module is the official entry for facts that used to be read in several
places at once, each with its own precedence (measured 2026-09-06: five
readers of a track name, two of them disagreeing on whether UserName or
EffectiveName wins). Anything that needs one of those facts imports it from
here; nothing re-implements it.

Every resolver returns a `Resolved`: the canonical value, which XML field it
came from, and the alternatives that were present but not chosen. A caller
that reports provenance (`data_source`, `target_evidence`) has the source
without having to guess it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any


TRACK_TAGS = {"AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack", "MainTrack"}


@dataclass(frozen=True)
class Resolved:
    """One fact, the field it came from, and what else was there.

    `source` is None when nothing carried the fact; `value` is then the
    caller's declared fallback and `confidence` says so.
    """

    value: Any
    source: str | None
    alternatives: dict[str, Any] = field(default_factory=dict)
    confidence: str = "read"  # read | derived | assumed | missing

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "source": self.source,
                "alternatives": dict(self.alternatives), "confidence": self.confidence}


@dataclass(frozen=True)
class TrackInfo:
    element: ET.Element
    track_id: int
    name: str
    track_type: str
    group_id: int | None
    device_tags: tuple[str, ...]


def value(element: ET.Element | None, default: str = "") -> str:
    return element.attrib.get("Value", default) if element is not None else default


# --- track name -------------------------------------------------------------
# Precedence: what the producer typed, then what Live shows. UserName is empty
# in most projects, which is why a UserName-only reader once skipped unnamed
# tracks entirely; EffectiveName can be inherited, which is why it does not win.
# Both are reported so a caller can see a disagreement instead of a silent pick.
NAME_FIELDS = (("user_name", "./Name/UserName"), ("effective_name", "./Name/EffectiveName"))


def resolve_track_name(track: ET.Element) -> Resolved:
    found = {}
    for label, path in NAME_FIELDS:
        text = value(track.find(path)).strip()
        if text:
            found[label] = text
    for label, _path in NAME_FIELDS:
        if label in found:
            return Resolved(found[label], label, {k: v for k, v in found.items() if k != label})
    return Resolved("", None, {}, "missing")


def track_name(track: ET.Element) -> str:
    """The canonical track name. Empty when the track carries no name at all."""
    return resolve_track_name(track).value


def direct_devices(track: ET.Element) -> list[ET.Element]:
    """Return only this track's devices, never devices belonging to children."""
    return list(track.findall("./DeviceChain/DeviceChain/Devices/*"))


# --- device chain -----------------------------------------------------------
# A rack looks like one device to direct_devices; the real chain is inside it.
# The two views are different facts, so the reader names which one it returned
# instead of leaving the caller to infer it from the call site.
RACK_TAGS = {"AudioEffectGroupDevice", "InstrumentGroupDevice", "MidiEffectGroupDevice"}
MAX_RACK_DEPTH = 3
DEVICE_NAME_ALIASES = {"Eq8": "EQ Eight", "Compressor2": "Compressor",
                       "GlueCompressor": "Glue Compressor", "StereoGain": "Utility"}


def device_name(device: ET.Element) -> str:
    """Live's display name for a device element, from its tag."""
    return DEVICE_NAME_ALIASES.get(device.tag, device.tag)


def expand_devices(devices: list[ET.Element], depth: int = 0) -> list[ET.Element]:
    """Replace racks with their contents; nesting depth is capped."""
    expanded: list[ET.Element] = []
    for device in devices:
        if device.tag in RACK_TAGS and depth < MAX_RACK_DEPTH:
            inner: list[ET.Element] = []
            for branch in device.findall("./Branches/*"):
                inner.extend(branch.findall("./DeviceChain/AudioToAudioDeviceChain/Devices/*"))
                inner.extend(branch.findall("./DeviceChain/MidiToAudioDeviceChain/Devices/*"))
            if inner:
                expanded.extend(expand_devices(inner, depth + 1))
                continue
        expanded.append(device)
    return expanded


def resolve_device_chain(track: ET.Element, expand: bool = False) -> Resolved:
    """The track's chain as display names. `expand` walks into racks; the
    answer says which view it is, because the two are not interchangeable."""
    top_level = direct_devices(track)
    devices = expand_devices(top_level) if expand else top_level
    return Resolved(
        tuple(device_name(device) for device in devices),
        "rack_expanded" if expand else "top_level",
        {"top_level": tuple(device_name(d) for d in top_level),
         "uses_rack": any(d.tag in RACK_TAGS for d in top_level),
         "tags": tuple(d.tag for d in devices)},
    )


def device_chain(track: ET.Element, expand: bool = False) -> tuple[str, ...]:
    return resolve_device_chain(track, expand).value


# --- musical context --------------------------------------------------------
# Tempo, key and time signature were read in three places with three different
# element paths. One reader, each field carrying where it came from.
PITCH_CLASSES = {"0": "C", "1": "C#", "2": "D", "3": "D#", "4": "E", "5": "F",
                 "6": "F#", "7": "G", "8": "G#", "9": "A", "10": "A#", "11": "B"}


def resolve_tempo(root: ET.Element) -> Resolved:
    for label, path in (("master_track", ".//MasterTrack//Tempo/Manual"), ("any_tempo", ".//Tempo/Manual")):
        node = root.find(path)
        if node is not None:
            try:
                return Resolved(float(value(node)), label)
            except ValueError:
                continue
    return Resolved(None, None, {}, "missing")


def resolve_key(root: ET.Element) -> Resolved:
    """The set's own ScaleInformation. Absent in most projects: reported
    missing rather than defaulted to C major."""
    raw_root = value(root.find(".//ScaleInformation/Root"))
    raw_name = value(root.find(".//ScaleInformation/Name"))
    if not raw_root and not raw_name:
        return Resolved(None, None, {}, "missing")
    return Resolved({"root": PITCH_CLASSES.get(raw_root, raw_root or None),
                     "scale": raw_name.capitalize() if raw_name else None},
                    "scale_information", {"root_raw": raw_root, "scale_raw": raw_name})


def resolve_time_signature(root: ET.Element) -> Resolved:
    """Beats per bar from the set's own time signature. The Extensions SDK
    exposes no song signature (GAP-003), so on the live path this is the only
    reading there is; absent means absent, never an assumed 4."""
    for node in root.iter("TimeSignature"):
        numerator = value(node.find(".//Numerator"))
        denominator = value(node.find(".//Denominator")) or "4"
        if numerator:
            try:
                return Resolved(float(numerator) * 4.0 / float(denominator), "time_signature",
                                {"numerator": int(numerator), "denominator": int(denominator)})
            except (ValueError, ZeroDivisionError):
                break
    return Resolved(None, None, {}, "missing")


def musical_context(root: ET.Element) -> dict[str, Resolved]:
    return {"tempo": resolve_tempo(root), "key": resolve_key(root),
            "beats_per_bar": resolve_time_signature(root)}


def iter_tracks(root: ET.Element):
    for element in root.iter():
        if element.tag in TRACK_TAGS:
            yield element


def analyze_tracks(root: ET.Element) -> list[TrackInfo]:
    result = []
    for track in iter_tracks(root):
        group_value = value(track.find("./TrackGroupId"), "")
        result.append(
            TrackInfo(
                element=track,
                track_id=int(track.attrib.get("Id", "-1")),
                name=track_name(track),
                track_type=track.tag,
                group_id=int(group_value) if group_value else None,
                device_tags=tuple(device.tag for device in direct_devices(track)),
            )
        )
    return result


def find_unique_track(root: ET.Element, name: str) -> TrackInfo:
    matches = [track for track in analyze_tracks(root) if track.name == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one track named {name!r}, found {len(matches)}")
    return matches[0]


def track_snapshot(track: ET.Element) -> dict[str, bytes]:
    """Capture fields a BUSS Builder must not modify."""
    paths = {
        "routing": "./DeviceChain/AudioOutputRouting",
        "mixer": "./DeviceChain/Mixer",
        "automation": "./AutomationEnvelopes",
        "clips": "./DeviceChain/ArrangementClips",
        "slots": "./DeviceChain/MainSequencer/ClipSlotList",
    }
    return {
        name: (
            ET.tostring(node, encoding="utf-8")
            if (node := track.find(path)) is not None
            else b"<missing />"
        )
        for name, path in paths.items()
    }


def preservation_snapshot(root: ET.Element) -> dict[tuple[str, str, int], dict[str, bytes]]:
    """Snapshot every mutable mix field outside a device-chain edit."""
    snapshot: dict[tuple[str, str, int], dict[str, bytes]] = {}
    for index, track in enumerate(iter_tracks(root)):
        key = (track.tag, track_name(track), index)
        snapshot[key] = track_snapshot(track)
    return snapshot
