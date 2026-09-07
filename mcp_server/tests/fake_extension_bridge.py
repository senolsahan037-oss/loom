"""A stand-in for the Loom extension's file bridge, for tests only.

It speaks the same protocol the extension does (requests/ -> done/ or
errors/, state/live_state.json, expiry, target_session, idempotency replay,
atomic outcome files) against a tiny in-memory set, so the MCP server can be
exercised end to end in a temporary directory with no Live, no control
surface and no user data. It is not the extension: whatever passes here
proves the MCP side of the contract, not Live's.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SURFACE_VERSION = "loom-extension/test"
BRIDGE_PROTOCOL = "loom.bridge/3"
CAPABILITIES = {
    "transport": False, "meters": False, "time_signature": False, "key_write": False, "preset_load": False,
    "native_device_insert": True, "arrangement_clips": True, "session_clips": True, "tracks": True,
    "locators": True, "mixer": True, "device_parameters": True, "audio_import": True, "render_pre_fx": True,
    "tempo": True, "drum_pads": True, "drum_kit_build": True, "journal_import": True, "recording": False,
}
UNSUPPORTED = {"transport", "set_key", "capture_prepare", "capture_route", "capture_arm", "capture_record", "capture_stop", "capture_result"}


class FakeSet:
    def __init__(self) -> None:
        self.tempo = 120.0
        self.tracks: list[dict[str, Any]] = []
        self.cues: list[dict[str, Any]] = []
        self.applied: list[str] = []  # ops in the order they mutated the set

    def add_track(self, name: str, *, devices: list[str] | None = None, pad_notes: list[int] | None = None, is_midi: bool = True,
                  pad_devices: dict[int, list[str]] | None = None) -> dict[str, Any]:
        track = {"name": name, "is_midi": is_midi, "devices": list(devices or []), "pad_notes": pad_notes,
                 "pad_devices": dict(pad_devices or {}), "arrangement": [], "slots": [None] * 8}
        self.tracks.append(track)
        return track

    def track(self, name: Any) -> dict[str, Any]:
        matches = [t for t in self.tracks if t["name"] == name]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one track named {name!r}, found {len(matches)}")
        return matches[0]

    def state(self) -> dict[str, Any]:
        return {
            "tempo": self.tempo, "is_playing": None, "signature_numerator": None, "signature_denominator": None,
            "track_count": len(self.tracks),
            "tracks": [{"index": i, "name": t["name"], "has_midi_input": t["is_midi"],
                        "devices": [{"name": d, "class_name": d} for d in t["devices"]]} for i, t in enumerate(self.tracks)],
            "cue_points": [dict(c) for c in self.cues],
        }


class FakeExtensionBridge:
    """Runs a consumer thread over `root`. Knobs for the failure modes the MCP
    must handle: `swallow` consumes requests without ever answering,
    `corrupt_results` writes answers without an id, `delay` holds each
    request before answering, `session_id` names the Live session."""

    def __init__(self, root: Path, live: FakeSet | None = None, *, session_id: str | None = None,
                 delay: float = 0.0, swallow: bool = False, corrupt_results: bool = False, poll: float = 0.05,
                 hold_before_apply: bool = False, crash_after_apply: bool = False,
                 protocol: str | None = BRIDGE_PROTOCOL, publish_session: bool = True) -> None:
        self.root = Path(root)
        self.live = live or FakeSet()
        self.session_id = session_id or uuid.uuid4().hex[:8]
        # What the state file claims about the queue protocol. `protocol=None`
        # and `publish_session=False` imitate an older extension.
        self.protocol = protocol
        self.publish_session = publish_session
        self.delay = delay
        self.swallow = swallow
        self.corrupt_results = corrupt_results
        self.poll = poll
        # Same file lifecycle as the extension: a request is CLAIMED by an
        # atomic rename into processing/ before it is read or applied; the
        # outcome is written, then the claimed file is removed. hold_before_apply
        # parks a claimed request until release() -- the "mutation in flight"
        # state a timeout can race with. crash_after_apply applies the mutation
        # and then stops dead: no outcome, no journal update, claimed file left.
        self.hold_before_apply = hold_before_apply
        self.crash_after_apply = crash_after_apply
        self.release_event = threading.Event()
        self.crashed = threading.Event()
        self.seen: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        for sub in ("requests", "processing", "done", "errors", "state"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # --- lifecycle -----------------------------------------------------------
    def start(self) -> "FakeExtensionBridge":
        self.publish_state()
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def release(self) -> None:
        self.release_event.set()

    # --- journal: the fake keeps ONE json document; the extension keeps a JSONL
    # file plus a marker (see bridge.ts). Same rules: started before mutation,
    # replay on the same key + content + session, indeterminate wins. ---
    def _journal_path(self) -> Path:
        return self.root / "state" / "journal.jsonl"

    def _read_journal(self) -> list[dict[str, Any]]:
        """Last line per key, like the extension's readJournal."""
        by_key: dict[str, dict[str, Any]] = {}
        try:
            for line in self._journal_path().read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    by_key[entry["key"]] = entry
        except FileNotFoundError:
            return []
        return list(by_key.values())

    def _journal_put(self, entry: dict[str, Any]) -> None:
        self._journal_path().parent.mkdir(parents=True, exist_ok=True)
        with self._journal_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def recover(self) -> list[str]:
        """What the extension does at start: a claimed request without an
        outcome was interrupted mid-way; it is answered indeterminate, never
        re-applied."""
        answered = []
        for claimed in sorted((self.root / "processing").glob("*.json")):
            if (self.root / "done" / claimed.name).exists() or (self.root / "errors" / claimed.name).exists():
                claimed.unlink()
                continue
            try:
                payload = json.loads(claimed.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                payload = {}
            record = {**payload, "status": "indeterminate", "error": "BridgeError: indeterminate_after_restart: the host stopped while this request was being applied; not re-applied",
                      "outcome": {"kind": "indeterminate", "code": "indeterminate_after_restart", "applied": None, "verified": False},
                      "schema_version": "sensei.bridge.v2", "surface_version": SURFACE_VERSION, "completed_at": time.time()}
            self._write_atomic(self.root / "errors" / claimed.name, record)
            claimed.unlink()
            answered.append(claimed.name)
        return answered

    def __enter__(self) -> "FakeExtensionBridge":
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    # --- protocol ------------------------------------------------------------
    def _write_atomic(self, path: Path, body: dict[str, Any]) -> None:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(body, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def publish_state(self, captured_at: float | None = None) -> None:
        state = {"schema_version": "sensei.bridge.v2", "surface_version": SURFACE_VERSION,
                 "capabilities": CAPABILITIES, **self.live.state(), "captured_at": captured_at if captured_at is not None else time.time()}
        if self.protocol:
            state["bridge_protocol"] = self.protocol
        if self.publish_session:
            state["session_id"] = self.session_id
        self._write_atomic(self.root / "state" / "live_state.json", state)

    def _loop(self) -> None:
        while not self._stop.is_set():
            pending = sorted((self.root / "requests").glob("*.json"))
            if pending:
                self.process(pending[0])
            else:
                time.sleep(self.poll)

    @staticmethod
    def payload_hash(payload: dict[str, Any]) -> str:
        volatile = {"id", "created_at", "issued_at", "expires_at", "target_session", "idempotency_key", "schema_version"}
        import hashlib
        body = json.dumps({k: v for k, v in payload.items() if k not in volatile}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

    def process(self, request_path: Path) -> None:
        if self.crashed.is_set():
            return
        claimed = self.root / "processing" / request_path.name
        try:
            os.rename(request_path, claimed)  # the claim: atomic, exactly one winner against a withdrawal
        except FileNotFoundError:
            return  # withdrawn first; nothing was applied
        try:
            payload = json.loads(claimed.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        self.seen.append(str(payload.get("id")))
        if self.swallow:
            claimed.unlink(missing_ok=True)  # picked up, never answered
            return
        if self.delay:
            time.sleep(self.delay)
        record: dict[str, Any]
        refusal = None
        now = time.time()
        if isinstance(payload.get("expires_at"), (int, float)) and payload["expires_at"] < now:
            refusal = "expired_request: not applied"
        elif payload.get("target_session") and payload["target_session"] != self.session_id:
            refusal = f"session_mismatch: issued for {payload['target_session']}, this is {self.session_id}; not applied"
        op = payload.get("op") or "write_clip"
        key = f"key:{payload['idempotency_key']}" if payload.get("idempotency_key") else f"id:{payload.get('id')}"
        digest = self.payload_hash(payload)
        earlier = next((e for e in self._read_journal() if e.get("key") == key), None)
        def refused(code: str, message: str) -> dict[str, Any]:
            return {**payload, "status": "error", "error": f"BridgeError: {message}",
                    "outcome": {"kind": "refused", "code": code, "applied": False, "verified": True}}
        if refusal:
            record = refused(refusal.split(":")[0], refusal)
        elif earlier is not None and earlier.get("status") == "started":
            record = {**payload, "status": "indeterminate", "error": f"BridgeError: indeterminate_earlier_attempt: request {earlier.get('request_id')} with this key started and recorded no outcome; not applied again",
                      "outcome": {"kind": "indeterminate", "code": "indeterminate_earlier_attempt", "applied": None, "verified": False,
                                  "earlier_request_id": earlier.get("request_id"), "earlier_session": earlier.get("session"),
                                  "side_effects": f"{op} may have been applied by the earlier attempt", "next_step": "read the state before retrying"}}
        elif earlier is not None and earlier.get("session") != self.session_id:
            record = refused("replay_refused_other_session", "replay_refused_other_session: this key was used in another Live session; not replayed and not applied")
        elif earlier is not None and earlier.get("payload_hash") != digest:
            record = refused("idempotency_conflict", "idempotency_conflict: same key, different request contents; not applied")
        elif earlier is not None:
            record = {**earlier["record"], "id": payload.get("id"), "replayed": True}
        else:
            # intent first: if the host dies after this line the key reads as started
            self._journal_put({"key": key, "status": "started", "request_id": payload.get("id"), "session": self.session_id, "payload_hash": digest})
            if self.hold_before_apply:
                self.release_event.wait()  # a slow mutation, already committed to
            try:
                result = self.apply(payload)
                record = {**payload, "status": "ok", "result": result, "outcome": {"kind": "applied", "code": "applied", "applied": True, "verified": True}}
            except Exception as error:  # noqa: BLE001
                # The fake validates before it mutates, so its errors are refusals.
                record = refused("fake_refused", f"{type(error).__name__}: {error}")
            if self.crash_after_apply:
                self.crashed.set()  # the mutation happened; nothing else does
                self.publish_state()
                return
            self._journal_put({"key": key, "status": record["status"], "request_id": payload.get("id"), "session": self.session_id, "payload_hash": digest, "record": record})
        record.update({"schema_version": "sensei.bridge.v2", "surface_version": SURFACE_VERSION, "bridge_protocol": self.protocol,
                       "session_id": self.session_id, "completed_at": time.time()})
        if self.corrupt_results:
            record.pop("id", None)
        destination = "done" if record["status"] == "ok" else "errors"
        self._write_atomic(self.root / destination / request_path.name, record)
        claimed.unlink(missing_ok=True)
        self.publish_state()

    # --- operations ------------------------------------------------------------
    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        op = payload.get("op") or "write_clip"
        if op in UNSUPPORTED:
            raise ValueError(f"unsupported_in_extension: {op}")
        live = self.live
        if op == "get_state":
            return live.state()
        if op == "set_tempo":
            before, live.tempo = live.tempo, float(payload["bpm"])
            live.applied.append(op)
            return {"before": before, "after": live.tempo}
        if op == "create_midi_track":
            name = str(payload.get("name") or "").strip()
            existing = [t for t in live.tracks if t["name"] == name]
            family = str(payload.get("instrument_family") or "").strip()
            if existing and (existing[0]["devices"] or not family):
                return {"created": False, "adopted": True, "name": name, "instrument": "kept: track already existed" if family else "skipped"}
            adopted = bool(existing)
            track = existing[0] if existing else live.add_track(name)
            live.applied.append(op)
            if family in ("Drum Rack", "Operator", "Wavetable", "Simpler"):
                track["devices"].append(family)
                if family == "Drum Rack":
                    track["pad_notes"] = []  # like Live: a freshly inserted Drum Rack has no chains
                instrument = f"inserted: {family}" + (" (into the adopted track's empty chain)" if adopted else "")
            elif family:
                instrument = f"not_loadable_in_extension: {family} (no native device by that name)"
            else:
                instrument = "skipped"
            return {"created": not adopted, "adopted": adopted, "name": name, "index": live.tracks.index(track), "instrument": instrument}
        if op == "journal_import":
            imported = 0
            existing = {e.get("key") for e in self._read_journal()}
            for entry in payload.get("entries") or []:
                if not isinstance(entry, dict) or entry.get("session") == self.session_id or entry.get("key") in existing:
                    continue
                self._journal_put({**entry, "imported_from": payload.get("source")})
                imported += 1
            return {"imported": imported, "source": payload.get("source")}
        if op == "build_drum_kit":
            track = live.track(payload.get("track"))
            if "Drum Rack" not in track["devices"]:
                raise ValueError("expected exactly one Drum Rack, found 0")
            pads = track.get("pad_notes") or []
            for pad in payload.get("pads") or []:
                if int(pad["note"]) in pads:
                    raise ValueError(f"pads [{pad['note']}] already have a chain")
                pads.append(int(pad["note"]))
            track["pad_notes"] = sorted(pads)
            live.applied.append(op)
            return {"track": track["name"], "pad_notes": track["pad_notes"], "verified": True}
        if op == "drum_pads":
            track = live.track(payload.get("track"))
            pads = sorted(set(track.get("pad_notes") or []))
            racks = [{"device": next((d for d in track["devices"] if "Drum" in d or "Kit" in d), "Drum Rack"), "pad_notes": pads,
                      "chains": [{"note": n, "devices": list((track.get("pad_devices") or {}).get(n) or []),
                                  "device_names": list((track.get("pad_device_names") or {}).get(n) or (track.get("pad_devices") or {}).get(n) or [])} for n in pads]}] if pads or track["devices"] else []
            return {"track": track["name"], "drum_racks": racks, "pad_notes": pads, "verified": bool(pads),
                    "reason": None if pads else "no Drum Rack on this track"}
        if op == "write_arrangement_clip":
            track = live.track(payload.get("track"))
            start, length = float(payload["start_beat"]), float(payload["length_beats"])
            notes = payload.get("notes") or []
            for clip in track["arrangement"]:
                if clip["start"] < start + length and clip["end"] > start:
                    raise ValueError(f"beats {clip['start']}-{clip['end']} hold clip {clip['name']!r} that was not written by Loom")
            track["arrangement"].append({"name": payload.get("name") or "Loom", "start": start, "end": start + length, "notes": notes})
            live.applied.append(op)
            return {"track": track["name"], "note_count": len(notes), "verified_note_count": len(notes), "verified_notes_match": True, "replaced": 0}
        if op == "write_clip":
            track = live.track(payload.get("track")) if payload.get("track") else next(t for t in live.tracks if t["is_midi"])
            slot = int(payload["slot"]) if payload.get("slot") is not None else track["slots"].index(None)
            if track["slots"][slot] is not None:
                raise ValueError(f"clip slot {slot} is occupied")
            notes = payload.get("notes") or []
            track["slots"][slot] = {"name": payload.get("name"), "notes": notes}
            live.applied.append(op)
            return {"track": track["name"], "slot": slot, "note_count": len(notes), "verified_note_count": len(notes), "verified_notes_match": True}
        if op == "create_locator":
            cue = {"name": payload.get("name"), "time": float(payload["beat"])}
            live.cues.append(cue)
            live.applied.append(op)
            return cue
        raise ValueError(f"unknown op {op!r}")
