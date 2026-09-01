import json
import os
from _Framework.ControlSurface import ControlSurface

ACTION_LIST = os.path.expanduser(
    "~/Desktop/ArrangementGPS/Builds/Local_Engine_Test/ableton_action_list.json"
)

class ArrangementGPSBuilder(ControlSurface):
    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self.schedule_message(80, self._build)

    def _build(self):
        try:
            self.log_message("ArrangementGPSBuilder: starting V1_INSTRUMENT_FAMILY")

            with open(ACTION_LIST, "r") as f:
                data = json.load(f)

            song = self.song()
            song.tempo = float(data.get("project", {}).get("bpm", 95))

            actions = data.get("actions", [])
            track_actions = [a for a in actions if a.get("action") == "create_midi_track"]

            for i, action in enumerate(track_actions):
                name = "%s - %s" % (action.get("group", "").upper(), action.get("name", "Track"))

                if i < len(song.tracks):
                    track = song.tracks[i]
                else:
                    song.create_midi_track(len(song.tracks))
                    track = song.tracks[-1]

                track.name = name

                family = action.get("instrument_family")
                if family:
                    song.view.selected_track = track
                    self._load_browser_item(family)

            self.log_message("ArrangementGPSBuilder: complete V1_INSTRUMENT_FAMILY")

        except Exception as e:
            self.log_message("ArrangementGPSBuilder ERROR: %s" % str(e))

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
