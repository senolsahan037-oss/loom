#!/usr/bin/env python3
"""ArrangementGPSBuilder'in plan cikarimini Live olmadan dogrular.

_Framework Live'in kendi calisma zamaninda gelir; burada stub'lanip modul
normal sekilde import ediliyor. Kanitladigi sey plan->bolum/track donusumu;
kanitlamadigi sey Live'in kendi davranisi.
"""
import collections
import json
import os
import sys
import types

_framework = types.ModuleType("_Framework")
_control_surface = types.ModuleType("_Framework.ControlSurface")


class ControlSurface(object):
    def __init__(self, c_instance=None):
        pass


_control_surface.ControlSurface = ControlSurface
_framework.ControlSurface = _control_surface
sys.modules.setdefault("_Framework", _framework)
sys.modules.setdefault("_Framework.ControlSurface", _control_surface)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ArrangementGPSBuilder as builder  # noqa: E402

ACTION_LIST = os.path.expanduser(
    "~/Desktop/Loom/ArrangementGPS/Builds/"
    "energetic_house_festival_anthem_20260814_004230/ableton_action_list.json"
)

with open(ACTION_LIST) as handle:
    actions = json.load(handle)["actions"]

checks = []


def check(label, condition, detail=""):
    if not condition:
        print("BASARISIZ: %s %s" % (label, detail))
        sys.exit(1)
    checks.append(label)


sections = builder.collect_sections(actions)
check("7 bolum cikariliyor", len(sections) == 7, len(sections))
check(
    "bolum adlari ve bar'lari dogru",
    [(s["name"], s["start_bar"]) for s in sections]
    == [("Intro", 1), ("Verse 1", 9), ("Hook", 25), ("Verse 2", 33), ("Bridge", 49), ("Final Hook", 57), ("Outro", 73)],
    sections,
)
check(
    "bolumler bitisik, bosluk yok",
    all(sections[i]["end_bar"] + 1 == sections[i + 1]["start_bar"] for i in range(len(sections) - 1)),
)
check(
    "bozuk bar araligi olan locator atiliyor",
    builder.collect_sections([{"action": "create_locator", "name": "Bad", "start_bar": "x", "end_bar": 4}]) == [],
)
check(
    "create_locator disindaki aksiyonlar bolum sayilmiyor",
    builder.collect_sections([{"action": "create_midi_track", "start_bar": 1, "end_bar": 4}]) == [],
)

track_actions = [a for a in actions if a["action"] == "create_midi_track"]
plans = [
    builder.collect_track_plan(
        "%s - %s" % (a["group"].upper(), a["name"]), a, a.get("instrument_family")
    )
    for a in track_actions
]
check("17 track plani", len(plans) == 17, len(plans))
drums = [p for p in plans if p["track_name"] == "DRUMS - Kit"][0]
check("DRUMS - Kit bar araligi tasiniyor", drums["clip_start_bar"] == 1 and drums["clip_end_bar"] == 72, drums)
check("DRUMS - Kit mute bolgesi tasiniyor", drums["mute_regions"] == [{"start_bar": 73, "end_bar": 80, "reason": "Inactive in arrangement scene"}], drums["mute_regions"])
check(
    "eksik alanlar None olarak geciyor, uydurulmuyor",
    builder.collect_track_plan("X", {}, None)
    == {"track_name": "X", "instrument_family": None, "clip_start_bar": None, "clip_end_bar": None, "mute_regions": [], "section_activity": {}, "sensei_role": None},
)
check(
    "her plan girdisi eklentinin bekledigi anahtarlari tasiyor",
    all(
        set(p)
        == {"track_name", "instrument_family", "clip_start_bar", "clip_end_bar", "mute_regions", "section_activity", "sensei_role"}
        for p in plans
    ),
)
check("her bolum bir id tasiyor", all(s["id"] for s in sections), sections)
check(
    "bolum id'leri plan tarafiyla ayni",
    [s["id"] for s in sections] == ["intro", "verse_1", "hook", "verse_2", "bridge", "final_hook", "outro"],
    [s["id"] for s in sections],
)
check("17 track'in 17'sinde bolum aktivitesi var", all(p["section_activity"] for p in plans))
check(
    "DRUMS - Kit aktivitesi sahnedeki degerlerle ayni",
    drums["section_activity"] == {"intro": 30, "verse_1": 75, "hook": 95, "verse_2": 75, "bridge": 40, "final_hook": 100, "outro": 20},
    drums["section_activity"],
)
check(
    "her track'in aktivite anahtarlari bolum id'leriyle ortusuyor",
    all(set(p["section_activity"]) == {s["id"] for s in sections} for p in plans),
)

roles = [p["sensei_role"] for p in plans]
check("6 track Sensei rolu tasiyor", sum(1 for r in roles if r) == 6, collections.Counter(roles))
check("11 track'in rolu acikca None", sum(1 for r in roles if r is None) == 11)
check(
    "rol tasiyan her track'in enstrumani da var",
    all(p["instrument_family"] for p in plans if p["sensei_role"]),
)
check(
    "rolu olmayan hicbir track'e enstruman yuklenmeye calisilmiyor",
    all(not p["instrument_family"] for p in plans if p["sensei_role"] is None),
)

print("%d kontrol gecti:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
print("TUM KONTROLLER GECTI")
