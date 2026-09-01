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


def create_instance(c_instance):
    return Loom(c_instance)


class Loom(ControlSurface):
    def __init__(self, c_instance):
        super().__init__(c_instance)
        self._tick_count = 0
        self._ensure_dirs()
        self.log_message("Loom control surface loaded (%s)" % bridge_ops.SCHEMA_VERSION)
        self._register_timer_callback(self._on_timer)

    def disconnect(self):
        try:
            self._unregister_timer_callback(self._on_timer)
        finally:
            super().disconnect()

    def _on_timer(self):
        self._tick_count += 1
        if self._tick_count % REQUEST_EVERY_TICKS == 0:
            self._process_next_request()
        if self._tick_count % STATE_EVERY_TICKS == 0:
            self._dump_state()

    # --- durum yayini -----------------------------------------------------

    def _dump_state(self):
        try:
            state = bridge_ops.capture_state(self.song())
            state["captured_at"] = time.time()
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

        notes = []
        for note in payload.get("notes", []):
            notes.append(
                (
                    int(note["pitch"]),
                    float(note["start"]),
                    max(0.01, float(note["duration"])),
                    max(1, min(127, int(note["velocity"]))),
                    False,
                )
            )

        if hasattr(clip, "remove_notes"):
            clip.remove_notes(0.0, 0, length_beats, 128)
        clip.set_notes(tuple(notes))
        return {
            "track": track.name,
            "clip_name": clip.name,
            "length_beats": length_beats,
            "note_count": len(notes),
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
