"""The MCP's side of the one Loom connection: the extension's file bridge.

Live 12.4 beta runs the Loom extension (extension/),
which publishes a file bridge inside its own storage directory. Every Live
read and write goes through that bridge and nothing else: there is no
control-surface fallback and no silent switching between endpoints.

What this module owns:
  * resolving the ONE bridge root a call talks to (or refusing to name one)
  * the protocol gate: no mutation is written unless the bridge publishes a
    fresh state, a session id and a queue protocol this client knows
  * writing one request and reading back its structured outcome
  * the public status vocabulary every Live-touching tool answers with

Statuses (``response["status"]``):
  OK                 applied and verified by the extension
  REFUSED_IN_LIVE    the extension refused before touching Live (validation,
                     clip protection, expired/foreign request, journal rules)
  FAILED_IN_LIVE     attempted, failed, previous content restored
  INDETERMINATE      Live may or may not have applied it: the request was
                     claimed and no outcome arrived, an earlier attempt with
                     the same key never recorded one, or a restore failed
  NOT_CONSUMED       nobody picked the request up in time; it was withdrawn
  INVALID_RESULT     an outcome file that cannot be trusted (wrong id, no status)
  QUEUED             wait_seconds=0: written, not verified
  UNSUPPORTED_BY_SDK the Extensions SDK has no API for the op (never sent)
  PRESET_LOAD_REQUIRED  the plan names a real preset file the SDK cannot load and no
                     verified load path reached the open set; the track's writes are
                     blocked -- never a native stand-in, never a Simpler rebuild
  PRESET_LANDED_ELSEWHERE  a preset handed to Live landed on another track; no MIDI
                     goes anywhere near it
  NO_EXTENSION_BRIDGE / AMBIGUOUS_BRIDGE   no single bridge root to talk to
  NO_STATE / STALE_STATE                   the bridge has not published, or not recently
  UPGRADE_REQUIRED / PROTOCOL_MISMATCH     the running extension is older / other than this client
  STALE_SESSION      the Live session changed between resolving the target and sending

Every answer also carries ``outcome`` -- {kind, code, applied, verified,
side_effects, next_step} -- so a caller never has to parse the error text to
learn whether Live was touched.

LOOM_BRIDGE_ROOT pins the root for a test that runs a fake consumer in a
temporary directory; it is never pointed at a user's session.
"""
from __future__ import annotations

import datetime
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

EXTENSIONS_DATA_DIR = Path.home() / "Library" / "Application Support" / "Ableton" / "Extensions Data"
# The extension id Live derives from manifest.json (author "SubverseLab", name
# "Loom"). Only this id is ever asked to mutate.
LOOM_EXTENSION_IDS = ("subverselab.loom",)
# Ids this extension shipped under before 2026-09-06. Their bridges are read
# for diagnosis and for carrying the replay journal over; they are never
# mutated, and a fresh one is reported as superseded so the user removes it.
LEGACY_EXTENSION_IDS = ("loom.sensei-midi-writer", "ai-producer.sensei-midi-writer")
BRIDGE_ROOT_ENV = "LOOM_BRIDGE_ROOT"
BRIDGE_ROOT = Path(os.environ[BRIDGE_ROOT_ENV]).expanduser() if os.environ.get(BRIDGE_ROOT_ENV) else None
STATE_FRESH_SECONDS = 10.0
# A queued request nobody picked up by then must never be applied later.
REQUEST_TTL_SECONDS = 60.0
# After a timeout, how long the outcome of a request Live had already picked
# up is still looked for before "indeterminate" is the honest answer.
INDETERMINATE_GRACE_SECONDS = 2.0
BRIDGE_SCHEMA_VERSION = "sensei.bridge.v2"
# The queue protocols this client can drive. An extension publishing another
# one (or none: 0.1.0 / 0.2.0) is read from but never asked to mutate.
SUPPORTED_BRIDGE_PROTOCOLS = ("loom.bridge/3",)
# Operations that change nothing in Live; they need no protocol gate.
READ_OPS = frozenset({"get_state", "list_device_parameters", "drum_pads"})

# Operations the Extensions SDK has no API for. They are refused here, before
# anything is written to the bridge, naming the capability they would need.
SDK_UNSUPPORTED_OPS = {
    "transport": ("transport", "the SDK exposes no play/stop/continue and no playhead"),
    "set_key": ("key_write", "rootNote and scaleName are read-only in the SDK"),
    "load_preset": ("preset_load", "the SDK inserts native devices with their default preset only; browser presets cannot be loaded"),
    "capture_prepare": ("recording", "record mode, resampling input routing and arming are not in the SDK"),
    "capture_route": ("recording", "record mode, resampling input routing and arming are not in the SDK"),
    "capture_arm": ("recording", "record mode, resampling input routing and arming are not in the SDK"),
    "capture_record": ("recording", "record mode, resampling input routing and arming are not in the SDK"),
    "capture_stop": ("recording", "record mode, resampling input routing and arming are not in the SDK"),
    "capture_result": ("recording", "record mode, resampling input routing and arming are not in the SDK"),
}
# The published capability each supported op needs. A bridge whose state says
# the capability is false gets no request either.
OP_CAPABILITY = {
    "set_tempo": "tempo", "set_mixer": "mixer", "set_device_parameter": "device_parameters",
    "list_device_parameters": "device_parameters", "create_locator": "locators",
    "write_arrangement_clip": "arrangement_clips", "write_clip": "session_clips",
    "create_midi_track": "tracks", "import_audio_clip": "audio_import", "render_pre_fx": "render_pre_fx",
    "drum_pads": "drum_pads", "build_drum_kit": "drum_kit_build", "journal_import": "journal_import",
}

# Answers after which further mutations are pointless or unsafe: the bridge is
# not answering, cannot be named, or what it answered cannot be trusted.
DEAD_STATUSES = frozenset({
    "NOT_CONSUMED", "INDETERMINATE", "INVALID_RESULT", "NO_EXTENSION_BRIDGE", "AMBIGUOUS_BRIDGE", "LEGACY_EXTENSION",
    "NO_STATE", "STALE_STATE", "STALE_SESSION", "UPGRADE_REQUIRED", "PROTOCOL_MISMATCH",
})

# server.py binds its cooperative-cancellation primitives here once, so this
# module needs nothing from it at import time.
_hooks: dict[str, Any] = {"check_cancelled": lambda: None, "report_progress": lambda *a, **k: None, "cancelled_type": ()}


def bind(*, check_cancelled: Callable[[], None], report_progress: Callable[..., None], cancelled_type: type[BaseException]) -> None:
    _hooks.update({"check_cancelled": check_cancelled, "report_progress": report_progress, "cancelled_type": cancelled_type})


class BridgeUnavailable(RuntimeError):
    def __init__(self, status: str, message: str, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.candidates = candidates or []


class BridgeTarget:
    """One resolved Live endpoint. Immutable for the call that resolved it, so
    a request never lands on a different root than the one that was checked."""

    def __init__(self, root: Path, source: str, state: dict[str, Any] | None, age: float | None) -> None:
        self.root = Path(root)
        self.source = source
        self.state = state
        self.age = age

    requests = property(lambda self: self.root / "requests")
    processing = property(lambda self: self.root / "processing")
    done = property(lambda self: self.root / "done")
    errors = property(lambda self: self.root / "errors")
    state_dir = property(lambda self: self.root / "state")
    state_file = property(lambda self: self.root / "state" / "live_state.json")
    session_id = property(lambda self: (self.state or {}).get("session_id"))
    surface_version = property(lambda self: (self.state or {}).get("surface_version"))
    bridge_protocol = property(lambda self: (self.state or {}).get("bridge_protocol"))
    capabilities = property(lambda self: dict((self.state or {}).get("capabilities") or {}))
    journal = property(lambda self: (self.state or {}).get("journal"))
    is_fresh = property(lambda self: self.age is not None and self.age < STATE_FRESH_SECONDS)

    def protocol_report(self) -> dict[str, Any]:
        """Whether this client may send MUTATIONS to the bridge, and why not."""
        if self.state is None:
            return {"compatible": False, "status": "NO_STATE", "published": None, "supported": list(SUPPORTED_BRIDGE_PROTOCOLS),
                    "reason": "the extension has never published state at this root; is Live running with the Loom extension loaded?"}
        published = self.bridge_protocol
        if not published or not self.session_id:
            return {"compatible": False, "status": "UPGRADE_REQUIRED", "published": published, "supported": list(SUPPORTED_BRIDGE_PROTOCOLS),
                    "reason": f"the running extension ({self.surface_version or 'unversioned'}) publishes no queue protocol or session id; "
                              f"it predates {SUPPORTED_BRIDGE_PROTOCOLS[0]} (claim, expiry, session, journal, structured outcome). "
                              "Rebuild and reinstall the Loom extension, then restart Live"}
        if published not in SUPPORTED_BRIDGE_PROTOCOLS:
            return {"compatible": False, "status": "PROTOCOL_MISMATCH", "published": published, "supported": list(SUPPORTED_BRIDGE_PROTOCOLS),
                    "reason": f"the running extension speaks {published}, this MCP speaks {', '.join(SUPPORTED_BRIDGE_PROTOCOLS)}; "
                              "update whichever side is older so both come from the same Loom checkout"}
        if not self.is_fresh:
            return {"compatible": False, "status": "STALE_STATE", "published": published, "supported": list(SUPPORTED_BRIDGE_PROTOCOLS),
                    "reason": f"the bridge's state is {self.age:.0f}s old (limit {STATE_FRESH_SECONDS:.0f}s); Live is probably closed or the extension stopped"}
        return {"compatible": True, "status": "OK", "published": published, "supported": list(SUPPORTED_BRIDGE_PROTOCOLS), "reason": None}

    def describe(self) -> dict[str, Any]:
        return {"root": str(self.root), "source": self.source, "surface_version": self.surface_version,
                "bridge_protocol": self.bridge_protocol, "session_id": self.session_id,
                "state_age_seconds": round(self.age, 1) if self.age is not None else None,
                "is_fresh": self.is_fresh, "capabilities": self.capabilities, "journal": self.journal,
                "protocol": self.protocol_report()}


def _read_state(root: Path) -> tuple[dict[str, Any] | None, float | None]:
    """(state, age in seconds) of what a bridge root last published, or (None, None)."""
    state_file = Path(root) / "state" / "live_state.json"
    if not state_file.exists():
        return None, None
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        captured = float(state.get("captured_at") or 0)
    except Exception:  # noqa: BLE001
        return None, None
    if not isinstance(state, dict):
        return None, None
    return state, (time.time() - captured if captured else None)


def target_from(root: Path, source: str) -> BridgeTarget:
    state, age = _read_state(root)
    return BridgeTarget(Path(root), source, state, age)


def loom_extension_roots() -> list[Path]:
    """Bridge roots that belong to the Loom extension itself. Other extensions'
    storage (older ids, other products) is never a candidate."""
    return [EXTENSIONS_DATA_DIR / ext_id / "bridge" for ext_id in LOOM_EXTENSION_IDS
            if (EXTENSIONS_DATA_DIR / ext_id / "bridge").is_dir()]


def other_bridge_roots() -> list[Path]:
    """Every other bridge-shaped directory under Extensions Data, for the status
    tool only: these are shown as ignored, never talked to."""
    if not EXTENSIONS_DATA_DIR.exists():
        return []
    return [p / "bridge" for p in sorted(EXTENSIONS_DATA_DIR.iterdir())
            if p.name not in LOOM_EXTENSION_IDS and p.name not in LEGACY_EXTENSION_IDS
            and (p / "bridge" / "state" / "live_state.json").exists()]


def legacy_bridge_roots() -> list[tuple[str, Path]]:
    """(extension id, bridge root) for every earlier Loom extension id that
    left a bridge on this machine. Read only."""
    return [(ext_id, EXTENSIONS_DATA_DIR / ext_id / "bridge") for ext_id in LEGACY_EXTENSION_IDS
            if (EXTENSIONS_DATA_DIR / ext_id / "bridge").is_dir()]


def _journal_lines(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return entries
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn line proves nothing either way
        if isinstance(entry, dict) and isinstance(entry.get("key"), str):
            entries.append(entry)
    return entries


def legacy_journal_entries(root: Path) -> list[dict[str, Any]]:
    """The replay journal an earlier extension id left behind, as entries the
    current extension's journal_import accepts: the JSONL journal (last line
    per key wins) and, before that, the v2 single-document journal."""
    by_key: dict[str, dict[str, Any]] = {}
    legacy_doc = Path(root) / "state" / "processed_requests.json"
    if legacy_doc.exists():
        try:
            for raw in (json.loads(legacy_doc.read_text(encoding="utf-8")).get("entries") or []):
                record = raw.get("record") or {}
                by_key[str(raw.get("key"))] = {
                    "key": str(raw.get("key")), "status": raw.get("status") or ("ok" if record.get("status") == "ok" else "error"),
                    "request_id": raw.get("request_id"), "op": record.get("op"), "session": str(raw.get("session") or ""),
                    "payload_hash": str(raw.get("payload_hash") or ""), "at": float(raw.get("started_at") or 0),
                    "result": record.get("result"), "error": record.get("error")}
        except (OSError, ValueError, AttributeError):
            pass
    for entry in _journal_lines(Path(root) / "state" / "journal.jsonl"):
        by_key[entry["key"]] = entry
    return list(by_key.values())


def legacy_report(current: "BridgeTarget | None" = None) -> list[dict[str, Any]]:
    """Every earlier-id bridge: whether it is still alive (a superseded
    extension still installed in Live), what its journal holds, and whether
    that journal has been carried into the current bridge."""
    imported_from: set[str] = set()
    if current is not None:
        imported_from = {str(e.get("imported_from")) for e in _journal_lines(current.state_dir / "journal.jsonl") if e.get("imported_from")}
    report = []
    for ext_id, root in legacy_bridge_roots():
        target = target_from(root, "legacy")
        entries = legacy_journal_entries(root)
        unknown = sum(1 for e in entries if e.get("status") in ("started", "indeterminate"))
        report.append({
            "extension_id": ext_id, "root": str(root), "superseded_by": LOOM_EXTENSION_IDS[0],
            "state": "never_published" if target.state is None else ("fresh" if target.is_fresh else "stale"),
            "surface_version": target.surface_version, "journal_entries": len(entries), "journal_unknown_outcome": unknown,
            "journal_imported": ext_id in imported_from,
            "next_step": ("still running: remove it from Live's Extensions so only the Loom extension (subverselab.loom) stays" if target.is_fresh else None)
                         or ("carry its journal over with live_command op=journal_import" if entries and ext_id not in imported_from else None),
        })
    return report


def resolve_bridge_target() -> BridgeTarget:
    """The one endpoint this call talks to, or a refusal that says why.

    LOOM_BRIDGE_ROOT wins outright (tests). Otherwise exactly one Loom
    extension bridge is expected; with none the caller is told to install the
    extension, with several fresh ones no request is sent anywhere."""
    if BRIDGE_ROOT is not None:
        return target_from(BRIDGE_ROOT, BRIDGE_ROOT_ENV)
    roots = loom_extension_roots()
    if not roots:
        legacy = legacy_bridge_roots()
        if legacy:
            raise BridgeUnavailable(
                "LEGACY_EXTENSION",
                "only an earlier Loom extension id is installed (" + ", ".join(ext_id for ext_id, _ in legacy) + "); "
                "it is never asked to mutate. Add extension/dist/loom.ablx (id subverselab.loom) in Live 12.4 beta, remove the old "
                "extension from Live's Extensions, restart Live; then carry the old journal over with live_command op=journal_import",
                [str(root) for _, root in legacy])
        raise BridgeUnavailable(
            "NO_EXTENSION_BRIDGE",
            "no Loom extension bridge on this machine: install the Loom extension (extension/dist/loom.ablx) in Live 12.4 beta; "
            f"it publishes its bridge under {EXTENSIONS_DATA_DIR / LOOM_EXTENSION_IDS[0] / 'bridge'}")
    targets = [target_from(root, "loom_extension") for root in roots]
    fresh = [target for target in targets if target.is_fresh]
    if len(fresh) > 1:
        raise BridgeUnavailable("AMBIGUOUS_BRIDGE", "more than one fresh Loom extension bridge; refusing to pick one",
                                [str(target.root) for target in fresh])
    if len(fresh) == 1:
        fresh[0].source = "loom_extension_fresh"
        return fresh[0]
    if len(targets) == 1:
        targets[0].source = "loom_extension_stale" if targets[0].state else "loom_extension_no_state"
        return targets[0]
    raise BridgeUnavailable("AMBIGUOUS_BRIDGE", "several Loom extension bridges and none of them fresh; refusing to pick one",
                            [str(target.root) for target in targets])


def unsupported_reason(op: str, target: BridgeTarget) -> dict[str, Any] | None:
    if op in SDK_UNSUPPORTED_OPS:
        capability, why = SDK_UNSUPPORTED_OPS[op]
        return {"capability": capability, "reason": why}
    capability = OP_CAPABILITY.get(op)
    if capability and target.capabilities.get(capability) is False:
        return {"capability": capability, "reason": f"the running extension publishes {capability}=false"}
    return None


def ensure_bridge_dirs(target: BridgeTarget) -> None:
    for d in (target.requests, target.processing, target.done, target.errors, target.state_dir):
        d.mkdir(parents=True, exist_ok=True)


def refusal(status: str, message: str, *, code: str | None = None, op: str | None = None, target: BridgeTarget | None = None, **extra: Any) -> dict[str, Any]:
    """An answer for a request that was never written."""
    return {"status": status, "consumed": False, "error": message, "op": op,
            "outcome": {"kind": "refused", "code": code or status.lower(), "applied": False, "verified": True,
                        "side_effects": "nothing: no request was written to the bridge"},
            "bridge": target.describe() if target is not None else None, **extra}


def mutation_gate(target: BridgeTarget, op: str) -> dict[str, Any] | None:
    """Why `op` may not be sent to `target`, as a refusal answer, or None.

    Reads only need the SDK check; a mutation also needs a compatible, fresh,
    session-bearing bridge -- an older extension does not know expiry, claim
    or journal rules and would apply a stale request."""
    unsupported = unsupported_reason(op, target)
    if unsupported:
        return refusal("UNSUPPORTED_BY_SDK", f"unsupported_by_sdk: {op} needs {unsupported['capability']} -- {unsupported['reason']}",
                       code="unsupported_by_sdk", op=op, target=target, **unsupported)
    if op in READ_OPS:
        return None
    report = target.protocol_report()
    if not report["compatible"]:
        return refusal(report["status"], f"{report['status'].lower()}: {report['reason']}", op=op, target=target, protocol=report)
    return None


def write_atomic(path: Path, body: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, path)


def read_outcome(path: Path, request_id: str) -> tuple[str, dict[str, Any]]:
    """Validate a done/errors record and map it onto the public statuses.
    Anything unreadable, for another id or without a status is INVALID_RESULT,
    never OK. The record's structured `outcome` decides between a refusal, a
    failure and an indeterminate answer; the error text is never parsed."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001
        return "INVALID_RESULT", {"error": f"unreadable result file: {error}"}
    if not isinstance(record, dict):
        return "INVALID_RESULT", {"error": "result file is not a JSON object"}
    if record.get("id") != request_id:
        return "INVALID_RESULT", {"error": f"result file carries id {record.get('id')!r}, expected {request_id!r}", "record": record}
    status = record.get("status")
    outcome = record.get("outcome") if isinstance(record.get("outcome"), dict) else None
    if status == "ok":
        return "OK", record
    if status == "indeterminate":
        return "INDETERMINATE", record
    if status == "error":
        kind = (outcome or {}).get("kind")
        if kind == "refused":
            return "REFUSED_IN_LIVE", record
        if kind == "indeterminate":
            return "INDETERMINATE", record
        return "FAILED_IN_LIVE", record
    return "INVALID_RESULT", {"error": f"result file has status {status!r}", "record": record}


def submit_request(payload: dict[str, Any], wait_seconds: float, *, target: BridgeTarget | Path | None = None,
                   idempotency_key: str | None = None) -> dict[str, Any]:
    """Send one operation to the Loom extension and read back what Live did.

    Refuses before writing anything when the tool call was cancelled or timed
    out, when no single extension bridge can be named, when the SDK has no
    API for the op, or when the bridge does not speak this protocol. Never
    falls back to another endpoint."""
    _hooks["check_cancelled"]()  # a cancelled or timed-out call must not start a new mutation
    op = str(payload.get("op") or "write_clip")
    if target is None:
        try:
            target = resolve_bridge_target()
        except BridgeUnavailable as error:
            return refusal(error.status, str(error), op=op, candidates=error.candidates)
    elif not isinstance(target, BridgeTarget):
        target = target_from(Path(target), "caller")
    gate = mutation_gate(target, op)
    if gate:
        return gate
    response = _submit_to(target, payload, wait_seconds, idempotency_key)
    response["bridge"] = {"root": str(target.root), "source": target.source, "session_id": target.session_id}
    return response


def _submit_to(target: BridgeTarget, payload: dict[str, Any], wait_seconds: float, idempotency_key: str | None) -> dict[str, Any]:
    op = str(payload.get("op") or "write_clip")
    ensure_bridge_dirs(target)
    # The session may have changed since the target was resolved (a build
    # holds one target for minutes). A request stamped for the old session
    # would only be refused by the extension; better not to write it at all.
    if op not in READ_OPS and target.session_id:
        current, _age = _read_state(target.root)
        current_session = (current or {}).get("session_id")
        if current_session and current_session != target.session_id:
            return refusal("STALE_SESSION", f"stale_session: the bridge now belongs to Live session {current_session}, "
                           f"this call was prepared for {target.session_id}; nothing was sent -- resolve the target again",
                           op=op, target=target)
    request_id = f"req_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    filename = f"{request_id}.json"
    request_file = target.requests / filename
    issued = time.time()
    body = dict(payload)
    body["id"] = request_id
    body["schema_version"] = BRIDGE_SCHEMA_VERSION
    body["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    body["issued_at"] = issued
    # Past this moment the consumer must not apply the request, whoever finds it.
    body["expires_at"] = issued + (wait_seconds if wait_seconds > 0 else REQUEST_TTL_SECONDS)
    if target.session_id:
        body["target_session"] = target.session_id
    if idempotency_key:
        body["idempotency_key"] = idempotency_key
    write_atomic(request_file, json.dumps(body, indent=2))

    response: dict[str, Any] = {"request_id": request_id, "request_file": str(request_file), "op": op}
    if wait_seconds <= 0:
        response["status"] = "QUEUED"
        response["consumed"] = None
        response["expires_at"] = body["expires_at"]
        response["outcome"] = {"kind": "indeterminate", "code": "queued_unverified", "applied": None, "verified": False,
                               "side_effects": f"{op} will be applied if the extension picks it up before {body['expires_at']:.0f}",
                               "next_step": "read the outcome file or the state; nothing here confirms it"}
        return response

    done_file = target.done / filename
    error_file = target.errors / filename

    def outcome() -> dict[str, Any] | None:
        for path in (done_file, error_file):
            if path.exists():
                status, record = read_outcome(path, request_id)
                response["status"] = status
                response["consumed"] = True
                response["result"] = record.get("result")
                response["error"] = record.get("error")
                response["outcome"] = record.get("outcome") if isinstance(record.get("outcome"), dict) else {
                    "kind": "unknown", "code": "no_structured_outcome", "applied": None, "verified": None,
                    "side_effects": "the extension answered without a structured outcome"}
                response["result_file"] = str(path)
                if record.get("replayed"):
                    response["replayed"] = True
                    response["replayed_from"] = record.get("replayed_from")
                if isinstance(record.get("result"), dict) and record["result"].get("warning"):
                    response["warning"] = record["result"]["warning"]
                return response
        return None

    deadline = time.monotonic() + wait_seconds
    cancelled_type = _hooks["cancelled_type"]
    while time.monotonic() < deadline:
        try:
            _hooks["check_cancelled"]()
        except cancelled_type as cancelled:
            # Take the request back if Live has not picked it up; if it has,
            # the outcome is unknown and the message says so.
            try:
                request_file.unlink()
                raise cancelled_type(f"{cancelled}; request {request_id} was withdrawn before Live picked it up, nothing applied") from None
            except FileNotFoundError:
                raise cancelled_type(f"{cancelled}; Live had already picked up request {request_id}, its outcome is indeterminate") from None
        found = outcome()
        if found:
            return found
        _hooks["report_progress"](wait_seconds - (deadline - time.monotonic()), wait_seconds, "waiting for Live")
        time.sleep(0.2)

    # Timed out. If the request file is still ours to remove, Live never saw it.
    try:
        request_file.unlink()
        response["status"] = "NOT_CONSUMED"
        response["consumed"] = False
        response["message"] = (
            f"Live did not pick the request up within {wait_seconds}s; it was withdrawn, nothing was applied. "
            "Usual cause: Live is not running, or the Loom extension is not loaded in it.")
        response["outcome"] = {"kind": "refused", "code": "not_consumed", "applied": False, "verified": True,
                               "side_effects": "nothing: the request was withdrawn unread"}
        return response
    except FileNotFoundError:
        pass
    grace = time.monotonic() + INDETERMINATE_GRACE_SECONDS
    while time.monotonic() < grace:
        found = outcome()
        if found:
            return found
        time.sleep(0.1)
    response["status"] = "INDETERMINATE"
    response["consumed"] = True
    response["message"] = (
        f"Live picked request {request_id} up but no outcome arrived within {wait_seconds}s. "
        "The operation may or may not have been applied: read the state first. A retry with the same "
        "idempotency_key is answered from the extension's journal (the stored outcome, or an indeterminate "
        "refusal if the earlier attempt never recorded one); it is never applied a second time.")
    response["outcome"] = {"kind": "indeterminate", "code": "no_outcome_in_time", "applied": None, "verified": False,
                           "side_effects": f"{op} may have been applied by the extension after this call gave up",
                           "next_step": "read live_state; then retry with the SAME key to get the stored outcome, or a new key only for what the state shows is missing"}
    return response


def active_bridge_label() -> str:
    """Which Live endpoint the next request would go to."""
    try:
        target = resolve_bridge_target()
    except BridgeUnavailable as error:
        return f"no bridge ({error.status})"
    version = target.surface_version or "no state published yet"
    return f"Loom extension bridge ({version}) at {target.root}"


def bridge_candidates() -> list[dict[str, Any]]:
    """The Loom extension's bridge root(s) with their freshness, plus every
    other bridge-shaped directory that is deliberately ignored."""
    found = []
    try:
        active = resolve_bridge_target().root
    except BridgeUnavailable:
        active = None
    considered = [(BRIDGE_ROOT, BRIDGE_ROOT_ENV)] if BRIDGE_ROOT is not None else [(root, "loom_extension") for root in loom_extension_roots()]
    for root, source in considered + [(root, "legacy_superseded") for _id, root in legacy_bridge_roots()] + [(root, "ignored") for root in other_bridge_roots()]:
        target = target_from(root, source)
        entry: dict[str, Any] = {"root": str(root), "source": source, "active": root == active, "state": "never_published"}
        if target.state is not None:
            entry.update({
                "state": "fresh" if target.is_fresh else "stale",
                "age_seconds": round(target.age, 1) if target.age is not None else None,
                "surface_version": target.surface_version,
                "bridge_protocol": target.bridge_protocol,
                "session_id": target.session_id,
                "capabilities": target.capabilities,
                "protocol": target.protocol_report(),
            })
        elif target.state_file.exists():
            entry["state"] = "unreadable"
        found.append(entry)
    return found
