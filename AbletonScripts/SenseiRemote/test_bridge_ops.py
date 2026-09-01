#!/usr/bin/env python3
"""Kopru komut katmaninin dogrulanmasi -- Ableton acilmadan.

bridge_ops song nesnesini disaridan aldigi icin buradaki sahte song, Live'in
Object Model'inin kullanilan yuzeyini taklit eder. Kanitladigi sey komut
mantigi; kanitlamadigi sey Live'in gercek LOM'unun ayni sekilde davrandigi.
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


class FakeTrack(object):
    def __init__(self, name, midi=True, devices=None):
        self.name = name
        self.has_midi_input = midi
        self.mute = False
        self.solo = False
        self.arm = False
        self.mixer_device = FakeMixer()
        self.devices = devices or []


class FakeCue(object):
    def __init__(self, name, time_):
        self.name = name
        self.time = time_


class FakeView(object):
    def __init__(self, selected):
        self.selected_track = selected


class FakeSong(object):
    def __init__(self):
        eq = FakeDevice("EQ Eight", [FakeParameter("Frequency A", 100.0, 20.0, 20000.0),
                                     FakeParameter("Gain A", 0.0, -15.0, 15.0)])
        self.tracks = [FakeTrack("KICK", devices=[eq]), FakeTrack("BASS"), FakeTrack("KICK COPY")]
        self.tracks[2].name = "KICK"  # ayni isimde ikinci track: belirsizlik testi
        self.tempo = 120.0
        self.is_playing = False
        self.current_song_time = 0.0
        self.signature_numerator = 4
        self.signature_denominator = 4
        self.cue_points = []
        self.view = FakeView(self.tracks[1])

    def start_playing(self):
        self.is_playing = True

    def stop_playing(self):
        self.is_playing = False

    def continue_playing(self):
        self.is_playing = True

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
    check("durum sema surumu tasiyor", state["schema_version"] == bridge_ops.SCHEMA_VERSION)
    check("tempo okunuyor", state["tempo"] == 120.0, state["tempo"])
    check("track sayisi dogru", state["track_count"] == 3, state["track_count"])
    check("secili track bildiriliyor", state["selected_track"] == "BASS", state["selected_track"])
    check("mikser degerleri min/max ile geliyor",
          state["tracks"][0]["volume"]["max"] == 1.0 and state["tracks"][0]["panning"]["min"] == -1.0,
          state["tracks"][0].get("volume"))
    check("parametre gosterim degeri de veriliyor", state["tracks"][0]["volume"]["display_value"] == "0.85")
    check("cihazlar listeleniyor", state["tracks"][0]["devices"][0]["name"] == "EQ Eight", state["tracks"][0]["devices"])
    check("cihazlar istege bagli kapatilabiliyor", "devices" not in bridge_ops.capture_state(song, include_devices=False)["tracks"][0])

    # --- tempo ---
    result = bridge_ops.apply_operation(unique_song(), {"op": "set_tempo", "bpm": 126})
    check("tempo yaziliyor ve once/sonra bildiriliyor", result["before"] == 120.0 and result["after"] == 126.0, result)
    try:
        bridge_ops.apply_operation(unique_song(), {"op": "set_tempo", "bpm": 5})
        check("aralik disi tempo reddediliyor", False, "hata bekleniyordu")
    except bridge_ops.BridgeError as error:
        check("aralik disi tempo reddediliyor", "outside Live's range" in str(error), str(error))

    # --- mikser ---
    song = unique_song()
    result = bridge_ops.apply_operation(song, {"op": "set_mixer", "track": "KICK", "volume": 0.5, "mute": True})
    check("mikser degeri yaziliyor", song.tracks[0].mixer_device.volume.value == 0.5, result)
    check("mute yaziliyor", song.tracks[0].mute is True, result)
    check("degisiklikler once/sonra olarak raporlaniyor",
          result["changes"]["volume"]["before"] == 0.85 and result["changes"]["volume"]["after"] == 0.5, result)
    try:
        bridge_ops.apply_operation(unique_song(), {"op": "set_mixer", "track": "KICK", "volume": 5})
        check("aralik disi mikser degeri reddediliyor", False, "hata bekleniyordu")
    except bridge_ops.BridgeError as error:
        check("aralik disi mikser degeri reddediliyor", "outside" in str(error), str(error))
    try:
        bridge_ops.apply_operation(unique_song(), {"op": "set_mixer", "track": "KICK"})
        check("bos mikser komutu reddediliyor", False, "hata bekleniyordu")
    except bridge_ops.BridgeError:
        check("bos mikser komutu reddediliyor", True)

    ambiguous = FakeSong()  # iki track de "KICK"
    try:
        bridge_ops.apply_operation(ambiguous, {"op": "set_mixer", "track": "KICK", "mute": True})
        check("ayni isimli iki track belirsizlik sayilir", False, "hata bekleniyordu")
    except bridge_ops.BridgeError as error:
        check("ayni isimli iki track belirsizlik sayilir", "found 2" in str(error), str(error))

    # --- cihaz parametresi ---
    song = unique_song()
    result = bridge_ops.apply_operation(song, {
        "op": "set_device_parameter", "track": "KICK", "device": "EQ Eight",
        "parameter": "Gain A", "value": -3.0})
    check("cihaz parametresi yaziliyor", song.tracks[0].devices[0].parameters[1].value == -3.0, result)
    check("cihaz parametresi min/max ile raporlaniyor", result["parameter"]["min"] == -15.0, result["parameter"])
    try:
        bridge_ops.apply_operation(unique_song(), {
            "op": "set_device_parameter", "track": "KICK", "device": "EQ Eight",
            "parameter": "Gain A", "value": 99})
        check("aralik disi cihaz degeri reddediliyor", False, "hata bekleniyordu")
    except bridge_ops.BridgeError:
        check("aralik disi cihaz degeri reddediliyor", True)
    try:
        bridge_ops.apply_operation(unique_song(), {
            "op": "set_device_parameter", "track": "KICK", "device": "Yok Boyle", "parameter": "x", "value": 1})
        check("olmayan cihaz reddediliyor", False, "hata bekleniyordu")
    except bridge_ops.BridgeError:
        check("olmayan cihaz reddediliyor", True)

    listed = bridge_ops.apply_operation(unique_song(), {"op": "list_device_parameters", "track": "KICK", "device": "EQ Eight"})
    check("cihaz parametreleri listeleniyor", len(listed["parameters"]) == 2, listed)

    # --- transport ---
    song = unique_song()
    result = bridge_ops.apply_operation(song, {"op": "transport", "action": "play", "position": 32})
    check("transport oynatiyor", song.is_playing is True and song.current_song_time == 32.0, result)
    result = bridge_ops.apply_operation(song, {"op": "transport", "action": "stop"})
    check("transport durduruyor", song.is_playing is False, result)
    try:
        bridge_ops.apply_operation(unique_song(), {"op": "transport", "action": "rewind"})
        check("bilinmeyen transport eylemi reddediliyor", False, "hata bekleniyordu")
    except bridge_ops.BridgeError:
        check("bilinmeyen transport eylemi reddediliyor", True)

    # --- locator ---
    song = unique_song()
    result = bridge_ops.apply_operation(song, {"op": "create_locator", "beat": 96, "name": "Drop"})
    check("locator olusturuluyor ve adlandiriliyor",
          result["created"] and song.cue_points[0].time == 96 and song.cue_points[0].name == "Drop", result)
    result = bridge_ops.apply_operation(song, {"op": "create_locator", "beat": 96, "name": "Hook"})
    check("ayni beat'teki locator silinmiyor, yeniden adlandiriliyor",
          result["adopted"] and len(song.cue_points) == 1 and song.cue_points[0].name == "Hook", result)

    # --- bilinmeyen islem ---
    try:
        bridge_ops.apply_operation(unique_song(), {"op": "delete_everything"})
        check("bilinmeyen islem reddediliyor", False, "hata bekleniyordu")
    except bridge_ops.BridgeError as error:
        check("bilinmeyen islem reddediliyor", "unknown op" in str(error), str(error))
    try:
        bridge_ops.apply_operation(unique_song(), {})
        check("op'suz istek reddediliyor", False, "hata bekleniyordu")
    except bridge_ops.BridgeError:
        check("op'suz istek reddediliyor", True)

    print("%d kontrol gecti:" % len(checks))
    for label in checks:
        print("  ok  %s" % label)
    if failures:
        print()
        print("BASARISIZ:")
        for failure in failures:
            print("  - %s" % failure)
        sys.exit(1)
    print("KOPRU KOMUT KATMANI CALISIYOR")


if __name__ == "__main__":
    run()
