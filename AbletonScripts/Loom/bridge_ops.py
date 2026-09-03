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
            # None where Live refuses to say (Main/Return raise on .arm); see _attr.
            "arm": _attr(track, "arm", None),
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
    length = getattr(song, "song_length", None)
    if length is not None and beat > float(length):
        # Live refuses to move the playhead past the end of the arrangement
        # ("Cannot set the Songtime behind the Songlength"), and a clamped
        # playhead would drop the cue at the wrong beat. Say so instead.
        raise BridgeError("beat %g is beyond the arrangement length %g; write clips there first"
                          % (beat, float(length)))
    def _at_beat():
        for cue in getattr(song, "cue_points", []):
            if abs(float(cue.time) - beat) < 1e-6:
                return cue
        return None

    cue = _at_beat()
    if cue is not None:
        before = cue.name
        if payload.get("name"):
            cue.name = str(payload["name"])
        return {"created": False, "adopted": True, "beat": beat, "name_before": before, "name": cue.name}
    song.current_song_time = beat
    song.set_or_delete_cue()
    cue = _at_beat()
    if cue is not None:
        if payload.get("name"):
            cue.name = str(payload["name"])
        return {"created": True, "adopted": False, "beat": beat, "name": cue.name, "verified": True}
    # Live toggles the cue synchronously but may not refresh cue_points until
    # the next tick. Measured on 12.4.15b1: raising here made the caller retry,
    # and the retry *deleted* the cue it could not see. Report it unverified
    # instead; the caller checks the cue list afterwards.
    return {"created": True, "adopted": False, "beat": beat, "name": payload.get("name"), "verified": False,
            "note": "cue toggled; cue_points not refreshed yet, verify with get_state"}


_PITCH_CLASS = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
    "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}
_PITCH_NAME = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _find_browser_item(browser, term, depth=0, item=None):
    """Depth-first search of Live's browser for the first loadable item whose
    name contains `term`. Ported from ArrangementGPSBuilder so the Loom surface
    can load an instrument family itself; roots are tried in the order Live's
    own browser shows them."""
    if item is None:
        term = term.lower()
        for attr in ("drums", "instruments", "sounds", "packs", "user_library"):
            try:
                root = getattr(browser, attr)
            except Exception:
                root = None
            if root is None:
                continue
            found = _find_browser_item(browser, term, 0, root)
            if found is not None:
                return found
        return None
    if depth > 6:
        return None
    try:
        if term in item.name.lower() and item.is_loadable:
            return item
    except Exception:
        pass
    try:
        children = list(item.children)
    except Exception:
        children = []
    for child in children:
        found = _find_browser_item(browser, term, depth + 1, child)
        if found is not None:
            return found
    return None


def op_create_midi_track(song, payload, browser=None):
    """Create (or adopt) a MIDI track by exact name, optionally loading an
    instrument family from the browser onto it. Adopting an existing MIDI
    track of that name is deliberate: a rebuild must not duplicate tracks."""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise BridgeError("track name is required")
    same_name = [track for track in _tracks(song) if track.name == name]
    if len(same_name) > 1:
        raise BridgeError("expected at most one track named %r, found %d" % (name, len(same_name)))
    if same_name and not getattr(same_name[0], "has_midi_input", False):
        raise BridgeError("a non-MIDI track is already named %r" % name)
    if same_name:
        track = same_name[0]
        created = False
    else:
        if not hasattr(song, "create_midi_track"):
            raise BridgeError("this Live cannot create MIDI tracks from a control surface")
        song.create_midi_track(len(list(_tracks(song))))
        track = list(_tracks(song))[-1]
        track.name = name
        created = True
    result = {"created": created, "adopted": not created, "name": track.name,
              "index": list(_tracks(song)).index(track), "instrument": "skipped"}
    family = str(payload.get("instrument_family") or "").strip()
    # load_instrument_on_adopt: the extension bridge creates tracks but cannot
    # load presets; when the MCP hands the preset step to this surface the
    # track already exists and must still get its instrument.
    if family and (created or payload.get("load_instrument_on_adopt")):
        if browser is None:
            result["instrument"] = "unavailable: no browser"
        else:
            item = _find_browser_item(browser, family)
            if item is None:
                result["instrument"] = "not_found: %s" % family
            else:
                try:
                    song.view.selected_track = track
                    browser.load_item(item)
                    result["instrument"] = "loaded: %s" % item.name
                except Exception as error:
                    result["instrument"] = "failed: %s" % error
    elif family and not created:
        result["instrument"] = "kept: track already existed"
    if family and not created and payload.get("load_instrument_on_adopt") and result["instrument"].startswith("loaded"):
        result["adopted_and_loaded"] = True
    return result


def op_set_key(song, payload):
    """Set Live's own Song Key display (root + scale name)."""
    root = str(payload.get("root") or "").strip().replace(u"\u266f", "#").replace(u"\u266d", "b")
    mode = str(payload.get("mode") or "").strip()
    if root not in _PITCH_CLASS:
        raise BridgeError("unknown root %r" % root)
    if not mode:
        raise BridgeError("mode (scale name) is required")
    before = {"root": _PITCH_NAME[int(getattr(song, "root_note", 0)) % 12],
              "scale_name": getattr(song, "scale_name", None)}
    song.root_note = _PITCH_CLASS[root]
    song.scale_name = mode
    return {"before": before, "after": {"root": _PITCH_NAME[int(song.root_note) % 12],
                                        "scale_name": song.scale_name}}


CAPTURE_TRACK_NAME = "Loom Capture"
RESAMPLING_NAMES = ("Resampling", "Resample")


def _capture_track(song, create=True):
    """The audio track that records Live's own output. Adopted by name,
    created at the end of the set when missing."""
    matches = [track for track in _tracks(song) if track.name == CAPTURE_TRACK_NAME]
    if len(matches) > 1:
        raise BridgeError("expected at most one track named %r, found %d" % (CAPTURE_TRACK_NAME, len(matches)))
    if matches:
        return matches[0], False
    if not create:
        raise BridgeError("no track named %r" % CAPTURE_TRACK_NAME)
    if not hasattr(song, "create_audio_track"):
        raise BridgeError("this Live cannot create audio tracks from a control surface")
    song.create_audio_track(len(list(_tracks(song))))
    track = list(_tracks(song))[-1]
    track.name = CAPTURE_TRACK_NAME
    return track, True


def _resampling_routing(track):
    for candidate in getattr(track, "available_input_routing_types", []) or []:
        if getattr(candidate, "display_name", "") in RESAMPLING_NAMES:
            return candidate
    raise BridgeError("no Resampling input on %r; available: %s" % (
        track.name, [getattr(c, "display_name", "?") for c in getattr(track, "available_input_routing_types", []) or []]))


# The capture is deliberately five separate requests, one Live tick each.
# Doing create + route + arm + record + play in one tick on a freshly
# created track segfaulted Live 12.4.15b1 (2026-09-03 23:06, no Python
# frame in the crash) -- Live had not finished building the track.

def op_capture_prepare(song, payload):
    """Adopt or create the 'Loom Capture' audio track. Nothing else."""
    track, created = _capture_track(song)
    return {"track": track.name, "created": created,
            "input": getattr(getattr(track, "input_routing_type", None), "display_name", None)}


def op_capture_route(song, payload):
    """Point the capture track's input at Live's own Resampling."""
    track, _created = _capture_track(song, create=False)
    routing = _resampling_routing(track)
    before = getattr(getattr(track, "input_routing_type", None), "display_name", None)
    if before != routing.display_name:
        track.input_routing_type = routing
    return {"track": track.name, "input_before": before,
            "input": getattr(getattr(track, "input_routing_type", None), "display_name", None)}


def op_capture_arm(song, payload):
    track, _created = _capture_track(song, create=False)
    wanted = bool(payload.get("arm", True))
    track.arm = wanted
    return {"track": track.name, "armed": bool(track.arm)}


def op_capture_record(song, payload):
    """Record mode on and transport running: the armed Resampling track
    records the master into an arrangement clip."""
    track, _created = _capture_track(song, create=False)
    if not bool(getattr(track, "arm", False)):
        raise BridgeError("%r is not armed; run capture_arm first" % track.name)
    if "position" in payload:
        position = float(payload["position"])
        if position < 0:
            raise BridgeError("position must be >= 0")
        song.current_song_time = position
    before = len(list(getattr(track, "arrangement_clips", []) or []))
    song.record_mode = True
    if not bool(getattr(song, "is_playing", False)):
        song.start_playing()
    return {"track": track.name, "record_mode": bool(song.record_mode),
            "is_playing": bool(getattr(song, "is_playing", False)),
            "start_time": getattr(song, "current_song_time", None), "clips_before": before}


def op_capture_stop(song, payload):
    track, _created = _capture_track(song, create=False)
    song.record_mode = False
    if not payload.get("keep_playing"):
        song.stop_playing()
    if payload.get("disarm", True):
        track.arm = False
    clips = list(getattr(track, "arrangement_clips", []) or [])
    if not clips:
        raise BridgeError("no clip was recorded on %r (was record_mode on and the transport running?)" % track.name)
    newest = max(clips, key=lambda c: float(getattr(c, "start_time", 0.0)))
    path = getattr(newest, "file_path", None)
    return {"track": track.name, "clip_name": getattr(newest, "name", None), "start_time": getattr(newest, "start_time", None),
            "end_time": getattr(newest, "end_time", None), "file_path": path, "clips": len(clips),
            "record_mode": bool(song.record_mode), "is_playing": bool(getattr(song, "is_playing", False))}


class _NoteSpec(object):
    """Stand-in for Live.Clip.MidiNoteSpecification where Live is not importable.

    Only the test harness ever sees this; inside Live the real class is used.
    Same attribute names, so a caller cannot tell them apart -- which is the
    point: the fakes read attributes, and a dict would fail them the way it
    fails Live.
    """
    def __init__(self, pitch, start_time, duration, velocity, mute=False):
        self.pitch = pitch
        self.start_time = start_time
        self.duration = duration
        self.velocity = velocity
        self.mute = mute


def note_specs(notes):
    """Live.Clip.MidiNoteSpecification objects for add_new_notes.

    add_new_notes on the Python side takes MidiNoteSpecification objects, NOT
    dicts -- a dict is a Boost.Python ArgumentError in Live. The Max-for-Live
    docs show the dict form because that is the JavaScript API; Ableton's own
    pushbase and the working SenseiPadProbe script both construct
    MidiNoteSpecification(pitch=, start_time=, duration=, velocity=, mute=).
    Live is imported lazily so this module stays importable without it.
    """
    try:
        from Live.Clip import MidiNoteSpecification as spec_class  # type: ignore
    except Exception:
        spec_class = _NoteSpec
    specs = []
    for note in notes or []:
        specs.append(spec_class(
            pitch=int(note["pitch"]),
            start_time=float(note.get("start", note.get("time", 0.0))),
            duration=max(0.01, float(note["duration"])),
            velocity=max(1, min(127, int(note.get("velocity", 100)))),
            mute=False,
        ))
    return specs


def legacy_note_tuples(specs):
    """(pitch, time, duration, velocity, mute) for the pre-Live-11 set_notes."""
    return tuple((s.pitch, s.start_time, s.duration, s.velocity, False) for s in specs)

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
    specs = note_specs(payload.get("notes", []))
    if hasattr(clip, "add_new_notes"):
        clip.add_new_notes(tuple(specs))
    else:
        clip.set_notes(legacy_note_tuples(specs))

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
    "create_midi_track": op_create_midi_track,
    "set_key": op_set_key,
    "capture_prepare": op_capture_prepare,
    "capture_route": op_capture_route,
    "capture_arm": op_capture_arm,
    "capture_record": op_capture_record,
    "capture_stop": op_capture_stop,
}

# Operations that need Live's browser as well as the song.
_BROWSER_OPERATIONS = frozenset(["create_midi_track"])


def apply_operation(song, payload, browser=None):
    """Tek giris noktasi. write_clip disindaki her islem buradan gecer.
    `browser` is Live's application browser, only needed by operations that
    load content (create_midi_track with an instrument family)."""
    operation = payload.get("op")
    if operation is None:
        raise BridgeError("payload has no 'op'")
    handler = OPERATIONS.get(operation)
    if handler is None:
        raise BridgeError("unknown op %r. Known: %s" % (operation, ", ".join(sorted(OPERATIONS))))
    if operation in _BROWSER_OPERATIONS:
        return handler(song, payload, browser)
    return handler(song, payload)
