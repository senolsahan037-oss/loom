import json
import os
from _Framework.ControlSurface import ControlSurface

ACTION_LIST = os.path.expanduser(
    "~/Desktop/ArrangementGPS/Builds/Local_Engine_Test/ableton_action_list.json"
)

NOTES_JSON = os.path.expanduser(
    "~/Desktop/ArrangementGPS/engine/output/agent_outputs/drums/full_drums.notes.json"
)

class ArrangementGPSBuilder(ControlSurface):
    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self.schedule_message(100, self._build)

    def _build(self):
        try:
            self.log_message("ArrangementGPSBuilder: starting V1_MIDI_TEST")

            with open(ACTION_LIST, "r") as f:
                data = json.load(f)

            song = self.song()
            song.tempo = float(data.get("project", {}).get("bpm", 95))

            actions = data.get("actions", [])
            track_actions = [a for a in actions if a.get("action") == "create_midi_track"]

            for i, action in enumerate(track_actions):
                name = "%s - %s" % (
                    action.get("group", "").upper(),
                    action.get("name", "Track")
                )

                if i < len(song.tracks):
                    track = song.tracks[i]
                else:
                    song.create_midi_track(len(song.tracks))
                    track = song.tracks[-1]

                track.name = name

            self._load_sensei_clip(song)

            self.log_message("ArrangementGPSBuilder: complete V1_MIDI_TEST")

        except Exception as e:
            self.log_message("ArrangementGPSBuilder ERROR: %s" % str(e))

    def _load_sensei_clip(self, song):
        if not os.path.exists(NOTES_JSON):
            self.log_message("Sensei notes JSON not found")
            return

        with open(NOTES_JSON, "r") as f:
            data = json.load(f)

        target_name = data.get("target_track", "DRUMS - Kick")
        target = None

        for track in song.tracks:
            if track.name == target_name:
                target = track
                break

        if target is None:
            self.log_message("Target track not found: %s" % target_name)
            return

        slot = target.clip_slots[0]

        if not slot.has_clip:
            slot.create_clip(float(data.get("length_beats", 32)))

        clip = slot.clip
        clip.name = data.get("clip_name", "Sensei Full Drums")

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

        self.log_message("Sensei MIDI notes written: %s" % len(notes))
