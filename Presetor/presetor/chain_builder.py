"""Bir track'in cihaz zincirini, ayni projedeki baska bir track'ten kopyalar.

Neden kopyalama: gecerli bir Ableton cihaz XML'ini sifirdan uretmek guvenilir
degil, ama Live'in kendi yazdigi bir cihazi klonlamak guvenilir. AIMixMaster'in
buss_builder'i bunu DRUM BUSS icin kanitladi; buradaki tek fark, hangi track'ten
hangi track'e kopyalanacaginin sabit olmamasi.

Fail-closed: hedefin zinciri bosalmadikca yazilmaz, iki track'in de routing /
mixer / otomasyon / klip alanlari yazma oncesi ve sonrasi ayni olmak zorundadir.
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
    """Adiyla tek bir track bulur -- UserName bos ise EffectiveName ile.

    project_analyzer.find_unique_track yalnizca UserName'e bakar, ki cogu
    projede bos. Buradaki plan display_name ile kuruldugu icin yerlestirme de
    ayni adla aramak zorunda, yoksa plan bulunan track'i transplant bulamaz.
    Ayni benzersizlik kurali korunur: tam olarak bir eslesme sart.
    """
    matches = [track for track in iter_tracks(root) if display_name(track) == name]
    if len(matches) != 1:
        raise ChainBuildError(f"Expected one track named {name!r}, found {len(matches)}")
    return matches[0]


def chain_of(track_element: ET.Element) -> tuple[str, ...]:
    return tuple(normalized_device_name(device) for device in direct_devices(track_element))


def find_donors(root: ET.Element, wanted_chain: tuple[str, ...]) -> list[str]:
    """Bu projede istenen zincire birebir sahip track adlari."""
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
