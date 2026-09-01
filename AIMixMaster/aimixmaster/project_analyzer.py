"""Read-only track, routing, and direct-device-chain inspection."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass


TRACK_TAGS = {"AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack", "MainTrack"}


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


def track_name(track: ET.Element) -> str:
    return value(track.find("./Name/UserName")).strip()


def direct_devices(track: ET.Element) -> list[ET.Element]:
    """Return only this track's devices, never devices belonging to children."""
    return list(track.findall("./DeviceChain/DeviceChain/Devices/*"))


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
