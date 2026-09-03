"""Loom -- a bidirectional bridge for Ableton Live.

This is the Control Surface Ableton shows in Settings -> Link/MIDI. The folder
name is the name Live displays, so it is "Loom", not the name of whichever
subsystem happens to write into the bridge.

v1 did one thing: take a request off the queue and write a clip into the
selected track. No result was written back, and Live's state could not be read
from outside at all (GAP-001). v2:

  * Every request is processed and moved into done/ or errors/ WITH its result
  * Live's state is published to state/live_state.json on a timer
  * General commands beyond write_clip are supported (see bridge_ops.py)

The whole command layer lives in bridge_ops.py and is not coupled to Live, so
it can be tested without opening Ableton.
"""
import json
import os
import shutil
import time
from pathlib import Path

from _Framework.ControlSurface import ControlSurface

try:
    from . import bridge_ops
except ImportError:  # some Live versions load this as a flat module
    import bridge_ops


BRIDGE_ROOT = Path.home() / "Documents" / "SenseiV2Bridge"
REQUEST_DIR = BRIDGE_ROOT / "requests"
DONE_DIR = BRIDGE_ROOT / "done"
ERROR_DIR = BRIDGE_ROOT / "errors"
STATE_DIR = BRIDGE_ROOT / "state"
STATE_FILE = STATE_DIR / "live_state.json"

REQUEST_EVERY_TICKS = 8
STATE_EVERY_TICKS = 40
SURFACE_VERSION = "loom-surface/2.0.0"


def create_instance(c_instance):
    return Loom(c_instance)


class Loom(ControlSurface):
    def __init__(self, c_instance):
        self._peak_hold = {}
        super().__init__(c_instance)
        self._tick_count = 0
        self._ensure_dirs()
        self.log_message("Loom control surface loaded (%s)" % bridge_ops.SCHEMA_VERSION)
        # Live 12'nin ControlSurface'inde _register_timer_callback YOK; cagirmak
        # 'Loom object has no attribute' hatasi veriyor ve zamanlayici hic
        # calismiyordu (olculdu 2026-09-03: kuyruktaki istekler Haziran'dan beri
        # islenmemis, durum dosyasi saatlerce bayat). Live periyodik olarak
        # update_display() cagirir; kanca odur.

    def update_display(self):
        """Live'in periyodik cagrisi — koprunun kalp atisi."""
        try:
            self._on_timer()
        except Exception as error:
            self.log_message("Loom timer failed: %s" % error)

    def disconnect(self):
        try:
            pass
        finally:
            super().disconnect()

    def _on_timer(self):
        self._tick_count += 1
        if self._tick_count % REQUEST_EVERY_TICKS == 0:
            self._process_next_request()
        if self._tick_count % STATE_EVERY_TICKS == 0:
            self._dump_state()

    # --- durum yayini -----------------------------------------------------

    def _meters(self):
        """Track basina tepe/anlik seviye, dB.

        MixConsoleLive2'den devralindi. Oradaki hali her yoklamada TRACK BASINA
        bir log satiri yaziyordu; olculdu 2026-09-02, Live'in Log.txt'sine
        1.955.378 satir yazmis (dakikada ~40 bin) ve dosyayi 768 MB'a cikarmisti.
        Burada hicbir sey loglanmaz, olcum yalnizca durum dosyasina gider.

        MIDI ciktili track'lerde output_meter_* yoktur; sessizce atlanir.
        """
        out = {}
        # A Song without return tracks is not an error: the per-track guard below
        # was already there for tracks that carry no meters, but the collection
        # access sat outside it, so one missing attribute lost the whole state
        # dump -- silently, because the caller logs and moves on.
        song = self.song()
        tracks = list(getattr(song, "tracks", []) or [])
        tracks += list(getattr(song, "return_tracks", []) or [])
        for track in tracks:
            try:
                left = track.output_meter_left
                right = track.output_meter_right
            except Exception:
                continue
            name = track.name
            peak = max(left, right)
            held = max(self._peak_hold.get(name, 0.0), peak)
            self._peak_hold[name] = held
            out[name] = {
                "left_db": self._meter_db(left),
                "right_db": self._meter_db(right),
                "peak_db": self._meter_db(peak),
                "peak_hold_db": self._meter_db(held),
            }
        return out

    @staticmethod
    def _meter_db(value):
        if value is None or value <= 0.0:
            return None
        import math
        return round(20.0 * math.log10(value), 2)

    def _dump_state(self):
        try:
            state = bridge_ops.capture_state(self.song())
            state["captured_at"] = time.time()
            state["meters"] = self._meters()
            state["surface_version"] = SURFACE_VERSION
            self._ensure_dirs()
            temporary = STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, indent=2))
            os.replace(str(temporary), str(STATE_FILE))
        except Exception as error:
            self.log_message("Loom state dump failed: %s" % error)

    # --- istek islemesi ---------------------------------------------------

    def _process_next_request(self):
        self._ensure_dirs()
        requests = sorted(REQUEST_DIR.glob("*.json"))
        if not requests:
            return

        request_path = requests[0]
        try:
            payload = json.loads(request_path.read_text())
        except Exception as error:
            self._finish(request_path, {}, ERROR_DIR, error="unreadable request: %s" % error)
            return

        try:
            if payload.get("op") in (None, "write_clip"):
                result = self._write_clip(payload)
            else:
                result = bridge_ops.apply_operation(self.song(), payload)
            self._finish(request_path, payload, DONE_DIR, result=result)
            self.log_message("Loom ok: %s" % (payload.get("op") or "write_clip"))
        except Exception as error:
            self.log_message("Loom error: %s" % error)
            self._finish(request_path, payload, ERROR_DIR, error="%s: %s" % (type(error).__name__, error))

    def _finish(self, request_path, payload, destination, result=None, error=None):
        """Write the outcome into the request and move it, so the caller can read it."""
        record = dict(payload) if isinstance(payload, dict) else {}
        record["completed_at"] = time.time()
        record["schema_version"] = bridge_ops.SCHEMA_VERSION
        if error is None:
            record["status"] = "ok"
            record["result"] = result
        else:
            record["status"] = "error"
            record["error"] = error
        try:
            (destination / request_path.name).write_text(json.dumps(record, indent=2, default=str))
            request_path.unlink()
        except Exception:
            try:
                shutil.move(str(request_path), str(destination / request_path.name))
            except Exception:
                pass

    def _write_clip(self, payload):
        track = self.song().view.selected_track
        if not getattr(track, "has_midi_input", False):
            raise RuntimeError("Selected track is not a MIDI track")

        clip_slot = self._target_clip_slot(track)
        length_beats = float(payload.get("length_beats", 32.0))
        if not clip_slot.has_clip:
            clip_slot.create_clip(length_beats)

        clip = clip_slot.clip
        clip.name = str(payload.get("name", "Sensei V2 Groove"))
        clip.loop_start = 0.0
        clip.loop_end = length_beats
        clip.end_marker = length_beats

        specs = []
        for note in payload.get("notes", []):
            specs.append({
                "pitch": int(note["pitch"]),
                "start_time": float(note.get("start", note.get("time", 0.0))),
                "duration": max(0.01, float(note["duration"])),
                "velocity": max(1, min(127, int(note.get("velocity", 100)))),
                "mute": False,
            })

        # Live 11 replaced set_notes/remove_notes with add_new_notes and
        # remove_notes_extended, and warns -- with a modal that blocks the
        # surface until someone clicks -- whenever a script still uses the old
        # pair, because the old pair drops MPE, probability and release
        # velocity. The new pair is used wherever it exists; the old one stays
        # only for a Live that has nothing else.
        if hasattr(clip, "remove_notes_extended") and hasattr(clip, "add_new_notes"):
            clip.remove_notes_extended(0, 128, 0.0, length_beats)
            clip.add_new_notes(tuple(specs))
            api = "live11_extended"
        else:
            if hasattr(clip, "remove_notes"):
                clip.remove_notes(0.0, 0, length_beats, 128)
            clip.set_notes(tuple((n["pitch"], n["start_time"], n["duration"], n["velocity"], False)
                                 for n in specs))
            api = "legacy"
        return {
            "track": track.name,
            "clip_name": clip.name,
            "length_beats": length_beats,
            "note_count": len(specs),
            "note_api": api,
        }

    def _target_clip_slot(self, track):
        highlighted = self.song().view.highlighted_clip_slot
        if highlighted in tuple(track.clip_slots):
            return highlighted

        for clip_slot in track.clip_slots:
            if not clip_slot.has_clip:
                return clip_slot
        return track.clip_slots[0]

    def _ensure_dirs(self):
        for directory in (REQUEST_DIR, DONE_DIR, ERROR_DIR, STATE_DIR):
            if not directory.exists():
                os.makedirs(str(directory))
