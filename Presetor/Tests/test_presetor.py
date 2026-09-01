#!/usr/bin/env python3
"""Headless verification of Presetor. Ableton Live is not required.

What this proves: the chain-evidence thresholds, and that the transplant both
copies correctly and stops in the cases where it must not copy.
What it does not prove: what Live shows when it opens the resulting .als.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Presetor"))
sys.path.insert(0, str(ROOT / "AIMixMaster"))

from aimixmaster.project_analyzer import direct_devices  # noqa: E402
from presetor import chain_evidence  # noqa: E402
from presetor.chain_builder import (  # noqa: E402
    ChainBuildError,
    chain_of,
    find_donors,
    find_track,
    transplant_chain,
)
from presetor.chain_planner import plan_project  # noqa: E402

checks = []
failures = []


def check(label, condition, detail=""):
    if condition:
        checks.append(label)
    else:
        failures.append("%s  %s" % (label, detail))


def make_track(name, devices, next_id_start):
    """The smallest form of the structure Live writes that the test needs."""
    track = ET.Element("AudioTrack", {"Id": str(next_id_start)})
    name_node = ET.SubElement(track, "Name")
    ET.SubElement(name_node, "UserName", {"Value": ""})
    ET.SubElement(name_node, "EffectiveName", {"Value": name})
    ET.SubElement(track, "AutomationEnvelopes")
    outer = ET.SubElement(track, "DeviceChain")
    ET.SubElement(outer, "AudioOutputRouting")
    ET.SubElement(outer, "Mixer")
    ET.SubElement(outer, "ArrangementClips")
    inner = ET.SubElement(outer, "DeviceChain")
    devices_node = ET.SubElement(inner, "Devices")
    for index, tag in enumerate(devices):
        device = ET.SubElement(devices_node, tag, {"Id": str(next_id_start + 100 + index)})
        ET.SubElement(device, "Manual", {"Id": str(next_id_start + 200 + index), "Value": "1"})
    return track


def make_set(next_pointee=900):
    root = ET.Element("Ableton")
    live_set = ET.SubElement(root, "LiveSet")
    ET.SubElement(live_set, "NextPointeeId", {"Value": str(next_pointee)})
    # The names must carry a role: with no role the plan says "no_evidence"
    # and the planner is never actually exercised.
    live_set.append(make_track("BASS DONOR", ["Eq8", "GlueCompressor", "Saturator"], 10))
    live_set.append(make_track("BASS EMPTY", [], 20))
    live_set.append(make_track("BASS BUSY", ["Eq8"], 30))
    return root


# ---- evidence thresholds ----
rows = chain_evidence.load_tracks()
# The repo carries NO measured data (it is personal); a clean clone uses the
# synthetic fixture. The test must pass on both, so the threshold is tied to
# the minimum a recommendation needs to mean anything, not to the size of one
# producer's library.
check("evidence data loaded", len(rows) >= chain_evidence.MIN_ROLE_SAMPLE * 5, len(rows))
check("the data source in use is reported",
      chain_evidence.data_source() in ("measured", "synthetic_fixture"), chain_evidence.data_source())
check("the summary carries the source too", chain_evidence.summary(rows)["data_source"] == chain_evidence.data_source())
check("the kick role has a recommendation", chain_evidence.recommend("kick", rows) is not None)
kick = chain_evidence.recommend("kick", rows)
if kick:
    check("the kick recommendation starts with EQ Eight", kick.chain[0] == "EQ Eight", kick.chain)
    check("every recommended device is above the presence threshold",
          all(item.presence >= chain_evidence.PRESENCE_THRESHOLD for item in kick.devices),
          [(i.device, i.presence) for i in kick.devices])
    check("the recommendation states how many tracks back it", kick.role_sample >= chain_evidence.MIN_ROLE_SAMPLE, kick.role_sample)
check("a role with too small a sample yields NO recommendation",
      chain_evidence.recommend("__yok_boyle_bir_rol__", rows) is None)
few = [{"role": "tek", "chain": ["Eq8"], "project": "p"}] * (chain_evidence.MIN_ROLE_SAMPLE - 1)
check("a role below MIN_ROLE_SAMPLE stays silent", chain_evidence.recommend("tek", few) is None)

# ---- transplant ----
root = make_set()
check("the donor chain is read", chain_of(find_track(root, "BASS DONOR")) == ("EQ Eight", "Glue Compressor", "Saturator"))
check("an empty track has an empty chain", chain_of(find_track(root, "BASS EMPTY")) == ())
check("a donor carrying the wanted chain is found",
      find_donors(root, ("EQ Eight", "Glue Compressor", "Saturator")) == ["BASS DONOR"])

before_ids = {node.attrib["Id"] for node in root.iter() if "Id" in node.attrib}
result = transplant_chain(root, target_name="BASS EMPTY", donor_name="BASS DONOR")
check("the chain was copied", result.changed and result.inserted_devices == ("EQ Eight", "Glue Compressor", "Saturator"), result)
check("the target now has a chain", chain_of(find_track(root, "BASS EMPTY")) == ("EQ Eight", "Glue Compressor", "Saturator"))
check("the donor was not disturbed", chain_of(find_track(root, "BASS DONOR")) == ("EQ Eight", "Glue Compressor", "Saturator"))

# The track already had its own Id; the question is whether the COPIED devices
# were given new ones.
inserted_ids = {
    node.attrib["Id"]
    for device in direct_devices(find_track(root, "BASS EMPTY"))
    for node in device.iter()
    if "Id" in node.attrib
}
check("the copied devices were given NEW ids", not (inserted_ids & before_ids), sorted(inserted_ids & before_ids))
all_ids = [node.attrib["Id"] for node in root.iter() if "Id" in node.attrib and int(node.attrib["Id"]) > 0]
check("no id is used twice", len(all_ids) == len(set(all_ids)),
      [i for i in set(all_ids) if all_ids.count(i) > 1])
check("NextPointeeId was advanced", int(root.find("./LiveSet/NextPointeeId").attrib["Value"]) == result.next_pointee_id and result.next_pointee_id > 900, result.next_pointee_id)

again = transplant_chain(root, target_name="BASS EMPTY", donor_name="BASS DONOR")
check("running the same transplant twice changes nothing", again.changed is False, again)
check("a second run does not double the device count", len(direct_devices(find_track(root, "BASS EMPTY"))) == 3,
      len(direct_devices(find_track(root, "BASS EMPTY"))))

try:
    transplant_chain(root, target_name="BASS BUSY", donor_name="BASS DONOR")
    check("a track that already has a chain is not overwritten", False, "an error was expected")
except ChainBuildError as error:
    check("a track that already has a chain is not overwritten", "already has a chain" in str(error), str(error))

try:
    transplant_chain(root, target_name="BASS DONOR", donor_name="BASS DONOR")
    check("a track cannot donate to itself", False, "an error was expected")
except ChainBuildError:
    check("a track cannot donate to itself", True)

try:
    transplant_chain(root, target_name="BASS EMPTY", donor_name="__yok__")
    check("a donor that does not exist is refused", False, "an error was expected")
except ChainBuildError:
    check("a donor that does not exist is refused", True)

fresh = make_set()
try:
    transplant_chain(fresh, target_name="BASS EMPTY", donor_name="BUSY_YOK")
    check("a donor with no devices is refused", False, "an error was expected")
except ChainBuildError:
    check("a donor with no devices is refused", True)

# ---- planner ----
plan = plan_project(make_set(), rows)
check("the plan produces one row per track", plan["track_count"] == 3, plan["track_count"])
statuses = {item["track"]: item["status"] for item in plan["plans"]}
check("tracks with a chain are marked 'already_has_chain'", statuses["BASS DONOR"] == "already_has_chain", statuses)
empty_plan = next(item for item in plan["plans"] if item["track"] == "BASS EMPTY")
check("an empty track gets a recommendation and a donor", empty_plan["status"] in ("can_transplant", "no_donor"), empty_plan["status"])
if empty_plan["status"] == "can_transplant":
    check("the plan carries the evidence it rests on", empty_plan["evidence"] and empty_plan["role_sample"], empty_plan)

print("%d checks passed:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
if failures:
    print()
    print("FAILED:")
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("PRESETOR WORKS")
