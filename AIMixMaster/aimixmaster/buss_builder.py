"""Deterministic native-device BUSS builder for Ableton Live sets."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import xml.etree.ElementTree as ET

from .project_analyzer import direct_devices, find_unique_track, track_snapshot


EXPECTED_DRUM_BUSS_DEVICE_TAGS = ("Eq8", "GlueCompressor", "StereoGain")


class BussBuildError(ValueError):
    """Raised when an ALS cannot satisfy safe BUSS Builder invariants."""


@dataclass(frozen=True)
class BussBuildResult:
    target_name: str
    source_name: str
    inserted_tags: tuple[str, ...]
    next_pointee_id: int
    changed: bool


# Promoted to public names so Presetor can reuse the ID allocation instead of
# copying it. Copying it would mean two implementations of the one thing that
# must never be wrong: no two devices in a Live set may share a Pointee id.
def next_pointee_node(root: ET.Element) -> ET.Element:
    node = root.find("./LiveSet/NextPointeeId")
    if node is None or "Value" not in node.attrib:
        raise BussBuildError("LiveSet/NextPointeeId is missing")
    return node


def _positive_ids(element: ET.Element) -> set[int]:
    values: set[int] = set()
    for node in element.iter():
        raw = node.attrib.get("Id")
        if raw is not None and raw.lstrip("-").isdigit() and int(raw) > 0:
            values.add(int(raw))
    return values


def clone_with_new_ids(devices: list[ET.Element], next_id: int) -> tuple[list[ET.Element], int]:
    cloned = [deepcopy(device) for device in devices]
    old_ids = sorted({identifier for device in cloned for identifier in _positive_ids(device)})
    allocation = {old: next_id + index for index, old in enumerate(old_ids)}
    for device in cloned:
        for node in device.iter():
            raw = node.attrib.get("Id")
            if raw is not None and raw.lstrip("-").isdigit() and int(raw) in allocation:
                node.attrib["Id"] = str(allocation[int(raw)])
    return cloned, next_id + len(old_ids)


# Old private names kept so nothing that already imports them breaks.
_next_pointee_node = next_pointee_node
_clone_with_new_ids = clone_with_new_ids


def build_drum_buss(
    root: ET.Element,
    *,
    target_name: str = "DRUM BUSS",
    source_name: str = "KICK BUSS",
) -> BussBuildResult:
    """Clone the exact native chain from a validated in-set BUSS reference."""
    target = find_unique_track(root, target_name)
    if target.track_type != "GroupTrack":
        raise BussBuildError(f"{target_name!r} is not a GroupTrack")
    existing_tags = tuple(device.tag for device in direct_devices(target.element))
    if existing_tags == EXPECTED_DRUM_BUSS_DEVICE_TAGS:
        return BussBuildResult(
            target_name,
            source_name,
            existing_tags,
            int(_next_pointee_node(root).attrib["Value"]),
            False,
        )
    if existing_tags:
        raise BussBuildError(
            f"{target_name!r} has an unexpected direct chain: {existing_tags!r}"
        )

    source = find_unique_track(root, source_name)

    source_devices = direct_devices(source.element)
    source_tags = tuple(device.tag for device in source_devices)
    if source_tags != EXPECTED_DRUM_BUSS_DEVICE_TAGS:
        raise BussBuildError(
            f"{source_name!r} does not match required native chain: {source_tags!r}"
        )

    snapshots = {track.name: track_snapshot(track.element) for track in (target, source)}
    next_node = _next_pointee_node(root)
    next_id = int(next_node.attrib["Value"])
    cloned, updated_next_id = _clone_with_new_ids(source_devices, next_id)
    devices_node = target.element.find("./DeviceChain/DeviceChain/Devices")
    if devices_node is None:
        raise BussBuildError(f"{target_name!r} has no writable direct device chain")
    devices_node.extend(cloned)
    next_node.attrib["Value"] = str(updated_next_id)

    inserted_tags = tuple(device.tag for device in direct_devices(target.element))
    if inserted_tags != EXPECTED_DRUM_BUSS_DEVICE_TAGS:
        raise BussBuildError(f"Inserted device order is invalid: {inserted_tags!r}")
    for track in (target, source):
        if track_snapshot(track.element) != snapshots[track.name]:
            raise BussBuildError(f"{track.name!r} routing, mixer, automation, or clips changed")
    return BussBuildResult(target_name, source_name, inserted_tags, updated_next_id, True)
