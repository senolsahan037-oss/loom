import json
import os
from _Framework.ControlSurface import ControlSurface

NOTES_JSON = os.path.expanduser(
    "~/Desktop/ArrangementGPS/engine/output/agent_outputs/drums/full_drums.notes.json"
)

TARGET_TRACK = "DRUMS - Kick"
KIT_NAME = "Boom Bap Kit"

class MidiImportTest(ControlSurface):
    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self.schedule_message(100, self._run)

    def _run(self):
        try:
            self.log_message("MidiImportTest: KIT + MIDI TEST START")

            song = self.song()

            target = None
            for track in song.tracks:
                if track.name == TARGET_TRACK:
                    target = track
                    break

            if target is None:
                song.create_midi_track(0)
                target = song.tracks[0]
                target.name = TARGET_TRACK

            song.view.selected_track = target

            self._load_browser_item(KIT_NAME)

            song.view.selected_track = target
            song.view.highlighted_clip_slot = target.clip_slots[0]

            self._write_notes_to_slot(target.clip_slots[0])

            self.log_message("MidiImportTest: KIT + MIDI TEST COMPLETE")

        except Exception as e:
            self.log_message("MidiImportTest ERROR: %s" % str(e))

    def _write_notes_to_slot(self, slot):
        if not os.path.exists(NOTES_JSON):
            self.log_message("MidiImportTest: notes json missing")
            return

        with open(NOTES_JSON, "r") as f:
            data = json.load(f)

        if not slot.has_clip:
            slot.create_clip(float(data.get("length_beats", 32)))

        clip = slot.clip
        clip.name = "Sensei Full Drums"

        notes = []
        for n in data.get("notes", []):
            notes.append((
                int(n["pitch"]),
                float(n["start"]),
                float(n["duration"]),
                int(n["velocity"]),
                bool(n.get("mute", False))
            ))

        clip.select_all_notes()
        clip.replace_selected_notes(tuple(notes))

        self.log_message("MidiImportTest: notes written to %s: %s" % (TARGET_TRACK, len(notes)))

    def _load_browser_item(self, term):
        item = self._find_browser_item(term)

        if item:
            try:
                self.application().browser.load_item(item)
                self.log_message("MidiImportTest: loaded item %s" % item.name)
            except Exception as e:
                self.log_message("MidiImportTest load failed %s: %s" % (item.name, str(e)))
        else:
            self.log_message("MidiImportTest: item not found %s" % term)

    def _find_browser_item(self, term):
        browser = self.application().browser
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
        if depth > 8:
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
