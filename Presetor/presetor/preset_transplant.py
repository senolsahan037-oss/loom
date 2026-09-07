"""Real Ableton presets into a Live set FILE, the way Live itself writes them.

Why: the Extensions SDK inserts built-in devices with their default preset
and nothing else (insertDevice, SDK 1.0.0-beta.1). The plan's real
instruments -- a Drum Rack kit with its DrumCells, macros and choke groups,
an Instrument Rack bass, an .adv Operator pad -- can therefore not enter an
OPEN set through the extension. They can enter the set file: a preset is
Live-written XML whose device subtree is the one Live writes into a set;
only the branch wrappers differ (a preset carries BranchPresets, a set
carries Branches) and every set-wide id Live assigns at load time is 0 in
a preset. This module does that conversion deterministically and refuses
to write when any node it produces does not match, tag for tag and
attribute for attribute, a node Live itself wrote.

Measured on Live 12.4.15b1, 2026-09-07: the first artifact loaded with 0
exceptions and 0 repairs after two refusals Live named precisely -- a
list member without an Id (the device chains), and a branch Name that is a
container in a set. Both classes are now checked before writing.

What this does NOT do: invent fields, substitute a device for a preset it
cannot convert, rewrite sample paths, or touch anything but the output
directory. The template set is read only. Nothing here talks to a running
Live: the file is opened by the user, and the extension then writes MIDI
into the tracks this module made.
"""
from __future__ import annotations

import copy
import glob
import gzip
import io
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

_LOOM = Path(__file__).resolve().parents[2]
if str(_LOOM / "SampleAgent" / "sampler_engine") not in sys.path:
    sys.path.insert(0, str(_LOOM / "SampleAgent" / "sampler_engine"))
import alsguard  # noqa: E402  (the measured .als corruption checks)
if str(_LOOM / "Sensei") not in sys.path:
    sys.path.insert(0, str(_LOOM / "Sensei"))
from ableton.kit_resolver import decode_receiving_note  # noqa: E402  (Sensei owns the pad-note fact)

LIVE_APP = Path(os.environ.get("LOOM_LIVE_APP", "/Applications/Ableton Live 12 Beta.app/Contents/App-Resources"))
TEMPLATE = LIVE_APP / "Builtin" / "Templates" / "DefaultLiveSet.als"
# Live-written sets the wrappers are compared against. Read only.
SET_REFERENCES = [
    str(Path.home() / "Music" / "Ableton" / "Factory Packs" / "Sequencers" / "Sequencers Demo Set.als"),
    str(TEMPLATE),
    str(Path.home() / "Music" / "Ableton" / "**" / "*.als"),
]
# Presets Live wrote, per version, for device shapes. Read only.
PRESET_REFERENCE_ROOTS = [LIVE_APP, Path.home() / "Music" / "Ableton" / "Factory Packs", Path.home() / "Music" / "Ableton" / "User Library"]

# Ids Live allocates set-wide from LiveSet/NextPointeeId. In a preset they
# are all 0; in a Live-written set every one is distinct (measured: 4,764
# of 4,764 in the reference set). Everything else that carries an Id is a
# list-local index and is kept as the preset has it.
GLOBAL_ID_TAGS = {"AutomationTarget", "ModulationTarget", "Pointee"}
# The one preset-only field, absent from every device Live writes into a set.
PRESET_ONLY = {"ViewData"}
WRAPPER_TAGS = {"InstrumentBranch", "DrumBranch", "ReturnBranch", "MidiToAudioDeviceChain", "AudioToAudioDeviceChain"}
# Preset-side tag of a node the set writes under another name.
PRESET_TAG_FOR = {"MixerDevice": "AudioBranchMixerDevice"}


class Refused(Exception):
    """The output would not match what Live writes; nothing is written."""


def load(path: Path) -> ET.Element:
    return ET.fromstring(gzip.open(path).read())


def tags(e: ET.Element) -> list[str]:
    return [c.tag for c in e]


def child(e: ET.Element, tag: str) -> ET.Element:
    node = e.find(tag)
    if node is None:
        raise Refused(f"{e.tag} has no {tag}")
    return node


def is_track_global_id(tag: str) -> bool:
    """Ids that differ between the template's two Live-written MIDI-track
    twins (measured): the set-wide pool, every *ModulationTarget of the
    mixer, and the MIDI controller targets. Everything else is list-local."""
    return tag in GLOBAL_ID_TAGS or tag.endswith("ModulationTarget") or tag.startswith("ControllerTargets.")


# --- reference shapes ------------------------------------------------------
class Reference:
    """Ordered child-tag lists and attribute keys of nodes Live wrote.

    SETS give the wrappers (branches, chains) a preset does not carry;
    PRESETS written by the same Live version as the source give the device
    internals (a newer Live adds fields -- the Delay LFO in 12.4 -- and an
    older set would refuse a correct device). Where both exist and differ
    the drift is reported, never hidden.
    """

    def __init__(self) -> None:
        self.set_shapes: dict[str, list[str]] = {}
        self.set_sources: dict[str, str] = {}
        self.attr_keys: dict[tuple[str, str], set[frozenset]] = {}
        self.set_files: list[str] = []
        self.preset_shapes: dict[str, dict[str, list[str]]] = {}      # version -> tag -> shape
        self.preset_sources: dict[str, dict[str, str]] = {}
        self.preset_agreement: dict[str, dict[str, int]] = {}
        self.preset_files: dict[str, int] = {}

    def learn_sets(self, wanted: set[str]) -> None:
        for pattern in SET_REFERENCES:
            for path in sorted(glob.glob(pattern, recursive=True)):
                if "/Backup/" in path:
                    continue
                self._learn_set(path, wanted)
                if all(t in self.set_shapes for t in wanted):
                    return

    def _learn_set(self, path: str, wanted: set[str]) -> None:
        try:
            root = load(Path(path))
        except Exception:  # noqa: BLE001 -- unreadable set, skip
            return
        version = root.get("MinorVersion", "")
        if not version.startswith("12."):
            return
        self.set_files.append(path)
        for node in root.iter():
            if node.tag in wanted and node.tag not in self.set_shapes:
                self.set_shapes[node.tag] = tags(node)
                self.set_sources[node.tag] = f"{Path(path).name} ({version})"
        # Attribute keys per (parent, tag) inside Live-written racks: which
        # nodes carry an Id (list members), which a LomId, which nothing.
        for rack in list(root.iter("InstrumentGroupDevice")) + list(root.iter("DrumGroupDevice")):
            for parent in rack.iter():
                for node in parent:
                    self.attr_keys.setdefault((parent.tag, node.tag), set()).add(frozenset(node.attrib))

    def learn_presets(self, version: str, wanted: set[str], exclude: Path) -> None:
        """Device shapes from presets Live wrote with exactly `version`, the source excluded.
        Stops once every wanted tag has two independent files agreeing."""
        shapes = self.preset_shapes.setdefault(version, {})
        sources = self.preset_sources.setdefault(version, {})
        agreement = self.preset_agreement.setdefault(version, {})
        lookup = {PRESET_TAG_FOR.get(t, t): t for t in wanted}
        files: list[str] = []
        for root_dir in PRESET_REFERENCE_ROOTS:
            if root_dir.is_dir():
                files += glob.glob(str(root_dir / "**" / "*.adv"), recursive=True) + glob.glob(str(root_dir / "**" / "*.adg"), recursive=True)
        for path in sorted(files):
            if Path(path).resolve() == exclude.resolve() or "/Max.app/" in path:
                continue
            if all(agreement.get(t, 0) >= 2 for t in wanted):
                break
            try:
                raw = gzip.open(path).read()
            except Exception:  # noqa: BLE001
                continue
            if f'MinorVersion="{version}"'.encode() not in raw[:400]:
                continue
            if not any(f"<{pt} ".encode() in raw or f"<{pt}>".encode() in raw for pt in lookup):
                continue
            root = ET.fromstring(raw)
            self.preset_files[version] = self.preset_files.get(version, 0) + 1
            seen: set[str] = set()
            for node in root.iter():
                target = lookup.get(node.tag)
                if target is None or target in seen:
                    continue
                seen.add(target)
                shape = [t for t in tags(node) if t not in PRESET_ONLY]
                if target not in shapes:
                    shapes[target] = shape
                    sources[target] = f"{Path(path).name} ({version})"
                    agreement[target] = 1
                elif shapes[target] == shape:
                    agreement[target] += 1
                else:
                    raise Refused(f"Live {version} presets disagree on the shape of <{target}>: {sources[target]} vs {Path(path).name}")

    def expected(self, tag: str, version: str) -> tuple[list[str] | None, str]:
        if tag in self.preset_shapes.get(version, {}):
            return self.preset_shapes[version][tag], f"preset:{self.preset_sources[version][tag]} x{self.preset_agreement[version][tag]}"
        if tag in self.set_shapes:
            return self.set_shapes[tag], f"set:{self.set_sources[tag]}"
        return None, "none"

    def drift(self, version: str) -> dict[str, dict]:
        out = {}
        for tag, shape in self.preset_shapes.get(version, {}).items():
            if tag in self.set_shapes and self.set_shapes[tag] != shape:
                out[tag] = {"set_reference": self.set_sources[tag], "preset_reference": self.preset_sources[version][tag],
                            "only_in_preset_version": [t for t in shape if t not in self.set_shapes[tag]],
                            "only_in_set_version": [t for t in self.set_shapes[tag] if t not in shape]}
        return out


# --- preset -> set conversion ---------------------------------------------
class Transplant:
    """One preset file -> one device element as the set would hold it."""

    DISPLAY_NAME = {"DrumGroupDevice": "Drum Rack", "InstrumentGroupDevice": "Instrument Rack", "DrumCell": "Drum Sampler",
                    "Delay": "Delay", "Saturator": "Saturator", "OriginalSimpler": "Simpler", "MultiSampler": "Sampler",
                    "Operator": "Operator", "UltraAnalog": "Analog", "LoungeLizard": "Electric", "InstrumentVector": "Wavetable",
                    "Collision": "Collision", "StringStudio": "Tension", "Compressor2": "Compressor", "Reverb": "Reverb",
                    "Vinyl": "Vinyl Distortion", "Chorus": "Chorus-Ensemble", "Phaser": "Phaser-Flanger", "Amp": "Amp",
                    "AutoFilter": "Auto Filter", "GlueCompressor": "Glue Compressor", "StereoGain": "Utility",
                    "MidiVelocity": "Velocity", "Eq8": "EQ Eight", "ChannelEq": "Channel EQ", "Limiter": "Limiter",
                    "Hybrid": "Hybrid Reverb", "MultibandDynamics": "Multiband Dynamics", "Redux2": "Redux",
                    "Chorus2": "Chorus-Ensemble", "Roar": "Roar", "Drift": "Drift", "Meld": "Meld"}

    def __init__(self, ref: Reference, version: str) -> None:
        self.ref = ref
        self.version = version
        self.dropped: list[str] = []
        self.constructed: list[str] = []
        self.compared: dict[str, int] = {}
        self.sources: dict[str, str] = {}
        self.counts = {"devices": 0, "chains": 0, "pads": 0, "return_chains": 0, "samples": 0}

    def strip_preset_only(self, node: ET.Element, where: str) -> None:
        for c in list(node):
            if c.tag in PRESET_ONLY:
                node.remove(c)
                self.dropped.append(f"{where}/{c.tag}")

    def must_match(self, node: ET.Element, where: str) -> None:
        expected, source = self.ref.expected(node.tag, self.version)
        if expected is None:
            raise Refused(f"no Live-written reference for <{node.tag}> ({where}); cannot verify it")
        got = tags(node)
        if got != expected:
            only_got = [t for t in got if t not in expected]
            only_ref = [t for t in expected if t not in got]
            raise Refused(f"<{node.tag}> at {where} does not match Live's shape ({source}): extra {only_got}, missing {only_ref}, "
                          f"order_ok={sorted(got) == sorted(expected)}")
        self.compared[node.tag] = self.compared.get(node.tag, 0) + 1
        self.sources[node.tag] = source

    def display(self, device: ET.Element, where: str) -> str:
        user = device.find("UserName")
        if user is not None and user.get("Value"):
            return str(user.get("Value"))
        if device.tag not in self.DISPLAY_NAME:
            raise Refused(f"{where}: no display name known for <{device.tag}>; EffectiveName cannot be derived")
        return self.DISPLAY_NAME[device.tag]

    def effective_name(self, kind: str, index: int, devices: ET.Element, where: str) -> str:
        if kind == "drum":
            rel = devices.find(".//SampleRef/FileRef/RelativePath")
            if rel is None:
                raise Refused(f"{where}: pad has no sample reference to name it by")
            return Path(str(rel.get("Value"))).stem
        names = " | ".join(self.display(d, where) for d in devices)
        if kind == "return":
            return f"{chr(ord('A') + index)} {names}"
        return self.display(devices[0], where) if len(devices) else names

    @staticmethod
    def scalar(tag: str, value: str) -> ET.Element:
        e = ET.Element(tag)
        e.set("Value", value)
        return e

    def convert_device_presets(self, presets: ET.Element, where: str) -> list[ET.Element]:
        devices: list[ET.Element] = []
        for index, preset in enumerate(presets):
            if preset.tag == "AbletonDevicePreset":
                device = copy.deepcopy(child(preset, "Device")[0])
            elif preset.tag == "GroupDevicePreset":
                device = self.convert_rack(preset, f"{where}/{index}")
            else:
                raise Refused(f"unknown device preset <{preset.tag}> at {where}")
            device.set("Id", str(index))
            self.strip_preset_only(device, f"{where}/{device.tag}")
            self.must_match(device, f"{where}/{device.tag}")
            self.counts["devices"] += 1
            if device.tag == "DrumCell":
                self.counts["samples"] += len(device.findall(".//SampleRef/FileRef"))
            devices.append(device)
        return devices

    def convert_branch(self, preset: ET.Element, kind: str, index: int, where: str) -> ET.Element:
        """*BranchPreset -> the set's branch, field by field in Live's order
        (measured on the reference set). Every value comes from the preset
        except what Live fills in at load time: five defaults, and the
        derived EffectiveName."""
        branch_tag = {"instrument": "InstrumentBranch", "drum": "DrumBranch", "return": "ReturnBranch"}[kind]
        chain_tag = "AudioToAudioDeviceChain" if kind == "return" else "MidiToAudioDeviceChain"
        branch = ET.Element(branch_tag)
        branch.set("Id", str(index))
        expected_preset = ["Name", "IsSoloed", "DevicePresets", "MixerPreset", "BranchSelectorRange", "SessionViewBranchWidth",
                           "DocumentColorIndex", "AutoColored", "AutoColorScheme", "SourceContext"] + (["ZoneSettings"] if kind != "return" else [])
        if tags(preset) != expected_preset:
            raise Refused(f"<{preset.tag}> at {where} has fields this converter does not know: {tags(preset)}")

        branch.append(self.scalar("LomId", "0"))
        # A set writes the branch name as a container: UserName is the
        # preset's Name, EffectiveName is what Live derives from the chain.
        user_name = child(preset, "Name").get("Value", "")
        name = ET.SubElement(branch, "Name")
        effective = ET.SubElement(name, "EffectiveName")
        ET.SubElement(name, "UserName").set("Value", user_name)
        ET.SubElement(name, "Annotation").set("Value", "")
        ET.SubElement(name, "MemorizedFirstClipName").set("Value", "")
        branch.append(self.scalar("IsSelected", "true" if index == 0 else "false"))
        chain_wrap = ET.SubElement(branch, "DeviceChain")
        chain = ET.SubElement(chain_wrap, chain_tag)
        chain.set("Id", "0")  # a list member in Live's set: "Not all list members have Ids" without it
        devices = ET.SubElement(chain, "Devices")
        for device in self.convert_device_presets(child(preset, "DevicePresets"), f"{where}/Devices"):
            devices.append(device)
        ET.SubElement(chain, "SignalModulations")
        self.must_match(chain, f"{where}/{chain_tag}")
        effective.set("Value", user_name or self.effective_name(kind, index, devices, where))
        branch.append(copy.deepcopy(child(preset, "BranchSelectorRange")))
        branch.append(copy.deepcopy(child(preset, "IsSoloed")))
        branch.append(copy.deepcopy(child(preset, "SessionViewBranchWidth")))
        branch.append(self.scalar("IsHighlightedInSessionView", "false"))
        source = child(preset, "SourceContext")
        if len(source) == 0:
            sc = ET.SubElement(branch, "SourceContext")
            ET.SubElement(sc, "Value")
        else:
            # Preset: SourceContext/BranchSourceContext. Set: SourceContext/Value/BranchSourceContext.
            if tags(source) != ["BranchSourceContext"]:
                raise Refused(f"{where}: preset SourceContext is not one BranchSourceContext: {tags(source)}")
            sc = ET.SubElement(branch, "SourceContext")
            value = ET.SubElement(sc, "Value")
            inner = copy.deepcopy(source[0])
            self.must_match(inner, f"{where}/SourceContext/Value/BranchSourceContext")
            value.append(inner)
        branch.append(self.scalar("Color", child(preset, "DocumentColorIndex").get("Value", "0")))
        branch.append(copy.deepcopy(child(preset, "AutoColored")))
        branch.append(copy.deepcopy(child(preset, "AutoColorScheme")))
        branch.append(self.scalar("SoloActivatedInSessionMixer", "false"))
        wrapper = ET.SubElement(branch, "DevicesListWrapper")
        wrapper.set("LomId", "0")
        mixer_presets = child(preset, "MixerPreset")
        if tags(mixer_presets) != ["AbletonDevicePreset"]:
            raise Refused(f"{where}: MixerPreset is not one AbletonDevicePreset")
        mixer = copy.deepcopy(child(child(mixer_presets[0], "Device"), "AudioBranchMixerDevice"))
        mixer.tag = "MixerDevice"
        mixer.attrib.pop("Id", None)
        self.strip_preset_only(mixer, f"{where}/MixerDevice")
        self.must_match(mixer, f"{where}/MixerDevice")
        branch.append(mixer)
        if kind == "instrument":
            branch.append(copy.deepcopy(child(preset, "ZoneSettings")))
        elif kind == "drum":
            info = copy.deepcopy(child(preset, "ZoneSettings"))
            info.tag = "BranchInfo"
            if tags(info) != ["ReceivingNote", "SendingNote", "ChokeGroup"]:
                raise Refused(f"{where}: drum ZoneSettings is not ReceivingNote/SendingNote/ChokeGroup: {tags(info)}")
            branch.append(info)
        self.must_match(branch, where)
        self.constructed.append(f"{where}: LomId, IsSelected, IsHighlightedInSessionView, SoloActivatedInSessionMixer, DevicesListWrapper, "
                                f"Name/EffectiveName={effective.get('Value')!r}")
        return branch

    def convert_rack(self, group_preset: ET.Element, where: str) -> ET.Element:
        if tags(group_preset) != ["OverwriteProtectionNumber", "Device", "PresetRef", "BranchPresets", "ReturnBranchPresets"]:
            raise Refused(f"<GroupDevicePreset> at {where} has fields this converter does not know: {tags(group_preset)}")
        device = copy.deepcopy(child(group_preset, "Device")[0])
        kind = {"InstrumentGroupDevice": "instrument", "DrumGroupDevice": "drum"}.get(device.tag)
        if kind is None:
            raise Refused(f"rack device <{device.tag}> at {where} is not a rack this converter knows")
        branches = child(device, "Branches")
        returns = child(device, "ReturnBranches")
        if len(branches) or len(returns):
            raise Refused(f"{where}: the preset's rack already carries branches; expected empty Branches/ReturnBranches")
        for index, preset in enumerate(child(group_preset, "BranchPresets")):
            branches.append(self.convert_branch(preset, kind, index, f"{where}/{device.tag}/Branches/{index}"))
            self.counts["pads" if kind == "drum" else "chains"] += 1
        for index, preset in enumerate(child(group_preset, "ReturnBranchPresets")):
            returns.append(self.convert_branch(preset, "return", index, f"{where}/{device.tag}/ReturnBranches/{index}"))
            self.counts["return_chains"] += 1
        return device

    def convert_preset(self, preset_root: ET.Element, where: str) -> ET.Element:
        """The device a preset file holds: a rack (.adg), a wrapped device
        (AbletonDevicePreset) or, as Live writes .adv files, the device itself."""
        if len(preset_root) == 0:
            raise Refused(f"{where}: empty preset")
        top = preset_root[0]
        if top.tag == "GroupDevicePreset":
            device = self.convert_rack(top, where)
        elif top.tag == "AbletonDevicePreset":
            device = copy.deepcopy(child(top, "Device")[0])
        else:
            device = copy.deepcopy(top)
        self.strip_preset_only(device, f"{where}/{device.tag}")
        self.must_match(device, f"{where}/{device.tag}")
        device.set("Id", "0")
        self.counts["devices"] += 1
        return device


def device_tags_wanted(preset_root: ET.Element) -> set[str]:
    """Every device tag the preset will put into the set (chain devices only;
    a MixerPreset's AudioBranchMixerDevice becomes the branch's MixerDevice)."""
    wanted = {"MixerDevice", "BranchSourceContext"}
    top = preset_root[0]
    if top.tag == "GroupDevicePreset":
        wanted |= {"InstrumentGroupDevice", "DrumGroupDevice"}
        wanted |= {d.tag for p in top.iter("DevicePresets") for a in p if a.tag == "AbletonDevicePreset" for d in child(a, "Device")}
        wanted |= {d.tag for p in top.iter("DevicePresets") for a in p if a.tag == "GroupDevicePreset" for d in child(a, "Device")}
    elif top.tag == "AbletonDevicePreset":
        wanted |= {d.tag for d in child(top, "Device")}
    else:
        wanted.add(top.tag)
    return wanted


def allocate_global_ids(node: ET.Element, next_id: int) -> tuple[int, int]:
    """Give every set-wide id the preset left at 0 a fresh value, in document order."""
    given = 0
    for e in node.iter():
        if e.tag in GLOBAL_ID_TAGS:
            if e.get("Id") != "0":
                raise Refused(f"<{e.tag} Id={e.get('Id')}> in the preset is not 0; a preset-local value here cannot be trusted")
            e.set("Id", str(next_id))
            next_id += 1
            given += 1
    return next_id, given


def serialize(root: ET.Element) -> bytes:
    """Bytes Live accepts: its own declaration, tab indentation, gzip without name or mtime."""
    ET.indent(root, space="\t")
    text = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0) as handle:
        handle.write(text.encode("utf-8"))
    return buffer.getvalue()


# --- the set ---------------------------------------------------------------
class SetBuilder:
    """A set file from Live's own template: named MIDI tracks, each holding
    one real preset, and the project tempo. Nothing else is touched."""

    def __init__(self, template: Path = TEMPLATE) -> None:
        self.template = template
        self.root = load(template)
        self.baseline = alsguard.baseline(load(template))
        self.report: dict[str, Any] = {"template": str(template), "template_mode": "read-only",
                                       "template_version": {"MinorVersion": self.root.get("MinorVersion"), "Creator": self.root.get("Creator")},
                                       "tracks": [], "refused": []}
        self.ref = Reference()
        self.ref.learn_sets(WRAPPER_TAGS | {"MixerDevice", "BranchSourceContext", "InstrumentGroupDevice", "DrumGroupDevice"})
        self._learned_device_tags: set[str] = set()
        self.pointee = child(self.root, "./LiveSet/NextPointeeId")
        self.tracks = child(self.root, "./LiveSet/Tracks")
        self._twin = copy.deepcopy(next(t for t in self.tracks if t.tag == "MidiTrack"))
        self._free_midi = [t for t in self.tracks if t.tag == "MidiTrack"]

    # -- tracks
    def add_midi_track(self, name: str) -> ET.Element:
        """The template's own empty MIDI tracks first; then a clone of the
        template's MIDI-track twin with every twin-differing id reallocated."""
        if self._free_midi:
            track = self._free_midi.pop(0)
            origin = "template"
        else:
            track = copy.deepcopy(self._twin)
            next_id = int(self.pointee.get("Value"))
            for e in track.iter():
                if e.get("Id") is not None and is_track_global_id(e.tag):
                    e.set("Id", str(next_id))
                    next_id += 1
            self.pointee.set("Value", str(next_id))
            track.set("Id", str(max(int(t.get("Id")) for t in self.tracks if t.get("Id")) + 1))
            last_midi = max(i for i, t in enumerate(self.tracks) if t.tag == "MidiTrack")
            self.tracks.insert(last_midi + 1, track)
            origin = "cloned_from_template_twin"
        child(child(track, "Name"), "EffectiveName").set("Value", name)
        child(child(track, "Name"), "UserName").set("Value", name)
        self.report["tracks"].append({"name": name, "track_id": track.get("Id"), "origin": origin, "preset": None})
        return track

    # -- presets
    def place_preset(self, track: ET.Element, preset_path: Path) -> dict[str, Any]:
        preset_root = load(preset_path)
        version = str(preset_root.get("MinorVersion"))
        wanted = device_tags_wanted(preset_root)
        new_tags = wanted - self._learned_device_tags
        if new_tags or version not in self.ref.preset_shapes:
            self.ref.learn_presets(version, wanted, preset_path)
            self._learned_device_tags |= wanted
        weak = [t for t in wanted if self.ref.preset_agreement.get(version, {}).get(t, 0) < 2 and self.ref.expected(t, version)[0] is None]
        if weak:
            raise Refused(f"no Live-written reference for {sorted(weak)} (preset version {version})")
        work = Transplant(self.ref, version)
        where = child(child(track, "Name"), "UserName").get("Value", "track")
        device = work.convert_preset(preset_root, where)
        self._audit_attributes(device)
        first_id = int(self.pointee.get("Value"))
        next_id, given = allocate_global_ids(device, first_id)
        self.pointee.set("Value", str(next_id))
        devices = child(track, "DeviceChain/DeviceChain/Devices")
        if len(devices):
            raise Refused(f"{where}: track already has devices")
        devices.append(device)
        entry = next(t for t in self.report["tracks"] if t["track_id"] == track.get("Id"))
        user = device.find("UserName")
        entry["preset"] = {
            "path": str(preset_path), "version": version, "device": device.tag,
            "name": user.get("Value") if user is not None else None,
            "carried": work.counts, "ids_allocated": given,
            # ReceivingNote is stored inverted; the manifest carries the MIDI notes Live will report.
            "pad_notes": sorted(decode_receiving_note(n.get("Value")) for n in device.iter("ReceivingNote")),
            "macros": len([m for m in device if m.tag.startswith("MacroControls.")]),
            "choke_groups": sorted({int(c.get("Value")) for c in device.iter("ChokeGroup")}),
            "compared": {t: f"{n} vs {work.sources.get(t)}" for t, n in work.compared.items()},
            "dropped_preset_only": len(work.dropped),
            "constructed_branches": len(work.constructed),
            "version_drift_between_references": self.ref.drift(version),
        }
        return entry["preset"]

    def _audit_attributes(self, device: ET.Element) -> None:
        unknown: set[str] = set()
        for parent in device.iter():
            for node in parent:
                known = self.ref.attr_keys.get((parent.tag, node.tag))
                if known is None:
                    unknown.add(f"{parent.tag}/{node.tag}")
                    continue
                if frozenset(node.attrib) not in known:
                    raise Refused(f"<{parent.tag}/{node.tag}> carries attributes {sorted(node.attrib)}; Live writes {[sorted(k) for k in known]}")
        structural = {"DeviceChain/MidiToAudioDeviceChain", "DeviceChain/AudioToAudioDeviceChain", "MidiToAudioDeviceChain/Devices",
                      "AudioToAudioDeviceChain/Devices", "SourceContext/Value", "Value/BranchSourceContext",
                      "Branches/InstrumentBranch", "Branches/DrumBranch", "ReturnBranches/ReturnBranch"}
        if unknown & structural:
            raise Refused(f"structural nodes without an attribute reference: {sorted(unknown & structural)}")

    # -- project
    def set_tempo(self, bpm: float) -> None:
        node = child(self.root, "./LiveSet/MainTrack/DeviceChain/Mixer/Tempo/Manual")
        node.set("Value", str(float(bpm)).rstrip("0").rstrip(".") if float(bpm) != int(bpm) else str(int(bpm)))
        self.report["tempo"] = float(bpm)

    # -- checks and output
    def finish(self, out_path: Path) -> dict[str, Any]:
        problems = alsguard.check(self.root, self.baseline)
        if problems:
            raise Refused(f"alsguard: {problems}")
        next_id = int(self.pointee.get("Value"))
        pool = [int(e.get("Id")) for e in self.root.iter() if is_track_global_id(e.tag) and e.get("Id") and e.get("Id").isdigit()]
        if len(pool) != len(set(pool)) or max(pool) >= next_id:
            raise Refused(f"set-wide ids are not unique or NextPointeeId is behind: {len(pool)} ids, {len(set(pool))} distinct, max {max(pool)}, next {next_id}")
        track_ids = [t.get("Id") for t in self.tracks]
        if len(track_ids) != len(set(track_ids)):
            raise Refused(f"duplicate track ids: {track_ids}")
        order = [t.tag for t in self.tracks]
        if order != sorted(order, key=lambda t: {"MidiTrack": 0, "AudioTrack": 1, "GroupTrack": 1, "ReturnTrack": 2}.get(t, 1)):
            raise Refused(f"track order breaks Live's rule (returns last): {order}")
        # Sample files must exist: a pad without its sample is silent. A
        # device's preset back-reference (LastPresetRef/PresetRef) may point
        # at a file Live no longer ships ("Simple Delay/...adv" in a 12.4
        # rack); Live keeps such references without complaint, so they are
        # reported, not blocking.
        samples, preset_refs = [], []
        for sample_ref in self.root.iter("SampleRef"):
            fr = sample_ref.find("FileRef")
            rel, pack = (fr.find("RelativePath"), fr.find("LivePackName")) if fr is not None else (None, None)
            if rel is None or pack is None or not pack.get("Value"):
                continue
            base = LIVE_APP / "Core Library" if pack.get("Value") == "Core Library" else Path.home() / "Music" / "Ableton" / "Factory Packs" / str(pack.get("Value"))
            samples.append({"pack": pack.get("Value"), "relative": rel.get("Value"), "exists": (base / str(rel.get("Value"))).is_file()})
        for ref in list(self.root.iter("FilePresetRef")):
            rel = ref.find("FileRef/RelativePath")
            pack = ref.find("FileRef/LivePackName")
            if rel is not None and rel.get("Value"):
                base = LIVE_APP / "Core Library" if (pack is not None and pack.get("Value") == "Core Library") else None
                preset_refs.append({"relative": rel.get("Value"), "exists": bool(base and (base / str(rel.get("Value"))).is_file())})
        missing = [s for s in samples if not s["exists"]]
        if missing:
            raise Refused(f"sample references do not all resolve on this machine: {missing[:5]}")
        self.report["preset_back_references"] = {"total": len(preset_refs), "unresolved": [r["relative"] for r in preset_refs if not r["exists"]]}
        data = serialize(self.root)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        back = ET.fromstring(gzip.decompress(data))
        self.report.update({
            "written": True, "artifact": str(out_path), "bytes": len(data),
            "ids": {"next_pointee_id": next_id, "set_wide_ids": len(pool)},
            "samples": {"pack_relative": len(samples), "resolved_on_disk": len(samples) - len(missing), "paths_rewritten": 0},
            "static_validation": {"alsguard": "clean", "tag_count_written": sum(1 for _ in self.root.iter()),
                                  "tag_count_read_back": sum(1 for _ in back.iter()),
                                  "xml_declaration": gzip.decompress(data)[:38].decode(), "gzip_flags_mtime": [data[3], int.from_bytes(data[4:8], "little")],
                                  "tracks_read_back": [(t.tag, child(t, "Name/UserName").get("Value")) for t in back.find("LiveSet/Tracks")]},
        })
        manifest = {"artifact": str(out_path), "template": str(self.template), "tempo": self.report.get("tempo"),
                    "tracks": {t["name"]: t["preset"] and {k: t["preset"][k] for k in ("path", "version", "device", "name", "pad_notes")} for t in self.report["tracks"]}}
        manifest_path = out_path.with_suffix(".loom-manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        self.report["manifest"] = str(manifest_path)
        return self.report
