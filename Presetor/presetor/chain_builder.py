"""Copy a track's device chain from another track in the same project.

Why copying: synthesising valid Ableton device XML from nothing is not
reliable, but cloning a device Live itself wrote is. AIMixMaster's buss_builder
proved this for the DRUM BUSS; the only difference here is that which track
gives and which receives is not fixed.

Fail-closed: nothing is written unless the target's chain is empty, and both
tracks' routing, mixer, automation and clip fields must be identical before and
after the write.
"""
from __future__ import annotations

from dataclasses import dataclass
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_AIMIXMASTER = Path(__file__).resolve().parents[2] / "AIMixMaster"
if str(_AIMIXMASTER) not in sys.path:
    sys.path.insert(0, str(_AIMIXMASTER))

from aimixmaster.buss_builder import clone_with_new_ids, next_pointee_node  # noqa: E402
from aimixmaster.gain_staging import normalized_device_name  # noqa: E402
from aimixmaster.project_analyzer import (  # noqa: E402
    direct_devices,
    iter_tracks,
    track_snapshot,
)

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from extract_device_chains import display_name  # noqa: E402


class ChainBuildError(ValueError):
    pass


@dataclass(frozen=True)
class ChainBuildResult:
    target_name: str
    donor_name: str
    inserted_devices: tuple[str, ...]
    next_pointee_id: int
    changed: bool


def find_track(root: ET.Element, name: str) -> ET.Element:
    """Find exactly one track by name -- falling back to EffectiveName.

    project_analyzer.find_unique_track reads only UserName, which is empty in
    most projects. The plan here is built with display_name, so placement has
    to search by the same name or the transplant cannot find the track the plan
    found. The uniqueness rule is kept: exactly one match is required.
    """
    matches = [track for track in iter_tracks(root) if display_name(track) == name]
    if len(matches) != 1:
        raise ChainBuildError(f"Expected one track named {name!r}, found {len(matches)}")
    return matches[0]


def chain_of(track_element: ET.Element) -> tuple[str, ...]:
    return tuple(normalized_device_name(device) for device in direct_devices(track_element))


def find_donors(root: ET.Element, wanted_chain: tuple[str, ...]) -> list[str]:
    """Names of tracks in this project whose chain matches exactly."""
    return [
        display_name(track)
        for track in iter_tracks(root)
        if display_name(track) and chain_of(track) == tuple(wanted_chain)
    ]


def transplant_chain(
    root: ET.Element,
    *,
    target_name: str,
    donor_name: str,
) -> ChainBuildResult:
    if target_name == donor_name:
        raise ChainBuildError("target and donor are the same track")

    target = find_track(root, target_name)
    donor = find_track(root, donor_name)

    donor_devices = direct_devices(donor)
    if not donor_devices:
        raise ChainBuildError(f"{donor_name!r} has no devices to copy")
    donor_chain = chain_of(donor)

    existing = chain_of(target)
    if existing == donor_chain:
        return ChainBuildResult(target_name, donor_name, existing, int(next_pointee_node(root).attrib["Value"]), False)
    if existing:
        # Never silently replace work that is already there.
        raise ChainBuildError(f"{target_name!r} already has a chain: {' > '.join(existing)}")

    devices_node = target.find("./DeviceChain/DeviceChain/Devices")
    if devices_node is None:
        raise ChainBuildError(f"{target_name!r} has no writable direct device chain")

    snapshots = {name: track_snapshot(element) for name, element in ((target_name, target), (donor_name, donor))}
    pointee_node = next_pointee_node(root)
    next_id = int(pointee_node.attrib["Value"])
    cloned, updated_next_id = clone_with_new_ids(donor_devices, next_id)
    devices_node.extend(cloned)
    pointee_node.attrib["Value"] = str(updated_next_id)

    inserted = chain_of(target)
    if inserted != donor_chain:
        raise ChainBuildError(f"Inserted chain does not match donor: {inserted!r} != {donor_chain!r}")
    for name, element in ((target_name, target), (donor_name, donor)):
        if track_snapshot(element) != snapshots[name]:
            raise ChainBuildError(f"{name!r} routing, mixer, automation, or clips changed")

    return ChainBuildResult(target_name, donor_name, inserted, updated_next_id, True)
