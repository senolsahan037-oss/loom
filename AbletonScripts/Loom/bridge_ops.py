"""Live command handlers -- NOT coupled to Ableton.

Every function takes Live's `song` object from outside. That is deliberate: it
lets the tests hand in a fake song and verify the whole command layer without
opening Ableton. Loom.py does nothing but call into this module.

Fail-closed:
  * An unknown operation is refused
  * A track name must match exactly one track
  * A value may not leave the parameter's own min/max
  * Every operation reports what it did -- it never just says "ok"
"""

SCHEMA_VERSION = "sensei.bridge.v2"


class BridgeError(Exception):
    pass


def _tracks(song):
    return list(song.tracks)


def _find_track(song, name):
    if not name:
        raise BridgeError("track name is required")
    matches = [track for track in _tracks(song) if track.name == name]
    if len(matches) != 1:
        raise BridgeError("expected exactly one track named %r, found %d" % (name, len(matches)))
    return matches[0]


def _find_device(track, name):
    matches = [device for device in track.devices if device.name == name]
    if len(matches) != 1:
        raise BridgeError("expected exactly one device named %r on %r, found %d" % (name, track.name, len(matches)))
    return matches[0]


def _find_parameter(device, name):
    matches = [param for param in device.parameters if param.name == name]
    if len(matches) != 1:
        raise BridgeError("expected exactly one parameter named %r on %r, found %d" % (name, device.name, len(matches)))
    return matches[0]


def _describe(parameter):
    display = None
    reader = getattr(parameter, "str_for_value", None)
    if callable(reader):
        try:
            display = reader(parameter.value)
        except Exception:
            display = None
    return {
        "name": parameter.name,
        "value": parameter.value,
        "min": parameter.min,
        "max": parameter.max,
        "display_value": display,
    }


def _set_parameter(parameter, value):
    value = float(value)
    if not (parameter.min <= value <= parameter.max):
        raise BridgeError(
            "value %g outside %r range [%g, %g]" % (value, parameter.name, parameter.min, parameter.max)
        )
    before = parameter.value
    parameter.value = value
    return {"before": before, "after": parameter.value}


# --- durum okuma ----------------------------------------------------------

def _attr(obj, name, default=None):
    """Live'de bazi ozellikler VAR ama okununca hata atar.

    Olculdu 2026-09-03: return ve main track'lerde `arm` okumak
    "RuntimeError: Main and Return Tracks have no 'Arm' state!" veriyor.
    getattr'in varsayilani burada ise yaramaz cunku ozellik eksik degil,
    okumasi patliyor — bu yuzden durum yayini her turda dusuyordu.
    """
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def capture_state(song, include_devices=True):
    """Live'in o anki durumu. GAP-001'in okuma yarisi burasi."""
    view = getattr(song, "view", None)
    selected = getattr(view, "selected_track", None) if view is not None else None

    tracks = []
    for index, track in enumerate(_tracks(song)):
        mixer = getattr(track, "mixer_device", None)
        entry = {
            "index": index,
            "name": track.name,
            "has_midi_input": bool(_attr(track, "has_midi_input", False)),
            "mute": bool(_attr(track, "mute", False)),
            "solo": bool(_attr(track, "solo", False)),
            "arm": bool(_attr(track, "arm", False)),
            "is_selected": selected is not None and track is selected,
        }
        if mixer is not None:
            volume = _attr(mixer, "volume")
            panning = _attr(mixer, "panning")
            if volume is not None:
                entry["volume"] = _describe(volume)
            if panning is not None:
                entry["panning"] = _describe(panning)
        if include_devices:
            entry["devices"] = [
                {"name": device.name, "class_name": getattr(device, "class_name", None),
                 "parameter_count": len(list(device.parameters))}
                for device in getattr(track, "devices", [])
            ]
        tracks.append(entry)

    cue_points = [
        {"name": cue.name, "time": cue.time}
        for cue in getattr(song, "cue_points", [])
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "tempo": getattr(song, "tempo", None),
        "is_playing": bool(getattr(song, "is_playing", False)),
        "current_song_time": getattr(song, "current_song_time", None),
        "signature_numerator": getattr(song, "signature_numerator", None),
        "signature_denominator": getattr(song, "signature_denominator", None),
        "selected_track": selected.name if selected is not None else None,
        "track_count": len(tracks),
        "tracks": tracks,
        "cue_points": cue_points,
    }


# --- islemler -------------------------------------------------------------

def op_get_state(song, payload):
    return capture_state(song, include_devices=bool(payload.get("include_devices", True)))


def op_set_tempo(song, payload):
    tempo = float(payload["bpm"])
    if not (20.0 <= tempo <= 999.0):
        raise BridgeError("tempo %g outside Live's range [20, 999]" % tempo)
    before = song.tempo
    song.tempo = tempo
    return {"before": before, "after": song.tempo}


def op_set_mixer(song, payload):
    track = _find_track(song, payload.get("track"))
    mixer = track.mixer_device
    changes = {}
    if "volume" in payload:
        changes["volume"] = _set_parameter(mixer.volume, payload["volume"])
    if "pan" in payload:
        changes["pan"] = _set_parameter(mixer.panning, payload["pan"])
    if "mute" in payload:
        before = bool(track.mute)
        track.mute = bool(payload["mute"])
        changes["mute"] = {"before": before, "after": bool(track.mute)}
    if "solo" in payload:
        before = bool(track.solo)
        track.solo = bool(payload["solo"])
        changes["solo"] = {"before": before, "after": bool(track.solo)}
    if not changes:
        raise BridgeError("set_mixer needs at least one of: volume, pan, mute, solo")
    return {"track": track.name, "changes": changes}


def op_set_device_parameter(song, payload):
    track = _find_track(song, payload.get("track"))
    device = _find_device(track, payload.get("device"))
    parameter = _find_parameter(device, payload.get("parameter"))
    change = _set_parameter(parameter, payload["value"])
    return {
        "track": track.name,
        "device": device.name,
        "parameter": _describe(parameter),
        "change": change,
    }


def op_list_device_parameters(song, payload):
    track = _find_track(song, payload.get("track"))
    device = _find_device(track, payload.get("device"))
    return {
        "track": track.name,
        "device": device.name,
        "parameters": [_describe(parameter) for parameter in device.parameters],
    }


def op_transport(song, payload):
    action = payload.get("action")
    if action not in ("play", "stop", "continue"):
        raise BridgeError("transport action must be play, stop or continue")
    if "position" in payload:
        position = float(payload["position"])
        if position < 0:
            raise BridgeError("position must be >= 0")
        song.current_song_time = position
    if action == "play":
        song.start_playing()
    elif action == "stop":
        song.stop_playing()
    else:
        song.continue_playing()
    return {
        "action": action,
        "is_playing": bool(getattr(song, "is_playing", False)),
        "current_song_time": getattr(song, "current_song_time", None),
    }


def op_create_locator(song, payload):
    beat = float(payload["beat"])
    if beat < 0:
        raise BridgeError("beat must be >= 0")
    existing = {cue.time: cue for cue in getattr(song, "cue_points", [])}
    if beat in existing:
        cue = existing[beat]
        before = cue.name
        if payload.get("name"):
            cue.name = str(payload["name"])
        return {"created": False, "adopted": True, "beat": beat, "name_before": before, "name": cue.name}
    song.current_song_time = beat
    song.set_or_delete_cue()
    for cue in getattr(song, "cue_points", []):
        if cue.time == beat:
            if payload.get("name"):
                cue.name = str(payload["name"])
            return {"created": True, "adopted": False, "beat": beat, "name": cue.name}
    raise BridgeError("locator was not created at beat %g" % beat)


def op_write_arrangement_clip(song, payload):
    """Write a MIDI clip straight into the Arrangement, on a named track.

    This is the writer the SDK extension used to own. The Python LOM can do it
    too -- Track.create_midi_clip(start, length) places a clip in the
    Arrangement in beats, and Clip.add_new_notes fills it -- so the whole build
    runs through the one control surface install.py already installs, with
    nothing else to load into Live.

    A rebuild must replace, not stack: a clip of the same name overlapping the
    target range is deleted first. The note count is read back from Live
    rather than trusted, the same posture as create_locator.
    """
    track = _find_track(song, payload.get("track")) if payload.get("track") else song.view.selected_track
    if not getattr(track, "has_midi_input", False):
        raise BridgeError("track %r is not a MIDI track" % getattr(track, "name", "?"))
    start_beat = float(payload.get("start_beat", 0.0))
    length_beats = float(payload.get("length_beats", 16.0))
    if start_beat < 0 or length_beats <= 0:
        raise BridgeError("start_beat must be >= 0 and length_beats > 0")
    if not hasattr(track, "create_midi_clip"):
        raise BridgeError("this Live cannot create Arrangement MIDI clips from a control surface")
    name = str(payload.get("name") or "Loom")
    end_beat = start_beat + length_beats

    replaced = 0
    for clip in list(getattr(track, "arrangement_clips", []) or []):
        overlaps = float(clip.start_time) < end_beat and float(clip.end_time) > start_beat
        if overlaps and getattr(clip, "name", "") == name:
            track.delete_clip(clip)
            replaced += 1

    clip = track.create_midi_clip(start_beat, length_beats)
    clip.name = name
    specs = []
    for note in payload.get("notes", []):
        specs.append({
            "pitch": int(note["pitch"]),
            "start_time": float(note.get("start", note.get("time", 0.0))),
            "duration": max(0.01, float(note["duration"])),
            "velocity": max(1, min(127, int(note.get("velocity", 100)))),
            "mute": False,
        })
    if hasattr(clip, "add_new_notes"):
        clip.add_new_notes(tuple(specs))
    else:
        clip.set_notes(tuple((n["pitch"], n["start_time"], n["duration"], n["velocity"], False)
                             for n in specs))

    written = None
    if hasattr(clip, "get_notes_extended"):
        try:
            written = len(clip.get_notes_extended(0, 128, 0.0, length_beats))
        except Exception:
            written = None
    if written is not None and written != len(specs):
        raise BridgeError("wrote %d notes but Live holds %d" % (len(specs), written))
    return {
        "track": track.name,
        "clip_name": clip.name,
        "start_beat": start_beat,
        "length_beats": length_beats,
        "note_count": len(specs),
        "verified_note_count": written,
        "replaced": replaced,
    }


OPERATIONS = {
    "get_state": op_get_state,
    "set_tempo": op_set_tempo,
    "set_mixer": op_set_mixer,
    "set_device_parameter": op_set_device_parameter,
    "list_device_parameters": op_list_device_parameters,
    "transport": op_transport,
    "create_locator": op_create_locator,
    "write_arrangement_clip": op_write_arrangement_clip,
}


def apply_operation(song, payload):
    """Tek giris noktasi. write_clip disindaki her islem buradan gecer."""
    operation = payload.get("op")
    if operation is None:
        raise BridgeError("payload has no 'op'")
    handler = OPERATIONS.get(operation)
    if handler is None:
        raise BridgeError("unknown op %r. Known: %s" % (operation, ", ".join(sorted(OPERATIONS))))
    return handler(song, payload)
