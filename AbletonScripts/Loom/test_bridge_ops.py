#!/usr/bin/env python3
"""Verification of the bridge command layer -- without opening Ableton.

Because bridge_ops takes the song object from outside, the fake song here
mimics the surface of Live's Object Model that is actually used. What this
proves is the command logic; what it does not prove is that Live's real LOM
behaves the same way.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_ops  # noqa: E402

checks = []
failures = []


def check(label, condition, detail=""):
    if condition:
        checks.append(label)
    else:
        failures.append("%s  %s" % (label, detail))


class FakeParameter(object):
    def __init__(self, name, value, minimum, maximum):
        self.name = name
        self.value = value
        self.min = minimum
        self.max = maximum

    def str_for_value(self, value):
        return "%.2f" % value


class FakeDevice(object):
    def __init__(self, name, parameters):
        self.name = name
        self.class_name = name.replace(" ", "")
        self.parameters = parameters


class FakeMixer(object):
    def __init__(self):
        self.volume = FakeParameter("Volume", 0.85, 0.0, 1.0)
        self.panning = FakeParameter("Pan", 0.0, -1.0, 1.0)


class FakeArrangementClip(object):
    """What Track.create_midi_clip hands back: a clip already in the Arrangement."""
    def __init__(self, start_time, length):
        self.name = ""
        self.start_time = float(start_time)
        self.end_time = float(start_time) + float(length)
        self.notes = []

    def add_new_notes(self, specs):
        # Live's add_new_notes takes MidiNoteSpecification objects; handing it a
        # dict is a Boost.Python ArgumentError. The fake refuses dicts the same
        # way, so a writer that builds dicts fails here instead of in Live.
        for spec in specs:
            if isinstance(spec, dict):
                raise TypeError("add_new_notes: expected MidiNoteSpecification, got dict")
            self.notes.append(spec)

    def get_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        return [n for n in self.notes
                if from_pitch <= n.pitch < from_pitch + pitch_span
                and from_time <= n.start_time < from_time + time_span]


class FakeSessionClip(object):
    """A Session clip that speaks both note APIs and counts which one was used.

    Live 11's add_new_notes/remove_notes_extended is what a script must call;
    the old set_notes/remove_notes pair still exists but trips a modal warning
    in Live 12. The counters let a test prove the surface took the new road.
    """
    def __init__(self, length):
        self.name = ""
        self.loop_start = 0.0
        self.loop_end = float(length)
        self.end_marker = float(length)
        self.notes = []
        self.calls = {"add_new_notes": 0, "remove_notes_extended": 0, "set_notes": 0, "remove_notes": 0}

    def add_new_notes(self, specs):
        self.calls["add_new_notes"] += 1
        for spec in specs:
            if isinstance(spec, dict):
                raise TypeError("add_new_notes: expected MidiNoteSpecification, got dict")
            self.notes.append(spec)

    def remove_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        self.calls["remove_notes_extended"] += 1
        self.notes = [n for n in self.notes if not (from_time <= n.start_time < from_time + time_span)]

    def set_notes(self, tuples):
        self.calls["set_notes"] += 1
        self.notes = [{"pitch": t[0], "start_time": t[1], "duration": t[2], "velocity": t[3]} for t in tuples]

    def remove_notes(self, from_time, from_pitch, time_span, pitch_span):
        self.calls["remove_notes"] += 1
        self.notes = []


class FakeClipSlot(object):
    def __init__(self):
        self.clip = None

    @property
    def has_clip(self):
        return self.clip is not None

    def create_clip(self, length):
        self.clip = FakeSessionClip(length)


class FakeTrack(object):
    def __init__(self, name, midi=True, devices=None):
        self.name = name
        self.has_midi_input = midi
        self.mute = False
        self.solo = False
        self.arm = False
        self.mixer_device = FakeMixer()
        self.devices = devices or []
        self.arrangement_clips = []
        self.clip_slots = [FakeClipSlot() for _ in range(8)]

    def create_midi_clip(self, start_time, length):
        clip = FakeArrangementClip(start_time, length)
        self.arrangement_clips.append(clip)
        return clip

    def delete_clip(self, clip):
        self.arrangement_clips.remove(clip)


class FakeCue(object):
    def __init__(self, name, time_):
        self.name = name
        self.time = time_


class FakeReturnTrack(FakeTrack):
    def __init__(self, name):
        FakeTrack.__init__(self, name, midi=False)
        self.can_be_armed = False

    @property
    def arm(self):
        raise RuntimeError("Main and Return Tracks have no 'Arm' state!")

    @arm.setter
    def arm(self, value):
        # FakeTrack.__init__ assigns arm; Live's Return track would refuse that
        # too, but the point of this fake is the read, so the write is ignored.
        pass


class FakeView(object):
    def __init__(self, selected):
        self.selected_track = selected
        self.highlighted_clip_slot = None


class FakeBrowserItem(object):
    def __init__(self, name, loadable=False, children=None):
        self.name = name
        self.is_loadable = loadable
        self.children = children or []


class FakeBrowser(object):
    """A three-level browser: Instruments > Bass > 'Basic Analog Bass'. Loading
    an item drops a device of that name onto the selected track, as Live does."""
    def __init__(self, song):
        self._song = song
        self.loaded = []
        self.drums = FakeBrowserItem("Drums", children=[FakeBrowserItem("Drum Rack", loadable=True)])
        self.instruments = FakeBrowserItem("Instruments", children=[
            FakeBrowserItem("Bass", children=[FakeBrowserItem("Basic Analog Bass", loadable=True)])])
        self.sounds = None
        self.packs = FakeBrowserItem("Packs")
        self.user_library = FakeBrowserItem("User Library")

    def load_item(self, item):
        self.loaded.append(item.name)
        self._song.view.selected_track.devices.append(FakeDevice(item.name, []))


class FakeSong(object):
    def __init__(self):
        eq = FakeDevice("EQ Eight", [FakeParameter("Frequency A", 100.0, 20.0, 20000.0),
                                     FakeParameter("Gain A", 0.0, -15.0, 15.0)])
        self.tracks = [FakeTrack("KICK", devices=[eq]), FakeTrack("BASS"), FakeTrack("KICK COPY")]
        self.tracks[2].name = "KICK"  # a second track with the same name: ambiguity test
        self.tempo = 120.0
        self.is_playing = False
        self.current_song_time = 0.0
        self.signature_numerator = 4
        self.signature_denominator = 4
        self.cue_points = []
        self.song_length = 512.0  # beats; Live's own read-only arrangement length
        # A Return track: Live raises on .arm rather than returning anything.
        self.return_tracks = [FakeReturnTrack("A-Reverb")]
        self.view = FakeView(self.tracks[1])

    def start_playing(self):
        self.is_playing = True

    def stop_playing(self):
        self.is_playing = False

    def continue_playing(self):
        self.is_playing = True

    def create_midi_track(self, index):
        # Like Live: the new track appears at `index`, unnamed-ish, with MIDI input.
        self.tracks.insert(index, FakeTrack("%d-MIDI" % (len(self.tracks) + 1)))

    def set_or_delete_cue(self):
        for cue in self.cue_points:
            if cue.time == self.current_song_time:
                self.cue_points.remove(cue)
                return
        self.cue_points.append(FakeCue("", self.current_song_time))


def unique_song():
    song = FakeSong()
    song.tracks[2].name = "SNARE"
    return song


def run():
    # --- durum okuma ---
    song = unique_song()
    state = bridge_ops.capture_state(song)
    check("state carries the schema version", state["schema_version"] == bridge_ops.SCHEMA_VERSION)
    check("tempo is read", state["tempo"] == 120.0, state["tempo"])
    check("the track count is right", state["track_count"] == 3, state["track_count"])
    check("the selected track is reported", state["selected_track"] == "BASS", state["selected_track"])
    check("mixer values arrive with their min/max",
          state["tracks"][0]["volume"]["max"] == 1.0 and state["tracks"][0]["panning"]["min"] == -1.0,
          state["tracks"][0].get("volume"))
    check("the parameter's display value is given too", state["tracks"][0]["volume"]["display_value"] == "0.85")
    check("devices are listed", state["tracks"][0]["devices"][0]["name"] == "EQ Eight", state["tracks"][0]["devices"])
    check("devices can be left out on request", "devices" not in bridge_ops.capture_state(song, include_devices=False)["tracks"][0])

    # --- tempo ---
    result = bridge_ops.apply_operation(unique_song(), {"op": "set_tempo", "bpm": 126})
    check("tempo is written and reported before/after", result["before"] == 120.0 and result["after"] == 126.0, result)
    try:
        bridge_ops.apply_operation(unique_song(), {"op": "set_tempo", "bpm": 5})
        check("a tempo outside the range is refused", False, "an error was expected")
    except bridge_ops.BridgeError as error:
        check("a tempo outside the range is refused", "outside Live's range" in str(error), str(error))

    # --- mikser ---
    song = unique_song()
    result = bridge_ops.apply_operation(song, {"op": "set_mixer", "track": "KICK", "volume": 0.5, "mute": True})
    check("the mixer value is written", song.tracks[0].mixer_device.volume.value == 0.5, result)
    check("mute is written", song.tracks[0].mute is True, result)
    check("changes are reported as before/after",
          result["changes"]["volume"]["before"] == 0.85 and result["changes"]["volume"]["after"] == 0.5, result)
    try:
        bridge_ops.apply_operation(unique_song(), {"op": "set_mixer", "track": "KICK", "volume": 5})
        check("a mixer value outside the range is refused", False, "an error was expected")
    except bridge_ops.BridgeError as error:
        check("a mixer value outside the range is refused", "outside" in str(error), str(error))
    try:
        bridge_ops.apply_operation(unique_song(), {"op": "set_mixer", "track": "KICK"})
        check("an empty mixer command is refused", False, "an error was expected")
    except bridge_ops.BridgeError:
        check("an empty mixer command is refused", True)

    ambiguous = FakeSong()  # iki track de "KICK"
    try:
        bridge_ops.apply_operation(ambiguous, {"op": "set_mixer", "track": "KICK", "mute": True})
        check("two tracks with the same name count as ambiguous", False, "an error was expected")
    except bridge_ops.BridgeError as error:
        check("two tracks with the same name count as ambiguous", "found 2" in str(error), str(error))

    # --- cihaz parametresi ---
    song = unique_song()
    result = bridge_ops.apply_operation(song, {
        "op": "set_device_parameter", "track": "KICK", "device": "EQ Eight",
        "parameter": "Gain A", "value": -3.0})
    check("the device parameter is written", song.tracks[0].devices[0].parameters[1].value == -3.0, result)
    check("the device parameter is reported with its min/max", result["parameter"]["min"] == -15.0, result["parameter"])
    try:
        bridge_ops.apply_operation(unique_song(), {
            "op": "set_device_parameter", "track": "KICK", "device": "EQ Eight",
            "parameter": "Gain A", "value": 99})
        check("a device value outside the range is refused", False, "an error was expected")
    except bridge_ops.BridgeError:
        check("a device value outside the range is refused", True)
    try:
        bridge_ops.apply_operation(unique_song(), {
            "op": "set_device_parameter", "track": "KICK", "device": "Yok Boyle", "parameter": "x", "value": 1})
        check("a device that does not exist is refused", False, "an error was expected")
    except bridge_ops.BridgeError:
        check("a device that does not exist is refused", True)

    listed = bridge_ops.apply_operation(unique_song(), {"op": "list_device_parameters", "track": "KICK", "device": "EQ Eight"})
    check("device parameters are listed", len(listed["parameters"]) == 2, listed)

    # --- transport ---
    song = unique_song()
    result = bridge_ops.apply_operation(song, {"op": "transport", "action": "play", "position": 32})
    check("transport plays", song.is_playing is True and song.current_song_time == 32.0, result)
    result = bridge_ops.apply_operation(song, {"op": "transport", "action": "stop"})
    check("transport stops", song.is_playing is False, result)
    try:
        bridge_ops.apply_operation(unique_song(), {"op": "transport", "action": "rewind"})
        check("an unknown transport action is refused", False, "an error was expected")
    except bridge_ops.BridgeError:
        check("an unknown transport action is refused", True)

    # --- locator ---
    song = unique_song()
    result = bridge_ops.apply_operation(song, {"op": "create_locator", "beat": 96, "name": "Drop"})
    check("a locator is created and named",
          result["created"] and song.cue_points[0].time == 96 and song.cue_points[0].name == "Drop", result)
    result = bridge_ops.apply_operation(song, {"op": "create_locator", "beat": 96, "name": "Hook"})
    check("a locator on the same beat is renamed, not deleted",
          result["adopted"] and len(song.cue_points) == 1 and song.cue_points[0].name == "Hook", result)

    # --- bilinmeyen islem ---
    try:
        bridge_ops.apply_operation(unique_song(), {"op": "delete_everything"})
        check("an unknown operation is refused", False, "an error was expected")
    except bridge_ops.BridgeError as error:
        check("an unknown operation is refused", "unknown op" in str(error), str(error))
    try:
        bridge_ops.apply_operation(unique_song(), {})
        check("a request with no op is refused", False, "an error was expected")
    except bridge_ops.BridgeError:
        check("a request with no op is refused", True)

    # --- Arrangement clip writing (the writer the SDK extension used to own) ----
    song = FakeSong()
    notes = [{"pitch": 36, "start": 0.0, "duration": 0.5, "velocity": 110},
             {"pitch": 36, "start": 2.0, "duration": 0.5, "velocity": 100}]
    result = bridge_ops.apply_operation(song, {"op": "write_arrangement_clip", "track": "BASS",
                                    "start_beat": 32.0, "length_beats": 16.0, "name": "Verse",
                                    "notes": notes})
    bass = song.tracks[1]
    check("an arrangement clip is created on the named track at the beat",
          len(bass.arrangement_clips) == 1 and bass.arrangement_clips[0].start_time == 32.0,
          [(c.name, c.start_time) for c in bass.arrangement_clips])
    check("the notes land in it and the count is read back from Live",
          result["note_count"] == 2 and result["verified_note_count"] == 2, result)
    again = bridge_ops.apply_operation(song, {"op": "write_arrangement_clip", "track": "BASS",
                                   "start_beat": 32.0, "length_beats": 16.0, "name": "Verse",
                                   "notes": notes[:1]})
    check("writing the same section again replaces the clip instead of stacking one on it",
          len(bass.arrangement_clips) == 1 and again["replaced"] == 1 and again["note_count"] == 1,
          (len(bass.arrangement_clips), again))
    bridge_ops.apply_operation(song, {"op": "write_arrangement_clip", "track": "BASS",
                           "start_beat": 48.0, "length_beats": 8.0, "name": "Hook", "notes": notes})
    check("a different section on the same track is a second clip, untouched by the first",
          len(bass.arrangement_clips) == 2, [c.name for c in bass.arrangement_clips])
    try:
        bridge_ops.apply_operation(song, {"op": "write_arrangement_clip", "track": "NOPE", "notes": notes})
        check("an unknown track is refused", False)
    except bridge_ops.BridgeError:
        check("an unknown track is refused", True)
    try:
        bridge_ops.apply_operation(song, {"op": "write_arrangement_clip", "track": "BASS", "start_beat": -1,
                               "notes": notes})
        check("a negative start beat is refused", False)
    except bridge_ops.BridgeError:
        check("a negative start beat is refused", True)

    # --- state capture survives tracks that refuse .arm ---------------------------
    song = FakeSong()
    state = bridge_ops.apply_operation(song, {"op": "get_state"})
    check("get_state does not die on a Return track that raises on .arm",
          isinstance(state, dict) and state.get("track_count") == 3, state if not isinstance(state, dict) else state.get("track_count"))
    try:
        song.return_tracks[0].arm
        check("the fake really raises like Live does", False)
    except RuntimeError:
        check("the fake really raises like Live does", True)

    # --- create_midi_track: the "from scratch" gap ----------------------------
    song = FakeSong()
    browser = FakeBrowser(song)
    made = bridge_ops.apply_operation(song, {"op": "create_midi_track", "name": "Main Bass",
                                             "instrument_family": "Basic Analog Bass"}, browser=browser)
    check("a missing track is created at the end and named exactly",
          made.get("created") is True and song.tracks[-1].name == "Main Bass" and made.get("index") == 3, made)
    check("the instrument family lands on that very track",
          made.get("instrument") == "loaded: Basic Analog Bass" and [d.name for d in song.tracks[-1].devices] == ["Basic Analog Bass"],
          (made.get("instrument"), [d.name for d in song.tracks[-1].devices]))
    again = bridge_ops.apply_operation(song, {"op": "create_midi_track", "name": "Main Bass",
                                              "instrument_family": "Basic Analog Bass"}, browser=browser)
    check("a rebuild adopts the existing track instead of duplicating it",
          again.get("adopted") is True and sum(1 for t in song.tracks if t.name == "Main Bass") == 1
          and again.get("instrument") == "kept: track already existed", again)
    handed = bridge_ops.apply_operation(song, {"op": "create_midi_track", "name": "Main Bass",
                                               "instrument_family": "Drum Rack", "load_instrument_on_adopt": True}, browser=browser)
    check("the surface can load the preset onto a track the extension already created",
          handed.get("adopted") is True and handed.get("instrument") == "loaded: Drum Rack" and handed.get("adopted_and_loaded") is True,
          handed)
    missing = bridge_ops.apply_operation(song, {"op": "create_midi_track", "name": "Keys",
                                                "instrument_family": "Electric Piano Daze"}, browser=browser)
    check("an unknown instrument family is reported, not raised",
          missing.get("created") is True and missing.get("instrument", "").startswith("not_found"), missing)
    nobrowser = bridge_ops.apply_operation(song, {"op": "create_midi_track", "name": "Pad",
                                                  "instrument_family": "Drum Rack"})
    check("without a browser the track is still created and the gap is named",
          nobrowser.get("created") is True and nobrowser.get("instrument", "").startswith("unavailable"), nobrowser)
    song.tracks.append(FakeTrack("Vocal", midi=False))
    try:
        bridge_ops.apply_operation(song, {"op": "create_midi_track", "name": "Vocal"}, browser=browser)
        check("an audio track wearing the name is refused, never adopted", False)
    except bridge_ops.BridgeError as error:
        check("an audio track wearing the name is refused, never adopted", "non-MIDI" in str(error), str(error))
    try:
        bridge_ops.apply_operation(song, {"op": "create_midi_track", "name": "KICK"}, browser=browser)
        check("an ambiguous name is refused", False)
    except bridge_ops.BridgeError as error:
        check("an ambiguous name is refused", "found 2" in str(error), str(error))

    # --- create_locator when Live has not refreshed cue_points yet -------------
    class LaggingSong(FakeSong):
        """set_or_delete_cue creates the cue but cue_points only shows it later."""
        def set_or_delete_cue(self):
            self._pending = FakeCue("", self.current_song_time)
    song = LaggingSong()
    lagging = bridge_ops.apply_operation(song, {"op": "create_locator", "beat": 64, "name": "Drop"})
    check("an unrefreshed cue list yields created-but-unverified, never an error, never a second toggle",
          lagging.get("created") is True and lagging.get("verified") is False and song.cue_points == [], lagging)

    # --- create_locator beyond the arrangement end ----------------------------
    song = FakeSong()
    try:
        bridge_ops.apply_operation(song, {"op": "create_locator", "beat": 4096, "name": "Outro"})
        check("a locator past the arrangement length is refused with the reason", False)
    except bridge_ops.BridgeError as error:
        check("a locator past the arrangement length is refused with the reason",
              "beyond the arrangement length" in str(error) and song.cue_points == [], str(error))

    # --- set_key --------------------------------------------------------------
    song = FakeSong()
    song.root_note = 0
    song.scale_name = "Major"
    keyed = bridge_ops.apply_operation(song, {"op": "set_key", "root": "F", "mode": "Minor"})
    check("set_key writes Live's own song key and reports before/after",
          song.root_note == 5 and song.scale_name == "Minor" and keyed.get("before", {}).get("root") == "C"
          and keyed.get("after") == {"root": "F", "scale_name": "Minor"}, keyed)
    try:
        bridge_ops.apply_operation(song, {"op": "set_key", "root": "H", "mode": "Minor"})
        check("an unknown root is refused", False)
    except bridge_ops.BridgeError:
        check("an unknown root is refused", True)

    print("%d checks passed:" % len(checks))
    for label in checks:
        print("  ok  %s" % label)
    if failures:
        print()
        print("FAILED:")
        for failure in failures:
            print("  - %s" % failure)
        sys.exit(1)
    print("BRIDGE COMMAND LAYER WORKS")


if __name__ == "__main__":
    run()
