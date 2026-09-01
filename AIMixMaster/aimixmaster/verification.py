"""Post-write proof checks for deterministic ALS mutations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .als_io import load_als
from .buss_builder import EXPECTED_DRUM_BUSS_DEVICE_TAGS
from .project_analyzer import direct_devices, find_unique_track, preservation_snapshot


class VerificationError(ValueError):
    """Raised when a written ALS does not contain the requested mutation."""


@dataclass(frozen=True)
class VerificationResult:
    path: Path
    target_name: str
    device_tags: tuple[str, ...]
    next_pointee_id: int


def verify_drum_buss(
    path: Path,
    target_name: str = "DRUM BUSS",
    preserved_before: dict | None = None,
) -> VerificationResult:
    root = load_als(path).getroot()
    target = find_unique_track(root, target_name)
    tags = tuple(device.tag for device in direct_devices(target.element))
    if tags != EXPECTED_DRUM_BUSS_DEVICE_TAGS:
        raise VerificationError(f"{target_name!r} reload chain is {tags!r}, not required chain")
    next_node = root.find("./LiveSet/NextPointeeId")
    if next_node is None or "Value" not in next_node.attrib:
        raise VerificationError("NextPointeeId missing after reload")
    if preserved_before is not None and preservation_snapshot(root) != preserved_before:
        raise VerificationError("Routing, mixer, automation, or clips changed after reload")
    return VerificationResult(path, target_name, tags, int(next_node.attrib["Value"]))
