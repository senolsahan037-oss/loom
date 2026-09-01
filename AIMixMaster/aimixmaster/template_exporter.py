"""Export a deterministic, ID-independent BUSS template from a verified ALS."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from .als_io import load_als
from .drum_buss_parameters import read_drum_buss_parameter_state
from .project_analyzer import analyze_tracks, direct_devices, find_unique_track, value
from .verification import verify_drum_buss


DEVICE_NAMES = {
    "Eq8": "EQ Eight",
    "GlueCompressor": "Glue Compressor",
    "StereoGain": "Utility",
}
REQUIRED_ROLES = ("KICK BUSS", "SNARE BUSS", "PERC BUSS", "LIVE BASS", "New Old Sub")
ACTUAL_NAMES = {"New Old Sub": "# New Old Sub"}


def _db(linear: float) -> float:
    return round(20 * math.log10(linear), 4)


def _parameter_state_sha256(device: ET.Element) -> str:
    """Hash serialized parameter state without project-local object IDs."""
    normalized = deepcopy(device)
    for node in normalized.iter():
        node.attrib.pop("Id", None)
        if node.tag == "PointeeId":
            node.attrib["Value"] = ""
    return hashlib.sha256(ET.tostring(normalized, encoding="utf-8")).hexdigest()


def export_boom_bap_95_drum_bus(als_path: Path, output_path: Path) -> None:
    proof = verify_drum_buss(als_path)
    root = load_als(als_path).getroot()
    tracks = analyze_tracks(root)
    by_id = {track.track_id: track.name for track in tracks}
    routing = {}
    faders = {}
    for role in REQUIRED_ROLES + ("DRUM BUSS",):
        actual_name = ACTUAL_NAMES.get(role, role)
        track = find_unique_track(root, actual_name)
        volume = float(value(track.element.find("./DeviceChain/Mixer/Volume/Manual"), "1"))
        faders[role] = _db(volume)
        if role != "DRUM BUSS":
            routing[role] = by_id.get(track.group_id, "")

    target = find_unique_track(root, "DRUM BUSS")
    devices = direct_devices(target.element)
    document = {
        "schema_version": 1,
        "project": "Golden Step_RECOVERED",
        "verification": {
            "method": "ALS reload parser and Ableton Live runtime",
            "direct_drum_buss_device_count": len(proof.device_tags),
            "next_pointee_id": proof.next_pointee_id,
        },
        "genre": "95 BPM boom-bap / sample-chop",
        "bpm_range": {"min": 90, "max": 100, "reference": 95},
        "required_track_roles": list(REQUIRED_ROLES),
        "actual_track_names": ACTUAL_NAMES,
        "routing": routing,
        "fader_offsets_db": faders,
        "device_order": [DEVICE_NAMES[device.tag] for device in devices],
        "device_parameters": [
            {
                "name": DEVICE_NAMES[device.tag],
                "native_tag": device.tag,
                "parameter_state_sha256": _parameter_state_sha256(device),
                "parameters_modified_by_builder": False,
            }
            for device in devices
        ],
        "parameter_values": read_drum_buss_parameter_state(root),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
