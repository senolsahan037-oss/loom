#!/usr/bin/env python3
"""Presetor'un headless dogrulanmasi. Ableton Live gerekmiyor.

Kanitladigi sey: zincir kanitinin esikleri, ve transplant'in hem dogru
kopyaladigi hem de kopyalamamasi gereken durumlarda durdugu.
Kanitlamadigi sey: Live'in bu .als'i acinca ne gosterdigi.
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
    """Live'in yazdigi yapinin, test icin gereken en kucuk hali."""
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
    # Adlar rol tasimali: rol cikarilamazsa plan "no_evidence" der ve
    # planlayici hic sinanmamis olur.
    live_set.append(make_track("BASS DONOR", ["Eq8", "GlueCompressor", "Saturator"], 10))
    live_set.append(make_track("BASS EMPTY", [], 20))
    live_set.append(make_track("BASS BUSY", ["Eq8"], 30))
    return root


# ---- kanit esikleri ----
rows = chain_evidence.load_tracks()
# Depoda olculmus veri YOKTUR (kisiseldir); temiz bir klonda sentetik fixture
# kullanilir. Test ikisinde de gecmeli, o yuzden esik kullanicinin kutuphane
# boyutuna degil, bir onerinin anlamli olmasi icin gereken minimuma bagli.
check("kanit verisi yuklendi", len(rows) >= chain_evidence.MIN_ROLE_SAMPLE * 5, len(rows))
check("hangi kaynagin kullanildigi bildiriliyor",
      chain_evidence.data_source() in ("measured", "synthetic_fixture"), chain_evidence.data_source())
check("ozet de kaynagi tasiyor", chain_evidence.summary(rows)["data_source"] == chain_evidence.data_source())
check("kick rolu icin oneri var", chain_evidence.recommend("kick", rows) is not None)
kick = chain_evidence.recommend("kick", rows)
if kick:
    check("kick onerisi EQ Eight ile basliyor", kick.chain[0] == "EQ Eight", kick.chain)
    check("her onerilen cihazin varlik orani esigin ustunde",
          all(item.presence >= chain_evidence.PRESENCE_THRESHOLD for item in kick.devices),
          [(i.device, i.presence) for i in kick.devices])
    check("oneri kac track'e dayandigini soyluyor", kick.role_sample >= chain_evidence.MIN_ROLE_SAMPLE, kick.role_sample)
check("orneklemi kucuk rol icin oneri URETILMEZ",
      chain_evidence.recommend("__yok_boyle_bir_rol__", rows) is None)
few = [{"role": "tek", "chain": ["Eq8"], "project": "p"}] * (chain_evidence.MIN_ROLE_SAMPLE - 1)
check("MIN_ROLE_SAMPLE altinda kalan rol sessiz kalir", chain_evidence.recommend("tek", few) is None)

# ---- transplant ----
root = make_set()
check("donor zinciri okunuyor", chain_of(find_track(root, "BASS DONOR")) == ("EQ Eight", "Glue Compressor", "Saturator"))
check("bos track'in zinciri bos", chain_of(find_track(root, "BASS EMPTY")) == ())
check("istenen zincire sahip donor bulunuyor",
      find_donors(root, ("EQ Eight", "Glue Compressor", "Saturator")) == ["BASS DONOR"])

before_ids = {node.attrib["Id"] for node in root.iter() if "Id" in node.attrib}
result = transplant_chain(root, target_name="BASS EMPTY", donor_name="BASS DONOR")
check("zincir kopyalandi", result.changed and result.inserted_devices == ("EQ Eight", "Glue Compressor", "Saturator"), result)
check("hedefte artik zincir var", chain_of(find_track(root, "BASS EMPTY")) == ("EQ Eight", "Glue Compressor", "Saturator"))
check("donor bozulmadi", chain_of(find_track(root, "BASS DONOR")) == ("EQ Eight", "Glue Compressor", "Saturator"))

# Track'in kendi Id'si zaten vardi; sorulan sey KOPYALANAN cihazlarin
# yeni id alip almadigi.
inserted_ids = {
    node.attrib["Id"]
    for device in direct_devices(find_track(root, "BASS EMPTY"))
    for node in device.iter()
    if "Id" in node.attrib
}
check("kopyalanan cihazlara YENI id verildi", not (inserted_ids & before_ids), sorted(inserted_ids & before_ids))
all_ids = [node.attrib["Id"] for node in root.iter() if "Id" in node.attrib and int(node.attrib["Id"]) > 0]
check("hicbir id iki kez kullanilmiyor", len(all_ids) == len(set(all_ids)),
      [i for i in set(all_ids) if all_ids.count(i) > 1])
check("NextPointeeId ilerletildi", int(root.find("./LiveSet/NextPointeeId").attrib["Value"]) == result.next_pointee_id and result.next_pointee_id > 900, result.next_pointee_id)

again = transplant_chain(root, target_name="BASS EMPTY", donor_name="BASS DONOR")
check("ayni transplant ikinci kez calistirilinca degisiklik yapmaz", again.changed is False, again)
check("ikinci calistirma cihaz sayisini iki katina cikarmaz", len(direct_devices(find_track(root, "BASS EMPTY"))) == 3,
      len(direct_devices(find_track(root, "BASS EMPTY"))))

try:
    transplant_chain(root, target_name="BASS BUSY", donor_name="BASS DONOR")
    check("dolu track'in uzerine yazilmaz", False, "hata bekleniyordu")
except ChainBuildError as error:
    check("dolu track'in uzerine yazilmaz", "already has a chain" in str(error), str(error))

try:
    transplant_chain(root, target_name="BASS DONOR", donor_name="BASS DONOR")
    check("track kendi kendine donor olamaz", False, "hata bekleniyordu")
except ChainBuildError:
    check("track kendi kendine donor olamaz", True)

try:
    transplant_chain(root, target_name="BASS EMPTY", donor_name="__yok__")
    check("olmayan donor reddedilir", False, "hata bekleniyordu")
except ChainBuildError:
    check("olmayan donor reddedilir", True)

fresh = make_set()
try:
    transplant_chain(fresh, target_name="BASS EMPTY", donor_name="BUSY_YOK")
    check("cihazsiz donor reddedilir", False, "hata bekleniyordu")
except ChainBuildError:
    check("cihazsiz donor reddedilir", True)

# ---- planlayici ----
plan = plan_project(make_set(), rows)
check("plan her track icin bir satir uretir", plan["track_count"] == 3, plan["track_count"])
statuses = {item["track"]: item["status"] for item in plan["plans"]}
check("dolu track'ler 'already_has_chain' olarak isaretlenir", statuses["BASS DONOR"] == "already_has_chain", statuses)
empty_plan = next(item for item in plan["plans"] if item["track"] == "BASS EMPTY")
check("bos track icin oneri ve donor uretilir", empty_plan["status"] in ("can_transplant", "no_donor"), empty_plan["status"])
if empty_plan["status"] == "can_transplant":
    check("plan hangi kanita dayandigini tasir", empty_plan["evidence"] and empty_plan["role_sample"], empty_plan)

print("%d kontrol gecti:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
if failures:
    print()
    print("BASARISIZ:")
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("PRESETOR CALISIYOR")
