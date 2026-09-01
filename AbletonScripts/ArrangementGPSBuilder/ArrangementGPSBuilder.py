import glob
import json
import os
import time
from pathlib import Path

from _Framework.ControlSurface import ControlSurface

ACTION_LIST_GLOBS = [
    os.path.expanduser("~/Desktop/ArrangementGPS/Builds/*/ableton_action_list.json"),
    os.path.expanduser("~/Desktop/Loom/ArrangementGPS/Builds/*/ableton_action_list.json"),
]

# The Sensei extension's Node runtime is permission-sandboxed and can only
# read/write inside its own storage directory, not ~/Documents. Write the
# pointer file there instead so sensei-midi-writer can read it.
BRIDGE_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Ableton"
    / "Extensions Data"
    / "ai-producer.sensei-midi-writer"
)
LAST_BUILD_PATH = BRIDGE_DIR / "arrangementgps_last_build.json"

# ControlSurfaceComponent has _register_timer_callback; plain ControlSurface
# does not. Polling is done with ControlSurface.schedule_message rescheduling
# itself each time it runs -- the same API the previous one-shot version used.
POLL_INTERVAL_TICKS = 200


def _parse_key(key_string):
    """"D Minor" -> ("D", "Minor"). Anything else -> (None, None)."""
    if not key_string:
        return None, None
    parts = str(key_string).strip().split()
    if len(parts) != 2:
        return None, None
    root, mode = parts[0].replace("♯", "#").replace("♭", "b"), parts[1]
    if mode not in ("Major", "Minor"):
        return None, None
    return root, mode


_PITCH_CLASS = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
    "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}


def collect_sections(actions):
    """Section list, taken from the create_locator actions.

    Kept at module level because it has to be testable without Live
    (test_plan_extraction.py).
    """
    return [
        {
            # section_activity is keyed by section id, so the id has to
            # travel with the section or the two cannot be joined again.
            "id": a.get("id"),
            "name": a.get("name", "Section"),
            "start_bar": a.get("start_bar"),
            "end_bar": a.get("end_bar"),
        }
        for a in actions
        if a.get("action") == "create_locator"
        and isinstance(a.get("start_bar"), int)
        and isinstance(a.get("end_bar"), int)
    ]


def collect_track_plan(track_name, action, family):
    """Bir create_midi_track aksiyonundan plan girdisi.

    The arrangement builder needs to know which sections this lane is active
    in. The plan already carried that; it simply never reached the extension.
    """
    return {
        "track_name": track_name,
        "instrument_family": family,
        "clip_start_bar": action.get("clip_start_bar"),
        "clip_end_bar": action.get("clip_end_bar"),
        "mute_regions": action.get("mute_regions", []),
        # Per-section 0-100 intensity. Presence is already decided by
        # mute_regions; this is the part that used to be discarded.
        "section_activity": action.get("section_activity", {}),
        # drum/bass/chord, or None for a lane Sensei has no role for.
        "sensei_role": action.get("sensei_role"),
    }


def create_instance(c_instance):
    return ArrangementGPSBuilder(c_instance)


class ArrangementGPSBuilder(ControlSurface):
    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self._last_built_path = None
        self._last_built_mtime = None
        self.log_message("ArrangementGPSBuilder loaded, watching for new plans")
        self.schedule_message(POLL_INTERVAL_TICKS, self._poll)

    def _poll(self):
        self._check_for_new_plan()
        self.schedule_message(POLL_INTERVAL_TICKS, self._poll)

    def _check_for_new_plan(self):
        try:
            candidates = []
            for pattern in ACTION_LIST_GLOBS:
                candidates.extend(glob.glob(pattern))
            if not candidates:
                return

            latest_path = max(candidates, key=lambda p: os.path.getmtime(p))
            latest_mtime = os.path.getmtime(latest_path)

            if latest_path == self._last_built_path and latest_mtime == self._last_built_mtime:
                return

            self._build(latest_path)
            self._last_built_path = latest_path
            self._last_built_mtime = latest_mtime
        except Exception as e:
            self.log_message("ArrangementGPSBuilder: plan check failed: %s" % str(e))

    def _build(self, action_list_path):
        try:
            self.log_message("ArrangementGPSBuilder: building from %s" % action_list_path)

            with open(action_list_path, "r") as f:
                data = json.load(f)

            song = self.song()
            song.tempo = float(data.get("project", {}).get("bpm", 95))

            target_root, target_mode = _parse_key(data.get("project", {}).get("key"))
            if target_root and target_mode:
                pitch_class = _PITCH_CLASS.get(target_root)
                if pitch_class is not None:
                    try:
                        # Live's own Song Key display (Live 11+) is separate
                        # from the target_root/target_mode we hand to Sensei
                        # for MIDI transposition -- without this the project
                        # always opens showing Live's default C Major, no
                        # matter what mood the prompt actually derived.
                        song.root_note = pitch_class
                        song.scale_name = target_mode
                    except Exception as e:
                        self.log_message("ArrangementGPSBuilder: could not set song key: %s" % str(e))

            actions = data.get("actions", [])
            track_actions = [a for a in actions if a.get("action") == "create_midi_track"]

            # create_locator actions were produced by ArrangementGPS all
            # along and silently dropped here. This script still does not
            # place them -- Live's Python API can only toggle a cue at the
            # playhead -- so they are handed to the SDK extension, which has
            # a real Song.createCuePoint(time).
            sections = collect_sections(actions)

            built_tracks = []

            # Only ever reuse existing MIDI tracks. Reusing Audio (or any
            # other) track by raw song.tracks[i] index silently corrupts it:
            # renaming works regardless of track type, but an instrument can
            # never be loaded onto an Audio track, so a pre-existing Audio
            # track (e.g. Live's own default new-project tracks) ends up
            # wearing a MIDI track's name with nothing loaded on it.
            midi_tracks = [t for t in song.tracks if getattr(t, "has_midi_input", False)]

            for i, action in enumerate(track_actions):
                group = action.get("group", "")
                name = action.get("name", "Track")
                track_name = "%s - %s" % (group.upper(), name)

                if i < len(midi_tracks):
                    track = midi_tracks[i]
                else:
                    song.create_midi_track(len(song.tracks))
                    track = song.tracks[-1]
                    midi_tracks.append(track)

                track.name = track_name

                family = action.get("instrument_family")
                if family:
                    song.view.selected_track = track
                    self._load_browser_item(family)

                built_tracks.append(collect_track_plan(track_name, action, family))

            self._write_last_build(action_list_path, data, built_tracks, target_root, target_mode, sections)

            self.log_message(
                "ArrangementGPSBuilder: complete, %d tracks, %d sections"
                % (len(built_tracks), len(sections))
            )

        except Exception as e:
            self.log_message("ArrangementGPSBuilder ERROR: %s" % str(e))

    def _write_last_build(self, action_list_path, data, built_tracks, target_root, target_mode, sections=None):
        if not BRIDGE_DIR.exists():
            os.makedirs(str(BRIDGE_DIR))

        project = data.get("project", {})

        payload = {
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "project_name": project.get("name", ""),
            "action_list_path": action_list_path,
            "target_root": target_root,
            "target_mode": target_mode,
            "total_bars": project.get("total_bars"),
            "sections": sections or [],
            "tracks": built_tracks,
        }

        with open(str(LAST_BUILD_PATH), "w") as f:
            json.dump(payload, f, indent=2)

    def _load_browser_item(self, term):
        browser = self.application().browser
        item = self._find_browser_item(browser, term)

        if item:
            try:
                browser.load_item(item)
                self.log_message("Loaded instrument family: %s" % item.name)
            except Exception as e:
                self.log_message("Load failed %s: %s" % (item.name, str(e)))
        else:
            self.log_message("Instrument family not found: %s" % term)

    def _find_browser_item(self, browser, term):
        roots = []

        for attr in ["drums", "instruments", "sounds", "packs", "user_library"]:
            try:
                root = getattr(browser, attr)
                if root:
                    roots.append(root)
            except:
                pass

        term_lower = term.lower()

        for root in roots:
            found = self._search_item(root, term_lower, 0)
            if found:
                return found

        return None

    def _search_item(self, item, term_lower, depth):
        if depth > 6:
            return None

        try:
            name = item.name.lower()
            if term_lower in name and item.is_loadable:
                return item
        except:
            pass

        try:
            for child in item.children:
                found = self._search_item(child, term_lower, depth + 1)
                if found:
                    return found
        except:
            pass

        return None
