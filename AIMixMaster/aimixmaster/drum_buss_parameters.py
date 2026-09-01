"""Verified parameter-only DRUM BUSS v1.1 configuration."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import xml.etree.ElementTree as ET

from .buss_builder import EXPECTED_DRUM_BUSS_DEVICE_TAGS
from .project_analyzer import direct_devices, find_unique_track, preservation_snapshot


class DrumBussParameterError(ValueError):
    """Raised when the verified Live 12 parameter schema is not present."""


@dataclass(frozen=True)
class ParameterChange:
    path: str
    old: str
    new: str


GLUE_TARGETS = {
    "Threshold": "-8.0",
    "Range": "2",
    "Makeup": "0.0",
    "Attack": "5",  # Live Glue Compressor: 10 ms.
    "Ratio": "0",  # Live Glue Compressor: 2:1.
    "Release": "6",  # Live Glue Compressor: Auto release.
    "DryWet": "1.0",
    "PeakClipIn": "false",
    "SideChain/OnOff": "false",
    "SideChainEq/On": "false",
}
UTILITY_TARGETS = {
    "StereoWidth": "1",
    "Mono": "false",
    "BassMono": "true",
    "BassMonoFrequency": "120.0",
    "Balance": "0",
    "Gain": "1.0",
    "Mute": "false",
}


def _id_multiset(root: ET.Element) -> Counter[str]:
    return Counter(
        node.attrib["Id"] for node in root.iter() if "Id" in node.attrib
    )


def _manual(device: ET.Element, path: str) -> ET.Element:
    node = device.find(f"./{path}/Manual")
    if node is None or "Value" not in node.attrib:
        raise DrumBussParameterError(f"Missing writable parameter: {device.tag}/{path}")
    return node


def _validate_value(device: ET.Element, path: str, target: str) -> None:
    parameter = device.find(f"./{path}")
    manual = _manual(device, path)
    if target in {"true", "false"}:
        if manual.attrib["Value"] not in {"true", "false"}:
            raise DrumBussParameterError(f"Expected boolean parameter: {device.tag}/{path}")
        return
    value_range = parameter.find("./MidiControllerRange") if parameter is not None else None
    if value_range is None:
        raise DrumBussParameterError(f"Missing numeric range: {device.tag}/{path}")
    minimum = float(value_range.find("./Min").attrib["Value"])
    maximum = float(value_range.find("./Max").attrib["Value"])
    value = float(target)
    if not minimum <= value <= maximum:
        raise DrumBussParameterError(f"Out-of-range value for {device.tag}/{path}: {target}")


def _set(device: ET.Element, path: str, new: str, changes: list[ParameterChange]) -> None:
    manual = _manual(device, path)
    old = manual.attrib["Value"]
    if old != new:
        manual.attrib["Value"] = new
        changes.append(ParameterChange(f"{device.tag}/{path}", old, new))


def _target_devices(root: ET.Element) -> dict[str, ET.Element]:
    track = find_unique_track(root, "DRUM BUSS")
    devices = direct_devices(track.element)
    if tuple(device.tag for device in devices) != EXPECTED_DRUM_BUSS_DEVICE_TAGS:
        raise DrumBussParameterError("DRUM BUSS direct chain is not Eq8 -> GlueCompressor -> StereoGain")
    return {device.tag: device for device in devices}


def apply_conservative_drum_buss_parameters(root: ET.Element) -> list[ParameterChange]:
    """Apply only the verified v1.1 controls; never add nodes or IDs."""
    before_ids = _id_multiset(root)
    before_next = root.find("./LiveSet/NextPointeeId")
    if before_next is None or "Value" not in before_next.attrib:
        raise DrumBussParameterError("NextPointeeId is missing")
    preserved = preservation_snapshot(root)
    devices = _target_devices(root)
    changes: list[ParameterChange] = []

    eq = devices["Eq8"]
    for index in range(8):
        for parameter_set in ("ParameterA", "ParameterB"):
            _validate_value(eq, f"Bands.{index}/{parameter_set}/IsOn", "false")
            _set(eq, f"Bands.{index}/{parameter_set}/IsOn", "false", changes)

    glue = devices["GlueCompressor"]
    for path, target in GLUE_TARGETS.items():
        _validate_value(glue, path, target)
        _set(glue, path, target, changes)

    utility = devices["StereoGain"]
    for path, target in UTILITY_TARGETS.items():
        _validate_value(utility, path, target)
        _set(utility, path, target, changes)

    if _id_multiset(root) != before_ids:
        raise DrumBussParameterError("Parameter operation changed IDs")
    if root.find("./LiveSet/NextPointeeId").attrib["Value"] != before_next.attrib["Value"]:
        raise DrumBussParameterError("Parameter operation changed NextPointeeId")
    if preservation_snapshot(root) != preserved:
        raise DrumBussParameterError("Parameter operation changed routing, mixer, automation, or clips")
    return changes


def verify_conservative_drum_buss_parameters(root: ET.Element) -> None:
    """Prove the reloaded ALS contains the exact v1.1 parameter state."""
    devices = _target_devices(root)
    eq = devices["Eq8"]
    for index in range(8):
        for parameter_set in ("ParameterA", "ParameterB"):
            if _manual(eq, f"Bands.{index}/{parameter_set}/IsOn").attrib["Value"] != "false":
                raise DrumBussParameterError(f"EQ band {index} {parameter_set} is still enabled")
    for path, expected in GLUE_TARGETS.items():
        if _manual(devices["GlueCompressor"], path).attrib["Value"] != expected:
            raise DrumBussParameterError(f"Glue parameter mismatch: {path}")
    for path, expected in UTILITY_TARGETS.items():
        if _manual(devices["StereoGain"], path).attrib["Value"] != expected:
            raise DrumBussParameterError(f"Utility parameter mismatch: {path}")


def read_drum_buss_parameter_state(root: ET.Element) -> dict:
    """Return the explicit v1.1 controls for template export and audit output."""
    devices = _target_devices(root)
    enabled_eq_bands = []
    eq = devices["Eq8"]
    for index in range(8):
        for parameter_set in ("ParameterA", "ParameterB"):
            if _manual(eq, f"Bands.{index}/{parameter_set}/IsOn").attrib["Value"] == "true":
                enabled_eq_bands.append(f"Bands.{index}/{parameter_set}")
    return {
        "EQ Eight": {"enabled_band_parameter_sets": enabled_eq_bands},
        "Glue Compressor": {
            path: _manual(devices["GlueCompressor"], path).attrib["Value"]
            for path in GLUE_TARGETS
        },
        "Utility": {
            path: _manual(devices["StereoGain"], path).attrib["Value"]
            for path in UTILITY_TARGETS
        },
    }
