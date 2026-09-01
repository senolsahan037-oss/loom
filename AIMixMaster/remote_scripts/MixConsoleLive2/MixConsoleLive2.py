from _Framework.ControlSurface import ControlSurface
import math
import json
import os
import time
import unicodedata
import hashlib
import socket


import os
# Derived from the home directory; a hardcoded absolute path is meaningless
# on another machine and in a published repository.
REQUEST_PATH = os.path.expanduser(
    "~/Desktop/Loom/AIMixMaster/reports/live_meter_active_session.json"
)
SCHEMA_VERSION = "1.0"
SAMPLE_INTERVAL_SECONDS = 0.1


def _normalize(value):
    return "".join(ch for ch in unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower() if ch.isalnum())


def _safe_value(value, default=None):
    try:
        return value.value if hasattr(value, "value") else value
    except Exception:
        return default


def _safe_text(value, default=None):
    try:
        return str(value)
    except Exception:
        return default


class MixConsoleLive2(ControlSurface):
    def __init__(self, c_instance):
        super(MixConsoleLive2, self).__init__(c_instance)
        self._task = None
        self._peak_by_track = {}
        self._live_session = None
        self._last_playing = None
        self._last_sample_monotonic = 0.0
        self._last_flush_monotonic = 0.0
        self._session_tracks = {}
        self._initial_audio_snapshot = None
        # Probe telemetry is paused. An unbound non-blocking socket preserves
        # the independent meter-sampling path without claiming UDP port 49200.
        self._probe_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._probe_socket.setblocking(False)
        self.log_message("MIX_CONSOLE_LIVE2: loaded peak hold")
        self._start()

    def _start(self):
        self._task = self._tasks.add(self._poll)

    def _meter_to_db(self, value):
        try:
            value = float(value)
        except Exception:
            return "-inf"
        if value <= 0.000001:
            return "-inf"
        return "%.2fdB" % (20.0 * math.log10(value))

    def _load_session_request(self):
        try:
            with open(REQUEST_PATH, "r") as source:
                request = json.load(source)
            if request.get("schema_version") != SCHEMA_VERSION:
                return None
            if not request.get("session_id") or not request.get("log_path"):
                return None
            return request
        except Exception:
            return None

    def _append_event(self, event):
        session = self._live_session
        if not session:
            return
        try:
            event["schema_version"] = SCHEMA_VERSION
            event["session_id"] = session["session_id"]
            event["monotonic_timestamp"] = time.monotonic()
            with open(session["log_path"], "a") as target:
                target.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                if time.monotonic() - self._last_flush_monotonic >= 1.0:
                    target.flush()
                    self._last_flush_monotonic = time.monotonic()
        except Exception as exc:
            try:
                self.log_message("LIVE_METER_WRITE_ERROR: %s" % str(exc))
            except Exception:
                pass

    def _activate_session_if_safe(self, request, song):
        if self._live_session or not request:
            return
        try:
            if os.path.exists(request["log_path"]):
                with open(request["log_path"], "r") as source:
                    if request["session_id"] in source.read():
                        self.log_message("LIVE_METER: refusing duplicate session_id")
                        return
            self._live_session = request
            self._live_session["telemetry_counters"] = {"unknown_uuid": 0, "received": 0, "stale": 0, "dropped": 0}
            self._live_session["telemetry_aggregate"] = {}
            self._last_playing = bool(song.is_playing)
        except Exception as exc:
            self.log_message("LIVE_METER_SESSION_ERROR: %s" % str(exc))

    def _snapshot_audio_mapping(self, song):
        """Ignore group/MIDI differences; require the audio-track sequence to match."""
        expected = [item for item in self._live_session.get("expected_tracks", []) if item.get("track_type") == "AudioTrack"]
        actual = []
        for index, track in enumerate(list(song.tracks)):
            if getattr(track, "is_foldable", False):
                continue
            try:
                track.output_meter_left
                actual.append((index, track))
            except Exception:
                # MIDI-output/no-meter tracks are unresolved, not fatal.
                pass
        if len(actual) != len(expected):
            return None, "Audio track count changed"
        mapping = {}
        for (index, track), item in zip(actual, expected):
            expected_name = item.get("live_track_name", item.get("track_name", ""))
            if _normalize(track.name) != _normalize(expected_name):
                return None, "Different audio track at audio sequence index %s" % index
            mapping[index] = item
        return mapping, None

    def _audio_track_snapshot(self, song):
        """Canonical diagnostic identity; groups, returns, and master are excluded."""
        tracks = []
        for index, track in enumerate(list(song.tracks)):
            if getattr(track, "is_foldable", False):
                continue
            try:
                track.output_meter_left
                tracks.append({"live_index": index, "name": track.name, "normalized_name": _normalize(track.name)})
            except Exception:
                pass
        encoded = json.dumps(tracks, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {"audio_track_count": len(tracks), "tracks": tracks, "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}

    def _track_gain_context(self, track, index):
        """Read-only metadata needed to interpret a post-mixer Live meter."""
        mixer = track.mixer_device
        volume = mixer.volume
        pan = getattr(mixer, "panning", None)
        devices = []
        for device in list(getattr(track, "devices", [])):
            devices.append({"name": _safe_text(getattr(device, "name", None)), "class_name": _safe_text(getattr(device, "class_name", None)), "is_active": bool(getattr(device, "is_active", True))})
        parent = getattr(track, "group_track", None)
        return {
            "track_index": index, "track_name": track.name,
            "fader_raw": _safe_value(volume), "fader_display": _safe_text(volume),
            "fader_automation_state": _safe_value(getattr(volume, "automation_state", None)),
            "pan_raw": _safe_value(pan), "pan_display": _safe_text(pan),
            "pan_automation_state": _safe_value(getattr(pan, "automation_state", None)),
            "mute": bool(track.mute), "solo": bool(track.solo),
            "track_activator": bool(track.mute == False),
            "parent_group_name": getattr(parent, "name", None) if parent else None,
            "output_routing_type": _safe_text(getattr(track, "output_routing_type", None)),
            "output_routing_channel": _safe_text(getattr(track, "output_routing_channel", None)),
            "input_routing_type": _safe_text(getattr(track, "input_routing_type", None)),
            "input_routing_channel": _safe_text(getattr(track, "input_routing_channel", None)),
            "crossfade_assign": _safe_value(getattr(mixer, "crossfade_assign", None)), "devices": devices,
        }

    def _snapshot_diff(self, first, second):
        first_names = [item["normalized_name"] for item in first["tracks"]]
        second_names = [item["normalized_name"] for item in second["tracks"]]
        return {"audio_track_count_changed": first["audio_track_count"] != second["audio_track_count"], "first_sha256": first["sha256"], "second_sha256": second["sha256"], "first_tracks": first["tracks"], "second_tracks": second["tracks"], "added_normalized_names": [name for name in second_names if name not in first_names], "removed_normalized_names": [name for name in first_names if name not in second_names]}

    def _write_live_sample(self, song):
        # Deliberately disabled: Live output meters are not telemetry evidence.
        return

    def _collect_probe_frames(self, song):
        """Append only Probe-originated measurements joined by explicit UUID map."""
        if not self._probe_socket or not self._live_session or not bool(song.is_playing):
            return
        mapping = self._live_session.get("probe_track_map", {})
        while True:
            try:
                payload, _ = self._probe_socket.recvfrom(8192)
            except socket.error:
                break
            try:
                frame = json.loads(payload.decode("utf-8"))
                item = mapping.get(frame.get("probe_id"))
                if not item:
                    self._live_session["telemetry_counters"]["unknown_uuid"] += 1
                    continue
                aggregate = self._live_session["telemetry_aggregate"].setdefault(frame["probe_id"], {"track_id": item["track_id"], "track_name": item.get("track_name"), "frames": 0, "max_sample_peak_left": 0.0, "max_sample_peak_right": 0.0, "sum_rms_left": 0.0, "sum_rms_right": 0.0, "first_timestamp": None, "last_timestamp": None, "clipped_left": 0, "clipped_right": 0})
                timestamp = frame["timestamp_monotonic_ms"]
                if aggregate["last_timestamp"] is not None and timestamp <= aggregate["last_timestamp"]:
                    self._live_session["telemetry_counters"]["stale"] += 1
                    continue
                aggregate["frames"] += 1
                aggregate["max_sample_peak_left"] = max(aggregate["max_sample_peak_left"], frame["sample_peak_left"])
                aggregate["max_sample_peak_right"] = max(aggregate["max_sample_peak_right"], frame["sample_peak_right"])
                aggregate["sum_rms_left"] += frame["rms_left"]; aggregate["sum_rms_right"] += frame["rms_right"]
                aggregate["first_timestamp"] = timestamp if aggregate["first_timestamp"] is None else aggregate["first_timestamp"]
                aggregate["last_timestamp"] = timestamp; aggregate["clipped_left"] = frame["clipped_left"]; aggregate["clipped_right"] = frame["clipped_right"]
                self._live_session["telemetry_counters"]["received"] += 1
                self._append_event({
                    "event": "probe_measurement_sample", "source": "AIMixMasterProbe",
                    "track_id": item["track_id"], "track_index": item.get("track_index"),
                    "track_name": item.get("track_name"), "probe_id": frame["probe_id"],
                    "probe_timestamp_monotonic_ms": frame["timestamp_monotonic_ms"],
                    "sample_peak_left": frame["sample_peak_left"], "sample_peak_right": frame["sample_peak_right"],
                    "rms_left": frame["rms_left"], "rms_right": frame["rms_right"],
                    "true_peak_left": frame.get("true_peak_left"), "true_peak_right": frame.get("true_peak_right"),
                    "clipped_left": frame["clipped_left"], "clipped_right": frame["clipped_right"],
                })
            except Exception as exc:
                self.log_message("PROBE_TELEMETRY_ERROR: %s" % str(exc))
        now = time.monotonic()
        if now - self._last_sample_monotonic < SAMPLE_INTERVAL_SECONDS:
            return
        self._last_sample_monotonic = now
        for index, track in enumerate(list(song.tracks)):
            if index not in self._session_tracks:
                continue
            item = self._session_tracks[index]
            self._append_event({
                "event": "measurement_sample",
                "transport_song_time": song.current_song_time,
                "is_playing": bool(song.is_playing),
                "track_index": index,
                "track_id": item.get("track_id"),
                "track_name": track.name,
                "track_type": item.get("track_type"),
                "meter_left": float(track.output_meter_left),
                "meter_right": float(track.output_meter_right),
                "meter_unit": "unknown_raw_live_meter",
                "track_volume": float(track.mixer_device.volume.value),
                "track_volume_display": _safe_text(track.mixer_device.volume),
                "pan": _safe_value(getattr(track.mixer_device, "panning", None)),
                "pan_display": _safe_text(getattr(track.mixer_device, "panning", None)),
                "track_activator": bool(track.mute == False),
                "solo": bool(track.solo),
                "mute": bool(track.mute),
                "parent_group": self._live_session.get("gain_context", {}).get(str(index), {}).get("parent_group_name"),
                "gain_context": self._live_session.get("gain_context", {}).get(str(index)),
                "warnings": ["meter tap point and unit are uncalibrated"],
            })

    def _live_measurement_tick(self, song):
        request = self._load_session_request()
        self._activate_session_if_safe(request, song)
        if not self._live_session:
            return
        playing = bool(song.is_playing)
        if playing and not self._last_playing:
            self._session_tracks, reason = self._snapshot_audio_mapping(song)
            if self._session_tracks is None:
                self._append_event({"event": "session_aborted", "warnings": [reason]})
                self._live_session = None
                return
            self._initial_audio_snapshot = self._audio_track_snapshot(song)
            self._live_session["gain_context"] = {
                str(index): self._track_gain_context(track, index)
                for index, track in enumerate(list(song.tracks)) if index in self._session_tracks
            }
            unresolved = [item.get("track_name") for item in self._live_session.get("expected_tracks", []) if item.get("track_type") != "AudioTrack"]
            self._append_event({"event": "session_started", "transport_song_time": song.current_song_time, "is_playing": playing, "requested_sample_rate_hz": 10.0, "audio_track_snapshot": self._initial_audio_snapshot, "identity_comparison": "canonical audio track list JSON SHA-256; group/return/master excluded", "warnings": ["Unresolved/non-meter tracks: " + ", ".join(unresolved)] if unresolved else []})
        if playing:
            current_snapshot = self._audio_track_snapshot(song)
            if self._initial_audio_snapshot and current_snapshot["audio_track_count"] != self._initial_audio_snapshot["audio_track_count"]:
                self._append_event({"event": "session_aborted", "warnings": ["Audio track count changed during session"], "snapshot_diff": self._snapshot_diff(self._initial_audio_snapshot, current_snapshot)})
                self._live_session = None
                return
            self._collect_probe_frames(song)
        if self._last_playing and not playing:
            final_snapshot = self._audio_track_snapshot(song)
            self._append_event({"event": "track_snapshot_comparison", "snapshot_diff": self._snapshot_diff(self._initial_audio_snapshot, final_snapshot) if self._initial_audio_snapshot else None})
            aggregates = []
            for probe_id, data in self._live_session["telemetry_aggregate"].items():
                frames = data["frames"]
                aggregates.append({"probe_id": probe_id, "track_id": data["track_id"], "track_name": data["track_name"], "frame_count": frames, "max_sample_peak_left": data["max_sample_peak_left"], "max_sample_peak_right": data["max_sample_peak_right"], "max_sample_peak_combined": max(data["max_sample_peak_left"], data["max_sample_peak_right"]), "accumulated_rms_left": data["sum_rms_left"] / frames if frames else None, "accumulated_rms_right": data["sum_rms_right"] / frames if frames else None, "first_timestamp": data["first_timestamp"], "last_timestamp": data["last_timestamp"], "clipped_left": data["clipped_left"], "clipped_right": data["clipped_right"], "true_peak_status": "unsupported", "data_completeness": "complete" if frames >= 10 else "insufficient_frames"})
            self._append_event({"event": "session_completed", "transport_song_time": song.current_song_time, "is_playing": playing, "probe_aggregates": aggregates, "telemetry_counters": self._live_session["telemetry_counters"]})
            self._live_session = None
        self._last_playing = playing

    def _poll(self, *args):
        song = self.song()
        self._live_measurement_tick(song)
        tracks = list(song.tracks) + list(song.return_tracks) + [song.master_track]
        self.log_message("==== MIX CONSOLE LIVE METER ====")
        snapshot_tracks = {}
        for track in tracks:
            try:
                name = track.name
                left = track.output_meter_left
                right = track.output_meter_right
                peak = max(left, right)
                previous_peak = self._peak_by_track.get(name, 0.0)
                if peak > previous_peak:
                    self._peak_by_track[name] = peak
                peak_hold = self._peak_by_track.get(name, 0.0)
                current_db = self._meter_to_db(peak)
                peak_hold_db = self._meter_to_db(peak_hold)
                left_db = self._meter_to_db(left)
                right_db = self._meter_to_db(right)
                self.log_message("%s | LIVE:%s | PEAK:%s | L:%s | R:%s | rawL:%.4f | rawR:%.4f" % (name, current_db, peak_hold_db, left_db, right_db, left, right))
                snapshot_tracks[name] = {"live": peak_hold_db, "current": current_db, "left": left_db, "right": right_db}
            except Exception as e:
                self.log_message("METER_ERROR: %s" % str(e))
        try:
            payload = {"timestamp": int(time.time()), "tracks": snapshot_tracks}
            tmp_path = "/tmp/mixconsole_live.json.tmp"
            final_path = "/tmp/mixconsole_live.json"
            with open(tmp_path, "w") as jf:
                json.dump(payload, jf, ensure_ascii=False)
            os.replace(tmp_path, final_path)
        except Exception as e:
            try:
                self.log_message("MIX_CONSOLE_LIVE2: failed to write JSON snapshot: %s" % str(e))
            except Exception:
                pass
        return 1.0
