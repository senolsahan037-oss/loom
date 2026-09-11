#!/usr/bin/env python3
# © 2026 Şenol Şahan / SubverseLab · Loom · https://subverselab.com/loom
"""One owner per fact: the reader-layer contract.

Loom reads the same facts from several engines. Before 2026-09-06 five of
those facts had more than one reader and two of them disagreed silently -- a
track's name resolved differently depending on which tool asked, and the
General MIDI pad map was an assumption no answer mentioned. This suite pins
the contract that replaced that:

  1. track name        -> aimixmaster.project_analyzer.resolve_track_name
  2. device chain      -> aimixmaster.project_analyzer.resolve_device_chain
  3. tempo / key / signature -> aimixmaster.project_analyzer.musical_context
  4. pad notes + role vocabulary -> ableton.kit_resolver.resolve_pad_notes
  5. sample reference  -> ableton.kit_resolver (absolute / pack_relative / missing)

Two things are checked for each: the value is right, and every reader in the
tree is *the same callable* rather than a copy that happens to agree today.

Hermetic: synthetic XML and a temp pack folder. No Live, no bridge, no user
data, nothing written inside the repo.
"""
from __future__ import annotations

import gzip
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "AIMixMaster", ROOT / "scripts", ROOT / "Presetor", ROOT / "Sensei"):
    sys.path.insert(0, str(path))

from aimixmaster.project_analyzer import (  # noqa: E402
    device_name,
    musical_context,
    resolve_device_chain,
    resolve_track_name,
    track_name,
)

checks: list[str] = []
failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    (checks if condition else failures).append(label if condition else f"{label}  {str(detail)[:400]}")


def track_xml(user: str | None, effective: str | None, devices: str = "") -> ET.Element:
    names = "".join(f'<{tag} Value="{value}" />' for tag, value in
                    (("UserName", user), ("EffectiveName", effective)) if value is not None)
    return ET.fromstring(
        f'<MidiTrack Id="1"><Name>{names}</Name>'
        f'<DeviceChain><DeviceChain><Devices>{devices}</Devices></DeviceChain></DeviceChain></MidiTrack>')


# ---- 1) track name ---------------------------------------------------------
check("what the producer typed wins over what Live shows",
      resolve_track_name(track_xml("MY KICK", "1-MIDI")).value == "MY KICK")
check("... and the answer says which field it came from, keeping the other",
      resolve_track_name(track_xml("MY KICK", "1-MIDI")).source == "user_name"
      and resolve_track_name(track_xml("MY KICK", "1-MIDI")).alternatives == {"effective_name": "1-MIDI"},
      resolve_track_name(track_xml("MY KICK", "1-MIDI")).as_dict())
check("an empty UserName falls through to EffectiveName instead of losing the track",
      resolve_track_name(track_xml("", "808 SUB")).value == "808 SUB"
      and resolve_track_name(track_xml("", "808 SUB")).source == "effective_name")
missing = resolve_track_name(track_xml(None, None))
check("a track with no name at all is missing, not guessed", missing.value == "" and missing.confidence == "missing", missing.as_dict())

# every reader in the tree must be the same callable
import extract_device_chains as edc  # noqa: E402
import extract_sound_sources as ess  # noqa: E402
from presetor import chain_builder, chain_planner  # noqa: E402
from aimixmaster import gain_staging  # noqa: E402
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("loom_shapes", ROOT / "scripts" / "extract_arrangement_shapes.py")
shapes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shapes)

name_readers = {
    "extract_device_chains.display_name": edc.display_name,
    "extract_sound_sources.display_name": ess.display_name,
    "presetor.chain_builder.display_name": chain_builder.display_name,
    "presetor.chain_planner.display_name": chain_planner.display_name,
    "extract_arrangement_shapes.display_name": shapes.display_name,
    "project_analyzer.track_name": track_name,
}
check("every track-name reader in the tree is the one owner, not a copy",
      len(set(name_readers.values())) == 1,
      {name: f"{fn.__module__}.{fn.__name__}" for name, fn in name_readers.items()})
check("the arrangement scan no longer prefers EffectiveName while the rest prefer UserName",
      shapes.display_name(track_xml("TYPED", "LIVE SHOWS")) == "TYPED")

# ---- 2) device chain -------------------------------------------------------
rack = ('<AudioEffectGroupDevice><Branches><Branch><DeviceChain><AudioToAudioDeviceChain><Devices>'
        '<Eq8 /><GlueCompressor /></Devices></AudioToAudioDeviceChain></DeviceChain></Branch></Branches></AudioEffectGroupDevice>')
racked = track_xml("BUS", None, rack + "<StereoGain />")
top = resolve_device_chain(racked)
expanded = resolve_device_chain(racked, expand=True)
check("the top-level view and the rack-expanded view are different facts, each named",
      top.value == ("AudioEffectGroupDevice", "Utility") and top.source == "top_level"
      and expanded.value == ("EQ Eight", "Glue Compressor", "Utility") and expanded.source == "rack_expanded",
      (top.as_dict(), expanded.as_dict()))
check("the expanded answer still carries the top-level chain and whether a rack was involved",
      expanded.alternatives["top_level"] == ("AudioEffectGroupDevice", "Utility") and expanded.alternatives["uses_rack"] is True)
check("Presetor's chain_of is the unexpanded view (what a transplant moves)",
      chain_builder.chain_of(racked) == ("AudioEffectGroupDevice", "Utility"))
check("one device-name alias table for every reader",
      gain_staging.normalized_device_name is device_name and edc.normalized_device_name is device_name
      and device_name(ET.Element("Eq8")) == "EQ Eight")

# ---- 3) tempo, key, time signature -----------------------------------------
full = ET.fromstring(
    '<Ableton><LiveSet><MasterTrack><AutomationEnvelopes /><DeviceChain><Mixer><Tempo>'
    '<Manual Value="93.5" /></Tempo></Mixer></DeviceChain></MasterTrack>'
    '<ScaleInformation><Root Value="2" /><Name Value="minor" /></ScaleInformation>'
    '<TimeSignature><TimeSignatures><RemoteableTimeSignature><Numerator Value="6" />'
    '<Denominator Value="8" /></RemoteableTimeSignature></TimeSignatures></TimeSignature>'
    "</LiveSet></Ableton>")
context = musical_context(full)
check("tempo is read with its source", context["tempo"].value == 93.5 and context["tempo"].source == "master_track", context["tempo"].as_dict())
check("the key is read from the set's own ScaleInformation",
      context["key"].value == {"root": "D", "scale": "Minor"} and context["key"].source == "scale_information", context["key"].as_dict())
check("6/8 resolves to 3 beats a bar, the same contract the writers use",
      context["beats_per_bar"].value == 3.0 and context["beats_per_bar"].alternatives["numerator"] == 6, context["beats_per_bar"].as_dict())
empty = musical_context(ET.fromstring("<Ableton><LiveSet /></Ableton>"))
check("a set with no key is reported missing, never defaulted to C major",
      empty["key"].value is None and empty["key"].confidence == "missing" and empty["beats_per_bar"].value is None,
      {k: v.as_dict() for k, v in empty.items()})

# ---- 3b) arrangement clips: timeline position, session clips excluded, audio counted, outliers named ----
import gzip as _gzip, tempfile as _tempfile
_SYN = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0_12402" Creator="Ableton Live 12.4d1"><LiveSet><Tracks>
<MidiTrack Id="1"><Name><EffectiveName Value="Kit"/><UserName Value=""/></Name><DeviceChain><MainSequencer>
 <ClipSlotList><ClipSlot Id="0"><ClipSlot><Value><MidiClip Id="9" Time="0"><CurrentStart Value="0"/><CurrentEnd Value="32"/><Notes><KeyTracks><KeyTrack Id="0"><Notes><MidiNoteEvent Time="0" Duration="0.25" Velocity="100"/></Notes><MidiKey Value="36"/></KeyTrack></KeyTracks></Notes></MidiClip></Value></ClipSlot></ClipSlot></ClipSlotList>
 <ClipTimeable><ArrangerAutomation><Events>
  <MidiClip Id="1" Time="8"><CurrentStart Value="0"/><CurrentEnd Value="4"/><Notes><KeyTracks><KeyTrack Id="0"><Notes><MidiNoteEvent Time="0" Duration="0.25" Velocity="100"/><MidiNoteEvent Time="1" Duration="0.25" Velocity="100"/></Notes><MidiKey Value="36"/></KeyTrack></KeyTracks></Notes></MidiClip>
  <MidiClip Id="2" Time="400"><CurrentStart Value="400"/><CurrentEnd Value="402"/><Notes><KeyTracks/></Notes></MidiClip>
 </Events></ArrangerAutomation></ClipTimeable></MainSequencer><DeviceChain><Devices><InstrumentGroupDevice Id="0"><Branches><InstrumentBranch Id="0"><DeviceChain><MidiToAudioDeviceChain Id="0"><Devices><DrumGroupDevice Id="0"><Branches><DrumBranch Id="0"><DeviceChain><MidiToAudioDeviceChain Id="0"><Devices><DrumCell Id="0"><Player><MultiSampleMap><SampleParts><MultiSamplePart Id="0"><SampleRef><FileRef><RelativePath Value="Samples/Kick BNYX 1.wav"/></FileRef></SampleRef></MultiSamplePart></SampleParts></MultiSampleMap></Player></DrumCell></Devices></MidiToAudioDeviceChain></DeviceChain></DrumBranch></Branches></DrumGroupDevice></Devices></MidiToAudioDeviceChain></DeviceChain></InstrumentBranch></Branches></InstrumentGroupDevice></Devices></DeviceChain></DeviceChain></MidiTrack>
<AudioTrack Id="2"><Name><EffectiveName Value="Loop"/><UserName Value=""/></Name><DeviceChain><MainSequencer><Sample><ArrangerAutomation><Events>
  <AudioClip Id="3" Time="0"><CurrentStart Value="10"/><CurrentEnd Value="11.5"/><SampleRef><FileRef><RelativePath Value="Samples/Gencebay chop.wav"/></FileRef></SampleRef></AudioClip>
  <AudioClip Id="4" Time="1.5"><CurrentStart Value="12"/><CurrentEnd Value="13.5"/><SampleRef><FileRef><RelativePath Value="Samples/Gencebay chop.wav"/></FileRef></SampleRef></AudioClip>
 </Events></ArrangerAutomation></Sample></MainSequencer></DeviceChain></AudioTrack>
<MidiTrack Id="3"><Name><EffectiveName Value="Bass Loom"/><UserName Value=""/></Name><DeviceChain><MainSequencer><ClipTimeable><ArrangerAutomation><Events/></ArrangerAutomation></ClipTimeable></MainSequencer><DeviceChain><Devices><OriginalSimpler Id="0"><Player><MultiSampleMap><SampleParts><MultiSamplePart Id="0"><SampleRef><FileRef><RelativePath Value="Samples/808 Oracle 1.wav"/></FileRef></SampleRef></MultiSamplePart></SampleParts></MultiSampleMap></Player></OriginalSimpler></Devices></DeviceChain></DeviceChain></MidiTrack>
</Tracks><MainTrack><DeviceChain><Mixer><Tempo><Manual Value="160"/></Tempo></Mixer></DeviceChain></MainTrack></LiveSet></Ableton>"""
_syn_path = Path(_tempfile.mkdtemp(prefix="loom_reader_")) / "syn.als"
with _gzip.open(_syn_path, "wb") as _h:
    _h.write(_SYN.encode("utf-8"))
_proj = shapes.read_project(str(_syn_path))
_rows = {r["track"]: r for r in _proj["tracks"]}
check("arrangement clips are read by their Time attribute: the Kit clip at beat 8 (CurrentStart 0) is an event at 8..12, the session clip is not an event",
      sorted(b for b, t in _proj["events"] if t == 0) == [8.0, 12.0] and _rows["Kit"]["midi_clips"] == 2 and _rows["Kit"]["outlier_clips"] == 1 and _rows["Kit"]["notes"] == 2, (_proj["events"], _rows.get("Kit")))
check("audio arrangement clips are counted on their own path, positioned by Time",
      _rows["Loop"]["audio_clips"] == 2 and _rows["Loop"]["first_beat"] == 0.0 and _rows["Loop"]["last_beat"] == 3.0, _rows.get("Loop"))
check("a clip far past everything else is an outlier: named, not the song's end",
      _proj["song_end_beat"] == 12.0 and _proj["last_clip_end_beat"] == 402.0 and len(_proj["outlier_clips"]) == 1 and _proj["outlier_clips"][0]["track"] == "Kit", (_proj["song_end_beat"], _proj["outlier_clips"]))

# ---- 4) pad notes and the role vocabulary ----------------------------------
from ableton.kit_resolver import decode_receiving_note  # noqa: E402
check("a preset's ReceivingNote is decoded, not read raw: 92 is pad 36 (measured on Live 12.4.15b1, BNYX Boot Kit)",
      decode_receiving_note("92") == 36 and decode_receiving_note(77) == 51 and decode_receiving_note("89") == 39, decode_receiving_note("92"))
try:
    decode_receiving_note("200")
    check("an impossible ReceivingNote is refused", False, "no error")
except ValueError:
    check("an impossible ReceivingNote is refused", True)

from ableton.kit_resolver import (  # noqa: E402
    GM_CORE_PADS,
    pad_mapping,
    resolve_kit,
    resolve_pad_notes,
    role_from_label,
)

assumed = resolve_pad_notes(None, None)
check("with no Drum Rack and no preset the pads are the named General MIDI assumption, and NOT writable",
      assumed["source"] == "assumed_general_midi" and assumed["writable"] is False
      and assumed["value"] == list(GM_CORE_PADS) and assumed["reason"], assumed)
live = resolve_pad_notes([46, 36, 38, 42, 999, -1], None)
check("the live Drum Rack wins and out-of-range notes are dropped",
      live["source"] == "live_drum_rack" and live["value"] == [36, 38, 42, 46] and live["writable"] is True, live)

hiphop_pads = [{"note": 80, "role": "kick"}, {"note": 79, "role": "snare"}, {"note": 82, "role": "closed_hat"}, {"note": 78, "role": "perc"}]
mapping = pad_mapping(hiphop_pads)
check("a kit whose pads sit outside the GM range is mapped by role, not written blindly",
      mapping["mode"] == "by_role" and mapping["map"][36] == 80 and mapping["map"][38] == 79 and mapping["map"][42] == 82, mapping)
check("a role the kit has no pad for is named and dropped, never moved onto another pad",
      mapping["unmapped_roles"] == ["open_hat"] and 46 not in mapping["map"], mapping)
check("a kit with GM notes AND matching roles is left alone",
      pad_mapping([{"note": 36, "role": "kick"}, {"note": 38, "role": "snare"},
                   {"note": 42, "role": "closed_hat"}])["mode"] == "direct")
check("the role vocabulary reads a pad's own label when the profile has none",
      (role_from_label("Hihat Open Bonzo"), role_from_label("Kick Golden Era 34"), role_from_label("Xylo Thing"))
      == ("open_hat", "kick", "unknown_pad"))

# the MCP must use that vocabulary, not a second copy
spec = importlib.util.spec_from_file_location("loom_server_contract", ROOT / "mcp_server" / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
check("the MCP resolves pads through the one resolver", server.pad_notes_resolver() is resolve_pad_notes)
check("the MCP maps pads through the one vocabulary", server.kit_pad_mapping(hiphop_pads) == mapping)
check("no second General MIDI table lives in the MCP",
      not hasattr(server, "GM_DRUM_ROLES") and not hasattr(server, "GM_DRUM_PAD_NOTES") and not hasattr(server, "GM_STANDARD_PADS"))
generated = server.handle_midi_generate({"role": "drum", "genre": "Trap", "bars": 2})
check("a drum part with no verified target says which source it assumed and refuses to be written",
      generated.get("pad_source") == "assumed_general_midi" and generated.get("writable_to_live") is False, generated.get("target_evidence"))

# ---- 5) sample references --------------------------------------------------
pack = Path(tempfile.mkdtemp(prefix="loom_reader_pack_"))
try:
    fixture = ROOT / "Sensei" / "ableton" / "fixtures" / "swang_bap_kit.adg"
    (pack / "Drums").mkdir(parents=True)
    shutil.copy2(fixture, pack / "Drums" / "Swang Bap Kit.adg")
    for relative in ("Samples/One Shots/Kick/Kick Golden Era 34.aif", "Samples/One Shots/Snare/Snare Golden Era 38.aif"):
        (pack / relative).parent.mkdir(parents=True, exist_ok=True)
        (pack / relative).write_bytes(b"FORM")
    kit = resolve_kit(str(pack / "Drums" / "Swang Bap Kit.adg"))
    states = {pad["reference_state"] for pad in kit["pads"]}
    check("a sample found through the pack-relative path says so, not merely 'found'",
          states == {"pack_relative"} and all(pad["found_in"] for pad in kit["pads"]), [(p["name"], p["reference_state"]) for p in kit["pads"]])
    check("every pad is accounted for: resolved plus missing equals the preset's pad count",
          len(kit["pads"]) + len(kit["missing"]) == kit["pad_count"] == 16, (len(kit["pads"]), len(kit["missing"])))
    check("a reference that resolves to nothing is reported missing with the path it declared",
          all(item["state"] == "missing" and item.get("declared") for item in kit["missing"]), kit["missing"][:1])
    check("the counts are summarised by state", kit["sample_states"]["pack_relative"] == len(kit["pads"])
          and kit["sample_states"]["missing"] == len(kit["missing"]), kit["sample_states"])
    check("the pads carry the preset as their source", kit["pad_source"] == "preset_xml")
    from_preset = resolve_pad_notes(None, kit)
    check("a preset's pads are writable and mapped by role", from_preset["source"] == "preset_xml"
          and from_preset["writable"] is True and from_preset["mapping"]["mode"] == "by_role", from_preset["source"])

    # an absolute reference that exists resolves as absolute, not pack-relative
    absolute_pack = Path(tempfile.mkdtemp(prefix="loom_reader_abs_"))
    try:
        from ableton.kit_resolver import _resolve_sample

        real = absolute_pack / "kick.wav"
        real.write_bytes(b"RIFF")
        found, state, where = _resolve_sample(pack / "Drums" / "x.adg", str(real), "Samples/kick.wav")
        check("an absolute path that exists is used as such", found == real and state == "absolute" and where == str(absolute_pack))
        gone, state, where = _resolve_sample(pack / "Drums" / "x.adg", "/nowhere/kick.wav", None)
        check("nothing to resolve is 'missing', with no silent substitute", gone is None and state == "missing" and where is None)
    finally:
        shutil.rmtree(absolute_pack, ignore_errors=True)
finally:
    shutil.rmtree(pack, ignore_errors=True)

# ---- 6) genre evidence: roles from samples and Drum Rack pitches, not only track names ----
import gzip as _gz2, xml.etree.ElementTree as _ET2
_ev = server.role_evidence(_ET2.fromstring(_gz2.open(_syn_path).read()))
check("a Drum Rack track named 'Kit' counts as a kick from its sample name AND its MIDI pitch 36; the bass track from its 808 sample and its name",
      _ev["kick"]["count"] == 1 and set(_ev["kick"]["sources"]) == {"sample_name", "drum_rack_midi_notes"} and _ev["bass"]["count"] == 1
      and set(_ev["bass"]["sources"]) >= {"track_name", "sample_name"} and _ev["snare"]["count"] == 0, _ev)

print("%d checks passed:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
if failures:
    print("FAILED:")
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("READER CONTRACT HOLDS: ONE OWNER PER FACT")
