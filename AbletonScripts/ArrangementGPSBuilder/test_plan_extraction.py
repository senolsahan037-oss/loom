#!/usr/bin/env python3
"""Verify ArrangementGPSBuilder's plan extraction without Live.

_Framework comes from Live's own runtime; here it is stubbed so the module can
be imported normally. What this proves is the plan-to-section/track conversion;
what it does not prove is Live's own behaviour.
"""
import collections
import json
import os
import sys
import types
from pathlib import Path

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

# The committed fixture plan, resolved from the repository rather than from an
# absolute path: a hardcoded build directory only exists on the machine that
# produced it, and dies in a clean clone and in CI.
ACTION_LIST = str(
    Path(__file__).resolve().parents[2]
    / "ArrangementGPS" / "Builds" / "fixture_project" / "ableton_action_list.json"
)

with open(ACTION_LIST) as handle:
    actions = json.load(handle)["actions"]

checks = []


def check(label, condition, detail=""):
    if not condition:
        print("FAILED: %s %s" % (label, detail))
        sys.exit(1)
    checks.append(label)


sections = builder.collect_sections(actions)
check("7 sections are extracted", len(sections) == 7, len(sections))
check(
    "section names and bars are correct",
    [(s["name"], s["start_bar"]) for s in sections]
    == [("Intro", 1), ("Verse 1", 9), ("Hook", 25), ("Verse 2", 33), ("Bridge", 49), ("Final Hook", 57), ("Outro", 73)],
    sections,
)
check(
    "sections are contiguous, with no gap",
    all(sections[i]["end_bar"] + 1 == sections[i + 1]["start_bar"] for i in range(len(sections) - 1)),
)
check(
    "a locator with a broken bar range is dropped",
    builder.collect_sections([{"action": "create_locator", "name": "Bad", "start_bar": "x", "end_bar": 4}]) == [],
)
check(
    "actions other than create_locator do not count as sections",
    builder.collect_sections([{"action": "create_midi_track", "start_bar": 1, "end_bar": 4}]) == [],
)

track_actions = [a for a in actions if a["action"] == "create_midi_track"]
plans = [
    builder.collect_track_plan(
        "%s - %s" % (a["group"].upper(), a["name"]), a, a.get("instrument_family")
    )
    for a in track_actions
]
check("17 track plans", len(plans) == 17, len(plans))
drums = [p for p in plans if p["track_name"] == "DRUMS - Kit"][0]
check("DRUMS - Kit carries its bar range", drums["clip_start_bar"] == 1 and drums["clip_end_bar"] == 72, drums)
check("DRUMS - Kit carries its mute region", drums["mute_regions"] == [{"start_bar": 73, "end_bar": 80, "reason": "Inactive in arrangement scene"}], drums["mute_regions"])
check(
    "missing fields pass through as None, never invented",
    builder.collect_track_plan("X", {}, None)
    == {"track_name": "X", "instrument_family": None, "clip_start_bar": None, "clip_end_bar": None, "mute_regions": [], "section_activity": {}, "sensei_role": None},
)
check(
    "every plan entry carries the keys the extension expects",
    all(
        set(p)
        == {"track_name", "instrument_family", "clip_start_bar", "clip_end_bar", "mute_regions", "section_activity", "sensei_role"}
        for p in plans
    ),
)
check("every section carries an id", all(s["id"] for s in sections), sections)
check(
    "section ids match the plan side",
    [s["id"] for s in sections] == ["intro", "verse_1", "hook", "verse_2", "bridge", "final_hook", "outro"],
    [s["id"] for s in sections],
)
check("all 17 tracks carry section activity", all(p["section_activity"] for p in plans))
check(
    "DRUMS - Kit activity matches the scene values",
    drums["section_activity"] == {"intro": 30, "verse_1": 75, "hook": 95, "verse_2": 75, "bridge": 40, "final_hook": 100, "outro": 20},
    drums["section_activity"],
)
check(
    "each track's activity keys line up with the section ids",
    all(set(p["section_activity"]) == {s["id"] for s in sections} for p in plans),
)

roles = [p["sensei_role"] for p in plans]
check("6 tracks carry a Sensei role", sum(1 for r in roles if r) == 6, collections.Counter(roles))
check("11 tracks have an explicitly null role", sum(1 for r in roles if r is None) == 11)
check(
    "every track with a role also has an instrument",
    all(p["instrument_family"] for p in plans if p["sensei_role"]),
)
check(
    "no roleless track is asked to load an instrument",
    all(not p["instrument_family"] for p in plans if p["sensei_role"] is None),
)

print("%d checks passed:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
print("ALL CHECKS PASSED")
