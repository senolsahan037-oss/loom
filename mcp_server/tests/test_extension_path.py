#!/usr/bin/env python3
# © 2026 Şenol Şahan / SubverseLab · Loom · https://subverselab.com/loom
"""The MCP against the extension protocol alone: no control surface, no Live.

A fake extension bridge (tests/fake_extension_bridge.py) consumes the
server's request files in a temporary directory. What is proven, on the MCP
side of the contract: every Live operation goes to that one bridge; SDK-less
operations are refused before a request exists; the legacy session writer
uses the same protocol; corrupt, foreign-id, withdrawn and unanswered
requests get INVALID_RESULT / NOT_CONSUMED / INDETERMINATE, never OK; a
cancelled call withdraws its request; a retry with the same idempotency key
does not mutate twice; expired and foreign-session requests are not applied;
two Loom bridges are refused as ambiguous; an older or incompatible
extension, a stale state or a changed session gets NO mutation request;
project_build validates first, writes in the contract's order, blocks
unverified targets and reports its status honestly; two plan_create calls
never share files.

Not proven here: the extension's own queue code. That is
test_bridge_consumer_real.py (the real bridge.ts over a fake Live) and, for
Live itself, the manual acceptance step on the beta.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_ROOT = Path(tempfile.mkdtemp(prefix="loom_ext_path_"))
os.environ["LOOM_BRIDGE_ROOT"] = str(BRIDGE_ROOT)
OUTPUT_ROOT = Path(tempfile.mkdtemp(prefix="loom_ext_output_"))
os.environ["LOOM_OUTPUT_ROOT"] = str(OUTPUT_ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_extension_bridge import FakeExtensionBridge, FakeSet  # noqa: E402

spec = importlib.util.spec_from_file_location("loom_server_ext", ROOT / "mcp_server" / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

checks, failures = [], []


def check(label, condition, detail=""):
    (checks if condition else failures).append(label if condition else "%s  %s" % (label, str(detail)[:int(os.environ.get("LOOM_TEST_DETAIL", "400"))]))


def reset_root() -> None:
    for sub in ("requests", "done", "errors", "state"):
        shutil.rmtree(BRIDGE_ROOT / sub, ignore_errors=True)


def request_files() -> list[str]:
    return sorted(p.name for p in (BRIDGE_ROOT / "requests").glob("*.json")) if (BRIDGE_ROOT / "requests").exists() else []


def a_set() -> FakeSet:
    live = FakeSet()
    live.add_track("KICK", devices=["Drum Rack"], pad_notes=[36, 38, 42, 46])
    live.add_track("BASS", devices=["Operator"])
    live.add_track("Vocal", is_midi=False)
    return live


try:
    # ---- 1) one bridge, no surface: reads and writes go through it ----------
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, a_set()) as bridge:
        status = server.dispatch_tool("live_bridge_status", {})
        check("the status names the extension endpoint and no fallback",
              status["endpoint"] == "loom_extension" and status["fallback"] is None and status["bridge_root"] == str(BRIDGE_ROOT), status)
        check("the status lists what the SDK cannot do", status["unsupported_by_sdk"].get("set_key") == "key_write", status["unsupported_by_sdk"])
        answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 131, "wait_seconds": 5})
        check("set_tempo goes through the extension bridge", answer["status"] == "OK" and bridge.live.tempo == 131.0 and answer["bridge"]["root"] == str(BRIDGE_ROOT), answer)
        state = server.dispatch_tool("live_state", {"wait_seconds": 5})
        check("live_state reads the extension's state with its session id", state["available"] and state["tempo"] == 131.0 and state["bridge"]["session_id"] == bridge.session_id, state.get("bridge"))

        # SDK-less operations never become a request file
        before = list(bridge.seen)
        for op, capability in (("set_key", "key_write"), ("transport", "transport"), ("capture_prepare", "recording")):
            answer = server.dispatch_tool("live_command", {"op": op, "root": "F", "mode": "Minor", "action": "play", "wait_seconds": 1})
            check(f"{op} is refused as UNSUPPORTED_BY_SDK before anything is sent",
                  answer["status"] == "UNSUPPORTED_BY_SDK" and answer["capability"] == capability, answer)
        check("no request reached the bridge for the refused operations", bridge.seen == before and request_files() == [], (bridge.seen, request_files()))
        answer = server.dispatch_tool("mix_capture", {"method": "resample"})
        check("resample capture is reported unsupported instead of emulated", answer["status"] == "UNSUPPORTED_BY_SDK" and answer["capability"] == "recording", answer)
        answer = server.dispatch_tool("mix_capture", {"method": "tap", "follow_transport": True})
        check("follow_transport is reported unsupported (no transport in the SDK)", answer["status"] == "UNSUPPORTED_BY_SDK" and answer["capability"] == "transport", answer)

        # the legacy writer on the common protocol
        answer = server.dispatch_tool("midi_write_to_live", {"name": "legacy", "notes": [{"pitch": 40, "start": 0, "duration": 1, "velocity": 100}], "length_beats": 4, "track": "BASS", "wait_seconds": 5})
        check("midi_write_to_live is a write_clip request on the common protocol",
              answer["status"] == "WRITTEN_TO_LIVE" and answer["op"] == "write_clip" and bridge.live.track("BASS")["slots"][0]["name"] == "legacy", answer)
        record = json.loads(Path(answer["result_file"]).read_text())
        check("the request carried expiry, session and schema", record.get("expires_at") and record.get("target_session") == bridge.session_id and record.get("schema_version") == "sensei.bridge.v2", record)
        answer = server.dispatch_tool("midi_write_to_live", {"name": "again", "notes": [{"pitch": 40, "start": 0, "duration": 1, "velocity": 100}], "length_beats": 4, "track": "BASS", "slot": 0, "wait_seconds": 5})
        check("an occupied session slot is refused, not overwritten", answer["status"] == "REJECTED_BY_LIVE" and bridge.live.track("BASS")["slots"][0]["name"] == "legacy", answer)
        check("every Live answer carries a structured outcome", answer.get("outcome", {}).get("kind") == "refused" and answer["outcome"].get("applied") is False, answer.get("outcome"))

    # ---- 2) corrupt / foreign results are never OK ----------------------------
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, a_set(), corrupt_results=True):
        answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 99, "wait_seconds": 5})
        check("a result without the request's id is INVALID_RESULT, not OK", answer["status"] == "INVALID_RESULT", answer)

    # ---- 3) nobody answers: withdrawn, or indeterminate ------------------------
    reset_root()
    FakeExtensionBridge(BRIDGE_ROOT, a_set()).publish_state()  # state exists, consumer does not run
    started = time.monotonic()
    answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 99, "wait_seconds": 0.6})
    check("an unanswered request is withdrawn and reported NOT_CONSUMED", answer["status"] == "NOT_CONSUMED" and answer["consumed"] is False, answer)
    check("the withdrawn request file is gone, so a later Live cannot apply it", request_files() == [], request_files())
    check("the wait was bounded", time.monotonic() - started < 5)
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, a_set(), swallow=True):
        answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 99, "wait_seconds": 0.6})
        check("a request Live picked up but never answered is INDETERMINATE, not NOT_CONSUMED",
              answer["status"] == "INDETERMINATE" and answer["consumed"] is True and "retry" in answer["message"], answer)

    # ---- 4) a queued request expires; a foreign session is refused --------------
    reset_root()
    live = a_set()
    consumer = FakeExtensionBridge(BRIDGE_ROOT, live)
    consumer.publish_state()  # a compatible bridge that is not consuming yet
    queued = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 55, "wait_seconds": 0})
    check("a fire-and-forget request is QUEUED with an expiry and an unverified outcome", queued["status"] == "QUEUED" and queued["expires_at"] > time.time() and queued["outcome"]["applied"] is None, queued)
    path = Path(queued["request_file"])
    body = json.loads(path.read_text())
    body["expires_at"] = time.time() - 1
    path.write_text(json.dumps(body))
    with consumer as bridge:
        deadline = time.monotonic() + 3
        while path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        check("an expired request is refused by the consumer and changes nothing", live.tempo == 120.0 and (BRIDGE_ROOT / "errors" / path.name).exists(), (live.tempo, sorted(p.name for p in (BRIDGE_ROOT / "errors").glob("*"))))
        foreign = BRIDGE_ROOT / "requests" / "req_foreign.json"
        foreign.write_text(json.dumps({"id": "req_foreign", "op": "set_tempo", "bpm": 66, "target_session": "some-earlier-live"}))
        deadline = time.monotonic() + 3
        while not (BRIDGE_ROOT / "errors" / "req_foreign.json").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        check("a request issued for another Live session is not applied", live.tempo == 120.0 and (BRIDGE_ROOT / "errors" / "req_foreign.json").exists(), (live.tempo, sorted(p.name for p in (BRIDGE_ROOT / "errors").glob("*"))))

    # ---- 5) cancellation withdraws the request; a cancelled call sends nothing -
    reset_root()
    FakeExtensionBridge(BRIDGE_ROOT, a_set()).publish_state()
    token = server._current_request.set("cancel-me")
    try:
        threading.Timer(0.3, server.mark_cancelled, args=("cancel-me",)).start()
        try:
            server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 99, "wait_seconds": 5})
            check("a cancelled call raises", False)
        except server.ToolCancelled as error:
            check("a cancelled call withdraws its request and says nothing was applied", "withdrawn" in str(error) and request_files() == [], (str(error), request_files()))
        server.mark_cancelled("cancel-me")
        try:
            server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 99, "wait_seconds": 5})
            check("an already-cancelled (timed-out) call starts no mutation", False)
        except server.ToolCancelled:
            check("an already-cancelled (timed-out) call starts no mutation", request_files() == [], request_files())
    finally:
        server._clear_cancelled("cancel-me")
        server._current_request.reset(token)

    # ---- 6) a retry with the same key does not write twice ---------------------
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, a_set()) as bridge:
        notes = [{"pitch": 36, "start": 0, "duration": 0.5, "velocity": 100}]
        first = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 1, "length_beats": 4, "name": "Intro", "notes": notes, "idempotency_key": "build-x:clip:KICK:Intro", "wait_seconds": 5})
        second = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 1, "length_beats": 4, "name": "Intro", "notes": notes, "idempotency_key": "build-x:clip:KICK:Intro", "wait_seconds": 5})
        check("the retry is answered from the stored outcome and the clip exists once",
              first["status"] == "OK" and second["status"] == "OK" and second.get("replayed") is True and len(bridge.live.track("KICK")["arrangement"]) == 1, (second, bridge.live.track("KICK")["arrangement"]))
        third = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 1, "length_beats": 4, "name": "Intro", "notes": notes, "wait_seconds": 5})
        check("without a key, a second write over a clip is refused by the consumer, not stacked", third["status"] == "REFUSED_IN_LIVE" and len(bridge.live.track("KICK")["arrangement"]) == 1, third)

    # ---- 7) endpoint discovery: none, one, ambiguous ---------------------------
    bc = server.bridge_client
    saved_env, saved_root = bc.BRIDGE_ROOT, bc.EXTENSIONS_DATA_DIR
    fake_data = Path(tempfile.mkdtemp(prefix="loom_ext_data_"))
    try:
        bc.BRIDGE_ROOT = None
        bc.EXTENSIONS_DATA_DIR = fake_data
        answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 99, "wait_seconds": 1})
        check("with no Loom extension installed the answer says so and sends nothing", answer["status"] == "NO_EXTENSION_BRIDGE" and "install" in answer["error"], answer)
        (fake_data / "other.vendor" / "bridge" / "state").mkdir(parents=True)
        (fake_data / "other.vendor" / "bridge" / "state" / "live_state.json").write_text(json.dumps({"surface_version": "loom-extension/0.3.0", "bridge_protocol": "loom.bridge/3", "session_id": "x", "captured_at": time.time()}))
        answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 99, "wait_seconds": 1})
        check("another vendor's bridge is never chosen even when it looks like Loom's", answer["status"] == "NO_EXTENSION_BRIDGE", answer)
        loom_root = fake_data / "subverselab.loom" / "bridge"
        FakeExtensionBridge(loom_root, a_set()).publish_state()
        target = server.resolve_bridge_target()
        check("the Loom extension's own bridge is the target", target.root == loom_root and target.source == "loom_extension_fresh", target.describe())
        bc.LOOM_EXTENSION_IDS = ("subverselab.loom", "subverselab.loom-dev")
        FakeExtensionBridge(fake_data / "subverselab.loom-dev" / "bridge", a_set()).publish_state()
        answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 99, "wait_seconds": 1})
        check("two fresh Loom bridges are refused as ambiguous, nothing is sent to either",
              answer["status"] == "AMBIGUOUS_BRIDGE" and len(answer["candidates"]) == 2 and not list((loom_root / "requests").glob("*.json")), answer)
        status = server.dispatch_tool("live_bridge_status", {})
        check("the status shows the ignored vendor bridge as ignored", any(c["source"] == "ignored" for c in status["bridge_candidates"]), status["bridge_candidates"])
    finally:
        bc.BRIDGE_ROOT, bc.EXTENSIONS_DATA_DIR = saved_env, saved_root
        bc.LOOM_EXTENSION_IDS = ("subverselab.loom",)
        shutil.rmtree(fake_data, ignore_errors=True)

    # ---- 8) midi_generate: offline suggestion vs live target -------------------
    # The Sensei corpus is built from the user's own Ableton install and is
    # never in the repository. With it, the checks below prove the write path;
    # on a clean checkout (CI) they prove the fail-closed path instead: every
    # clip write is blocked with dataset_release_invalid, nothing is written,
    # tempo and locators are still applied and verified. Neither shape is
    # counted as the other.
    probe = server.dispatch_tool("midi_generate", {"role": "bass", "genre": "Trap", "bars": 2})
    bass_corpus = bool(probe.get("generation_safe"))
    if not bass_corpus:
        print("  --  no Sensei corpus on this machine (%s): write-path checks assert the fail-closed shape instead" % probe.get("error"))

    def either(label, with_corpus, without_corpus, detail=""):
        if bass_corpus:
            check(label, with_corpus(), detail)
        else:
            check(label + "  [no corpus: blocked with dataset_release_invalid, nothing written]", without_corpus(), detail)

    generated = server.dispatch_tool("midi_generate", {"role": "drum", "genre": "Trap", "bars": 2})
    check("a drum part without pad evidence is an offline suggestion, not writable",
          generated.get("writable_to_live") is False and generated.get("target_evidence") == "assumed_general_midi_pads", {k: generated.get(k) for k in ("writable_to_live", "target_evidence", "error")})
    generated = server.dispatch_tool("midi_generate", {"role": "drum", "genre": "Trap", "bars": 2, "pad_notes": [36, 38, 42, 46]})
    check("with pad notes from Live the part is writable", generated.get("writable_to_live") is True and generated.get("target_evidence") == "live_drum_rack_pads", generated.get("error"))
    generated = server.dispatch_tool("midi_generate", {"role": "drum", "genre": "Trap", "bars": 2, "auto_write_to_live": True})
    either("auto-write of an unverified drum part is BLOCKED, not attempted",
           lambda: (generated.get("bridge_write_status") or {}).get("status") == "BLOCKED",
           lambda: (generated.get("bridge_write_status") or {}).get("status") == "NOT_WRITTEN"
           and "dataset_release_invalid" in str((generated.get("bridge_write_status") or {}).get("reason")), generated.get("bridge_write_status") or generated)
    waltz = server.dispatch_tool("midi_generate", {"role": "bass", "genre": "Trap", "bars": 2, "beats_per_bar": 3})
    if waltz.get("generation_safe"):
        check("beats_per_bar sizes the generated clip", waltz["payload"]["clip_length"] == 6.0, waltz["payload"]["clip_length"])
    else:
        print("  --  beats_per_bar clip-length check skipped: no bass corpus on this machine (%s)" % waltz.get("error"))

    # ---- 9) project_build: validation, order, evidence, status ------------------
    # Written into a temp dir, never into the repo: a test must not leave a
    # fixture behind, and `tests/fixtures/` holds committed evidence only.
    plan_dir = Path(tempfile.mkdtemp(prefix="loom_build_plan_"))
    fixture_plan = plan_dir / "build_plan_small.json"
    plan = {
        "project": {"name": "Küçük Şarkı", "bpm": 126, "key": "F Minor", "genre": "Trap", "total_bars": 8},
        "locators": [{"id": "intro", "name": "Intro", "start_bar": 1, "end_bar": 4, "energy": 30},
                     {"id": "hook", "name": "Hook", "start_bar": 5, "end_bar": 8, "energy": 90}],
        "tracks": [{"ableton_name": "KICK", "sensei_role": "drum", "instrument_family": "Drum Rack"},
                   {"ableton_name": "BASS", "sensei_role": "bass", "instrument_family": "Operator"},
                   {"ableton_name": "PAD", "sensei_role": "chord", "instrument_family": "Some Browser Preset"},
                   {"ableton_name": "Vocal", "sensei_role": None}],
    }
    fixture_plan.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    broken = dict(plan, locators=[{"id": "x", "name": "", "start_bar": 3, "end_bar": 1}])
    broken_path = fixture_plan.with_name("build_plan_broken.json")
    broken_path.write_text(json.dumps(broken), encoding="utf-8")
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, FakeSet()) as bridge:
        answer = server.dispatch_tool("project_build", {"plan_path": str(broken_path), "dry_run": False, "wait_seconds": 5})
        check("an invalid plan fails validation before any mutation", answer["status"] == "failed" and answer["stage"] == "plan_validation" and bridge.live.applied == [], answer)
        dry = server.dispatch_tool("project_build", {"plan_path": str(fixture_plan), "dry_run": True})
        check("a dry run touches nothing and reports the key as unsupported", dry["status"] == "dry_run" and bridge.live.applied == [] and any(s["kind"] == "key" and s["outcome"] == "UNSUPPORTED_BY_SDK" for s in dry["session_steps"]), dry.get("session_steps"))
        answer = server.dispatch_tool("project_build", {"plan_path": str(fixture_plan), "dry_run": False, "wait_seconds": 5})
        applied = bridge.live.applied
        first_clip = applied.index("write_arrangement_clip") if "write_arrangement_clip" in applied else -1
        either("the build writes in order: tempo, tracks, clips, locators",
               lambda: applied and applied[0] == "set_tempo" and all(op == "create_midi_track" for op in applied[1:4]) and first_clip > 0
               and all(op != "create_midi_track" for op in applied[first_clip:]) and applied[-1] == "create_locator" and "create_locator" not in applied[:first_clip],
               lambda: applied and applied[0] == "set_tempo" and all(op == "create_midi_track" for op in applied[1:4]) and first_clip == -1 and applied[-1] == "create_locator", applied)
        by_track = {}
        for write in answer["writes"]:
            by_track.setdefault(write["track"], []).append(write["status"])
        either("bass parts are written with a verified instrument; drums are blocked because an inserted Drum Rack is empty (no kit resolved)",
               lambda: set(by_track.get("BASS", [])) == {"OK"} and set(by_track.get("KICK", [])) == {"blocked"} and all("drum_rack_not_verified" in w.get("reason", "") for w in answer["writes"] if w["track"] == "KICK"),
               lambda: set(by_track.get("BASS", [])) == {"blocked"} and all("dataset_release_invalid" in w.get("reason", "") for w in answer["writes"] if w["track"] == "BASS"), by_track)
        check("a track whose instrument could not be loaded gets blocked writes, not silent ones",
              set(by_track.get("PAD", [])) == {"blocked"} and all("instrument_not_loaded" in w.get("reason", "") for w in answer["writes"] if w["track"] == "PAD"), by_track)
        check("the song key step is reported unsupported, never attempted", not any(op == "set_key" for op in applied) and any(s["kind"] == "key" and s["outcome"] == "UNSUPPORTED_BY_SDK" for s in answer["session_steps"]))
        check("locators and tempo are verified from a read-back", all(s.get("verified") for s in answer["session_steps"] if s["kind"] in ("locator", "tempo")), answer["session_steps"])
        either("the overall status is partial (one track blocked)", lambda: answer["status"] == "partial", lambda: answer["status"] == "blocked", answer["status"])
        check("Turkish characters in the project name survive", answer["project"]["name"] == "Küçük Şarkı")
        again = server.dispatch_tool("project_build", {"plan_path": str(fixture_plan), "dry_run": False, "wait_seconds": 5})
        either("rebuilding the same plan replays instead of stacking clips",
               lambda: len(bridge.live.track("BASS")["arrangement"]) == 2 and all(w.get("replayed") for w in again["writes"] if w["track"] == "BASS"),
               lambda: len(bridge.live.track("BASS")["arrangement"]) == 0 and again["status"] == "blocked", (len(bridge.live.track("BASS")["arrangement"]), [w.get("replayed") for w in again["writes"]]))
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, FakeSet(), swallow=True) as bridge:
        answer = server.dispatch_tool("project_build", {"plan_path": str(fixture_plan), "dry_run": False, "wait_seconds": 0.5})
        check("when the bridge stops answering the build is indeterminate and the rest is aborted",
              answer["status"] == "indeterminate" and answer["aborted"] and all(w["status"] in ("aborted", "muted_by_plan") for w in answer["writes"]), (answer["status"], answer["aborted"], answer["totals"]))
    reset_root()
    answer = server.dispatch_tool("project_build", {"plan_path": str(fixture_plan), "dry_run": False, "wait_seconds": 0.5})
    check("without a published state the build is blocked before any mutation", answer["status"] == "blocked" and answer["stage"] == "bridge" and answer.get("gate_status") == "NO_STATE", answer.get("reason"))

    # ---- 9e) the protocol gate: no mutation reaches an extension this client cannot trust ----
    def mutations_sent(bridge_obj) -> int:
        return len(bridge_obj.live.applied)

    for label, kwargs, expected in (
        ("an old extension (no protocol, no session id) gets UPGRADE_REQUIRED", {"protocol": None, "publish_session": False}, "UPGRADE_REQUIRED"),
        ("an extension without a session id gets UPGRADE_REQUIRED", {"publish_session": False}, "UPGRADE_REQUIRED"),
        ("an extension speaking another protocol gets PROTOCOL_MISMATCH", {"protocol": "loom.bridge/9"}, "PROTOCOL_MISMATCH"),
    ):
        reset_root()
        with FakeExtensionBridge(BRIDGE_ROOT, a_set(), **kwargs) as bridge:
            answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 99, "wait_seconds": 2})
            check(label, answer["status"] == expected and answer["consumed"] is False and answer.get("outcome", {}).get("applied") is False, answer)
            check(f"... and zero mutations were sent ({expected})", mutations_sent(bridge) == 0 and request_files() == [] and bridge.seen == [], (bridge.seen, bridge.live.applied))
            build = server.dispatch_tool("project_build", {"plan_path": str(fixture_plan), "dry_run": False, "wait_seconds": 2})
            check(f"... project_build is blocked at the gate ({expected})", build["status"] == "blocked" and build["stage"] == "bridge" and build.get("gate_status") == expected and mutations_sent(bridge) == 0, build.get("reason"))
            state = server.dispatch_tool("live_state", {"wait_seconds": 2})
            check(f"... but reading the state still works ({expected})", state.get("available") is True and state.get("tempo") == 120.0, state.get("reason"))
            status = server.dispatch_tool("live_bridge_status", {})
            check(f"... and the diagnosis names the gate ({expected})", status["mutations_allowed"] is False and status["protocol"]["status"] == expected, status.get("protocol"))
    reset_root()
    stale = FakeExtensionBridge(BRIDGE_ROOT, a_set())
    stale.publish_state(captured_at=time.time() - 120)  # published, consumer not running
    answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 99, "wait_seconds": 1})
    check("a stale state gets STALE_STATE and no request file", answer["status"] == "STALE_STATE" and request_files() == [], answer)
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, a_set(), session_id="live-1") as bridge:
        target = server.resolve_bridge_target()
        check("a compatible, fresh extension passes the gate", server.bridge_client.mutation_gate(target, "set_tempo") is None and target.protocol_report()["compatible"], target.describe())
        bridge.session_id = "live-2"  # Live restarted between resolving the target and sending
        bridge.publish_state()
        answer = server.bridge_client.submit_request({"op": "set_tempo", "bpm": 99}, 2, target=target)
        check("a session change between resolve and send is STALE_SESSION, nothing sent", answer["status"] == "STALE_SESSION" and mutations_sent(bridge) == 0 and request_files() == [], answer)
        answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 101, "wait_seconds": 5})
        check("the current, compatible extension is mutated normally", answer["status"] == "OK" and bridge.live.tempo == 101.0 and answer["outcome"]["kind"] == "applied", answer)

    # ---- 9b) the timeout race: claimed and in flight is never "nothing applied" -
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, a_set(), hold_before_apply=True) as bridge:
        answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 77, "wait_seconds": 0.6})
        check("a request the consumer claimed but has not finished is INDETERMINATE, never NOT_CONSUMED",
              answer["status"] == "INDETERMINATE" and answer["consumed"] is True, answer)
        check("the claimed request is out of the queue and out of the MCP's reach", request_files() == [] and (BRIDGE_ROOT / "processing" / Path(answer["request_file"]).name).exists(), sorted(p.name for p in (BRIDGE_ROOT / "processing").glob("*")))
        bridge.release()
        deadline = time.monotonic() + 3
        while not (BRIDGE_ROOT / "done" / Path(answer["request_file"]).name).exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        check("the in-flight mutation completes after the MCP gave up, and the state shows it", bridge.live.tempo == 77.0 and (BRIDGE_ROOT / "done" / Path(answer["request_file"]).name).exists(), bridge.live.tempo)
        state = server.dispatch_tool("live_state", {"wait_seconds": 3})
        check("reading the state before a retry reveals the applied change", state.get("tempo") == 77.0, state.get("tempo"))

    # ---- 9c) crash after the mutation, before the outcome: no second write ------
    reset_root()
    live = a_set()
    notes = [{"pitch": 36, "start": 0, "duration": 0.5, "velocity": 100}]
    with FakeExtensionBridge(BRIDGE_ROOT, live, session_id="live-A", crash_after_apply=True) as bridge:
        answer = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 1, "length_beats": 4, "name": "Intro", "notes": notes, "idempotency_key": "build-crash:clip:KICK:Intro", "wait_seconds": 0.8})
        check("a host that dies after applying leaves the MCP with INDETERMINATE", answer["status"] == "INDETERMINATE" and len(live.track("KICK")["arrangement"]) == 1, (answer["status"], len(live.track("KICK")["arrangement"])))
    with FakeExtensionBridge(BRIDGE_ROOT, live, session_id="live-A") as bridge:
        recovered = bridge.recover()
        check("on restart the interrupted request is answered indeterminate, not re-applied", len(recovered) == 1 and len(live.track("KICK")["arrangement"]) == 1, recovered)
        retry = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 1, "length_beats": 4, "name": "Intro", "notes": notes, "idempotency_key": "build-crash:clip:KICK:Intro", "wait_seconds": 5})
        check("a retry with the same key after the crash is INDETERMINATE (structured, not an error string) and writes nothing",
              retry["status"] == "INDETERMINATE" and retry.get("outcome", {}).get("code") == "indeterminate_earlier_attempt" and retry["outcome"].get("applied") is None
              and retry["outcome"].get("side_effects") and len(live.track("KICK")["arrangement"]) == 1, retry)
        other = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 5, "length_beats": 4, "name": "Hook", "notes": notes, "idempotency_key": "build-crash:clip:KICK:Intro", "wait_seconds": 5})
        check("the same key with different contents is INDETERMINATE too (the earlier attempt's fate wins over the conflict)", other["status"] == "INDETERMINATE" and len(live.track("KICK")["arrangement"]) == 1, other)
        conflict_key = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 9, "length_beats": 4, "name": "Bridge", "notes": notes, "idempotency_key": "build-crash:clip:KICK:Bridge", "wait_seconds": 5})
        conflict = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 13, "length_beats": 4, "name": "Outro", "notes": notes, "idempotency_key": "build-crash:clip:KICK:Bridge", "wait_seconds": 5})
        check("the same key with different contents (earlier attempt finished) is a refusal with a conflict code",
              conflict_key["status"] == "OK" and conflict["status"] == "REFUSED_IN_LIVE" and conflict.get("outcome", {}).get("code") == "idempotency_conflict" and len(live.track("KICK")["arrangement"]) == 2, conflict)
    with FakeExtensionBridge(BRIDGE_ROOT, a_set(), session_id="live-B") as bridge:
        foreign = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 9, "length_beats": 4, "name": "Bridge", "notes": notes, "idempotency_key": "build-crash:clip:KICK:Bridge", "wait_seconds": 5})
        check("a key finished in another Live session is not replayed into this one", foreign["status"] == "REFUSED_IN_LIVE" and foreign.get("outcome", {}).get("code") == "replay_refused_other_session" and len(bridge.live.track("KICK")["arrangement"]) == 0, foreign)
        interrupted = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 1, "length_beats": 4, "name": "Intro", "notes": notes, "idempotency_key": "build-crash:clip:KICK:Intro", "wait_seconds": 5})
        check("a key interrupted in another Live session is INDETERMINATE here too, and not applied", interrupted["status"] == "INDETERMINATE" and len(bridge.live.track("KICK")["arrangement"]) == 0, interrupted)

    # ---- 9d) build keys do not collide across plans with the same file name -----
    plan_dir_a = Path(tempfile.mkdtemp(prefix="loom_plan_a_"))
    plan_dir_b = Path(tempfile.mkdtemp(prefix="loom_plan_b_"))
    same_name_a = plan_dir_a / "ableton_session_plan.json"
    same_name_b = plan_dir_b / "ableton_session_plan.json"
    same_name_a.write_text(json.dumps(plan), encoding="utf-8")
    # Plan B: same file name, different contents, and sections placed after
    # plan A's so both can exist in one set (an overlap would be refused, and
    # rightly so -- that is the clip-protection rule, not a key collision).
    plan_b = json.loads(json.dumps(plan))
    plan_b["locators"] = [{"id": "intro_b", "name": "Intro B", "start_bar": 9, "end_bar": 12, "energy": 30},
                          {"id": "hook_b", "name": "Hook B", "start_bar": 13, "end_bar": 16, "energy": 90}]
    same_name_b.write_text(json.dumps(plan_b), encoding="utf-8")
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, FakeSet(), session_id="live-C") as bridge:
        first = server.dispatch_tool("project_build", {"plan_path": str(same_name_a), "dry_run": False, "wait_seconds": 5})
        second = server.dispatch_tool("project_build", {"plan_path": str(same_name_b), "dry_run": False, "wait_seconds": 5})
        bass_clips = bridge.live.track("BASS")["arrangement"]
        either("two different plans with the same file name get different build keys and both build",
               lambda: first["status"] == "partial" and second["status"] == "partial" and not any(w.get("replayed") for w in second["writes"])
               and [c["name"] for c in bass_clips] == ["Intro", "Hook", "Intro B", "Hook B"],
               lambda: first["status"] == "blocked" and second["status"] == "blocked" and not any(w.get("replayed") for w in second["writes"]) and bass_clips == [],
               (first["status"], second["status"], [c["name"] for c in bass_clips], second["totals"]))
    shutil.rmtree(plan_dir_a, ignore_errors=True)
    shutil.rmtree(plan_dir_b, ignore_errors=True)

    # ---- 9f) product identity: the earlier extension id is read, never mutated; its journal carries over ----
    bc = server.bridge_client
    saved_env, saved_root = bc.BRIDGE_ROOT, bc.EXTENSIONS_DATA_DIR
    fake_data = Path(tempfile.mkdtemp(prefix="loom_identity_"))
    try:
        bc.BRIDGE_ROOT = None
        bc.EXTENSIONS_DATA_DIR = fake_data
        legacy_root = fake_data / "loom.sensei-midi-writer" / "bridge"
        legacy = FakeExtensionBridge(legacy_root, a_set(), session_id="old-live")
        legacy.publish_state()
        (legacy_root / "state").mkdir(parents=True, exist_ok=True)
        (legacy_root / "state" / "journal.jsonl").write_text(
            json.dumps({"key": "key:old:clip:KICK:Intro", "status": "started", "session": "old-live", "payload_hash": "h", "at": time.time(), "request_id": "req_old"}) + "\n"
            + json.dumps({"key": "key:old:tempo", "status": "ok", "session": "old-live", "payload_hash": "h2", "at": time.time(), "result": {"after": 90}}) + "\n")
        answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 99, "wait_seconds": 1})
        check("with only the old extension id installed, a mutation is LEGACY_EXTENSION and nothing is written",
              answer["status"] == "LEGACY_EXTENSION" and not list((legacy_root / "requests").glob("*.json")) and legacy.live.applied == [], answer)
        status = server.dispatch_tool("live_bridge_status", {})
        check("the diagnosis names the superseded id, its journal and the next step",
              status["available"] is False and status.get("legacy_bridges") and status["legacy_bridges"][0]["extension_id"] == "loom.sensei-midi-writer"
              and status["legacy_bridges"][0]["journal_unknown_outcome"] == 1 and "loom.ablx" in str(status.get("error")), status.get("legacy_bridges"))
        new_root = fake_data / "subverselab.loom" / "bridge"
        with FakeExtensionBridge(new_root, a_set(), session_id="new-live") as current:
            target = server.resolve_bridge_target()
            check("with the Loom id present it is the target and the old id is not a candidate for mutation", target.root == new_root and target.source == "loom_extension_fresh", target.describe())
            imported = server.dispatch_tool("live_command", {"op": "journal_import", "wait_seconds": 5})
            check("journal_import carries the old id's journal into the current bridge", imported["status"] == "OK" and imported["imports"][0]["entries"] == 2 and (imported["imports"][0].get("result") or {}).get("imported") == 2, imported)
            retry = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 1, "length_beats": 4, "name": "Intro", "notes": [{"pitch": 36, "start": 0, "duration": 0.5, "velocity": 100}], "idempotency_key": "old:clip:KICK:Intro", "wait_seconds": 5})
            check("a key interrupted under the old id is INDETERMINATE under the new one, not applied", retry["status"] == "INDETERMINATE" and current.live.track("KICK")["arrangement"] == [], retry)
            status = server.dispatch_tool("live_bridge_status", {})
            check("the diagnosis now shows the old journal as imported", status["legacy_bridges"][0]["journal_imported"] is True, status.get("legacy_bridges"))
    finally:
        bc.BRIDGE_ROOT, bc.EXTENSIONS_DATA_DIR = saved_env, saved_root
        shutil.rmtree(fake_data, ignore_errors=True)

    # ---- 9g) the kit flow and explicit simplification in project_build ----------
    kit_dir = Path(tempfile.mkdtemp(prefix="loom_kit_"))
    pack = kit_dir / "Golden Era Hip-Hop Drums by Sound Oracle"
    (pack / "Drums").mkdir(parents=True)
    shutil.copy2(ROOT / "Sensei" / "ableton" / "fixtures" / "swang_bap_kit.adg", pack / "Drums" / "Swang Bap Kit.adg")
    for rel in ("Samples/One Shots/Kick/Kick Golden Era 34.aif", "Samples/One Shots/Snare/Snare Golden Era 38.aif", "Samples/One Shots/Hihat/Hihat Closed Golden Era 24.aif"):
        (pack / rel).parent.mkdir(parents=True, exist_ok=True)
        (pack / rel).write_bytes(b"FORM")
    kit_adg = str(pack / "Drums" / "Swang Bap Kit.adg")
    # Missing references must fail before any request. Complete the fake pack
    # afterwards; these are file-presence fixtures, not audio validation.
    incomplete_kit = server.resolve_kit_reference(kit_adg)
    refused = server.dispatch_tool("live_command", {"op": "build_drum_kit", "track": "Kit", "kit": kit_adg, "allow_lossy_kit": True})
    check("an incomplete preset is refused instead of silently losing pads", refused["outcome"]["code"] == "kit_samples_missing", refused)
    for missing_sample in incomplete_kit["missing"]:
        sample_path = pack / missing_sample["relative"]
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        sample_path.write_bytes(b"FORM")
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, FakeSet()) as bridge:
        bridge.live.add_track("Kit", devices=["Drum Rack"], pad_notes=[])
        refused = server.dispatch_tool("live_command", {"op": "build_drum_kit", "track": "Kit", "kit": kit_adg, "wait_seconds": 5})
        check("a complete preset needs explicit consent before lossy reconstruction", refused["outcome"]["code"] == "kit_rebuild_requires_consent" and not bridge.live.applied, refused)
        answer = server.dispatch_tool("live_command", {"op": "build_drum_kit", "track": "Kit", "kit": kit_adg, "allow_lossy_kit": True, "wait_seconds": 5})
        check("build_drum_kit takes a .adg: its pads are resolved from the preset's own samples and built, with fidelity stated",
              answer["status"] == "OK" and sorted(bridge.live.track("Kit")["pad_notes"]) == sorted(p["note"] for p in server.resolve_kit_reference(kit_adg)["pads"])
              and answer["kit"]["pads_requested"] == 16 and not answer["kit"]["missing"] and answer["kit"]["fidelity_summary"]["dropped_categories"]["macros"]["count"] == 16 and answer["kit"]["preset_preserved"] is False
              and "device_parameters" not in json.dumps(answer["kit"]["fidelity_summary"]["dropped_categories"]["device_parameters"]) and Path(answer["kit"]["overflow_path"]).is_file(), answer.get("kit"))
        full = json.loads(Path(answer["kit"]["overflow_path"]).read_text(encoding="utf-8"))
        check("the answer's overflow file holds the full resolution: every pad and the per-pad device parameters",
              len(full["pads"]) == 16 and full["fidelity"]["dropped"]["device_parameters"], list(full))
        answer = server.dispatch_tool("live_command", {"op": "build_drum_kit", "track": "Kit", "kit": "No Such Kit", "wait_seconds": 5})
        check("an unknown kit name is refused before anything is sent", answer["status"] == "REFUSED_IN_LIVE" and answer["outcome"]["code"] == "kit_unresolved", answer)
    # Measured in Live 2026-09-07: a first build created "Main Bass" for a
    # preset the SDK cannot load and left it silent; the second build with
    # a native device adopted the track, inserted nothing, and every bass
    # write was blocked with instrument_not_loaded. An empty chain is safe to
    # fill; a chain with devices is the user's and is kept.
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, FakeSet()) as bridge:
        bridge.live.add_track("Main Bass", devices=[])
        answer = server.dispatch_tool("live_command", {"op": "create_midi_track", "name": "Main Bass", "instrument_family": "Operator", "wait_seconds": 5})
        check("an adopted track with an empty chain gets the native device it is asked for",
              answer["status"] == "OK" and answer["result"]["adopted"] is True and str(answer["result"]["instrument"]).startswith("inserted: Operator")
              and bridge.live.track("Main Bass")["devices"] == ["Operator"], answer)
        answer = server.dispatch_tool("live_command", {"op": "create_midi_track", "name": "Main Bass", "instrument_family": "Drum Rack", "wait_seconds": 5})
        check("an adopted track that already has a device is kept, nothing inserted",
              str(answer["result"]["instrument"]).startswith("kept") and bridge.live.track("Main Bass")["devices"] == ["Operator"], answer)
    # The set FILE path: the plan's real presets into a set from Live's own
    # template, then project_build verifying the kit through the manifest
    # instead of rebuilding it. Needs the Live app's Core Library; skipped
    # where it is absent, never counted as passed.
    live_kit = Path("/Applications/Ableton Live 12 Beta.app/Contents/App-Resources/Core Library/Racks/Drum Racks/Electronic/BNYX Boot Kit.adg")
    def kit_in_catalogue(name):
        # The catalogue is built by install.py from the user's Live; a fresh
        # checkout beside an installed Live has the file but no catalogue.
        try:
            server.resolve_kit_reference(name)
            return True
        except Exception as error:  # noqa: BLE001 -- any resolver refusal means "not here"
            print("  --  SKIPPED: project_file_build checks need the kit catalogue from install.py (%s)" % str(error)[:120])
            return False

    if live_kit.is_file() and kit_in_catalogue("BNYX Boot Kit"):
        file_plan = dict(plan, tracks=[{"ableton_name": "Kit", "sensei_role": "drum", "instrument_family": "BNYX Boot Kit"},
                                       {"ableton_name": "BASS", "sensei_role": "bass", "instrument_family": "Operator"}])
        file_plan_path = kit_dir / "file_plan.json"
        file_plan_path.write_text(json.dumps(file_plan), encoding="utf-8")
        bnyx = server.resolve_kit_reference("BNYX Boot Kit")
        pairs = sorted((p["raw_receiving_note"], p["decoded_receiving_note"], p["note"], p["role"]) for p in bnyx["pads"])
        check("BNYX Boot Kit: all 16 pads carry raw 77..92 and decoded 36..51 (128 - raw), the note IS the decoded value, and the raw value is never used as a note",
              [r for r, *_ in pairs] == list(range(77, 93)) and sorted(d for _, d, *_ in pairs) == list(range(36, 52)) and all(n == d == 128 - r for r, d, n, _ in pairs)
              and not any(p["note"] > 51 for p in bnyx["pads"]), pairs[:4])
        roles = {p["note"]: p["role"] for p in bnyx["pads"]}
        check("... and the roles sit where General MIDI expects them: 36 kick, 39 clap, 42 closed hat, 46 open hat",
              roles.get(36) == "kick" and roles.get(39) == "clap" and roles.get(42) == "closed_hat" and roles.get(46) == "open_hat", roles)
        built_file = server.dispatch_tool("project_file_build", {"plan_path": str(file_plan_path), "out_dir": str(kit_dir / "builds"), "name": "Selftest"})
        check("project_file_build writes the real kit into a set file and lists the track without a preset file as unresolved, never with a stand-in",
              built_file.get("status") == "written" and Path(built_file["artifact"]).is_file() and [t["track"] for t in built_file["tracks"]] == ["Kit"]
              and built_file["tracks"][0]["pads"] == 16 and built_file["tracks"][0]["macros"] == 16
              and [u["track"] for u in built_file["unresolved"]] == ["BASS"], {k: built_file.get(k) for k in ("status", "reason", "tracks", "unresolved")})
        manifest = json.loads(Path(built_file["manifest"]).read_text(encoding="utf-8")) if built_file.get("manifest") else {}
        check("the manifest names the preset and the pad notes Live will report (ReceivingNote decoded: 36..51, not 77..92)", (manifest.get("tracks") or {}).get("Kit", {}).get("pad_notes") == list(range(36, 52)), manifest.get("tracks"))
        reset_root()
        with FakeExtensionBridge(BRIDGE_ROOT, FakeSet()) as bridge:
            bridge.live.add_track("Kit", devices=["Drum Rack"], pad_notes=list(range(36, 52)))
            dry = server.dispatch_tool("project_build", {"plan_path": str(file_plan_path), "dry_run": True, "tracks": ["Kit"], "set_manifest": built_file["manifest"]})
            check("with the manifest a dry run treats the kit as native in the set file: no rebuild, no consent gate",
                  dry["status"] == "dry_run" and dry["kits"]["Kit"].get("native_in_set_file") is True and dry["kits"]["Kit"]["load_path"]["via"] == "set_file_manifest"
                  and all(w["status"] in ("would_write", "muted_by_plan") for w in dry["writes"]), dry.get("kits"))
            built = server.dispatch_tool("project_build", {"plan_path": str(file_plan_path), "dry_run": False, "tracks": ["Kit"], "set_manifest": built_file["manifest"], "wait_seconds": 5})
            kick = next(t for t in built["tracks"] if t["track"] == "Kit")
            check("with the manifest the live build adopts the track, verifies the kit from Live's pads, rebuilds nothing and writes drums by role",
                  kick.get("kit", {}).get("status") == "native_preset_loaded" and kick["kit"]["via"] == "set_file_manifest" and kick.get("preset_identity_verified") is True
                  and "build_drum_kit" not in bridge.live.applied and any(w["status"] == "OK" for w in built["writes"] if w["track"] == "Kit"), (kick, built.get("status")))
    else:
        print("  --  SKIPPED: project_file_build checks need the Live app's Core Library (BNYX Boot Kit.adg)")
    kit_plan = json.loads(fixture_plan.read_text(encoding="utf-8"))
    kit_plan_path = kit_dir / "kit_plan.json"
    kit_plan_path.write_text(json.dumps(kit_plan), encoding="utf-8")
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, FakeSet()) as bridge:
        dry = server.dispatch_tool("project_build", {"plan_path": str(kit_plan_path), "dry_run": True, "kit": kit_adg, "tracks": ["KICK", "BASS", "PAD"], "device_map": {"PAD": "Wavetable"}})
        check("a dry run states the simplification: which plan tracks are built, which dropped, which device stands in for a preset",
              dry["simplification"]["dropped"] == ["Vocal"] and dry["simplification"]["device_overrides"][0]["device"] == "Wavetable" and dry["simplification"]["reason"], dry.get("simplification"))
        check("a dry run states, once per drum channel, the kit file, its decoded pads and how it would be loaded -- never a rebuild",
              "kit" not in dry and dry["kits"]["KICK"]["pads_resolved"] >= 3 and dry["kits"]["KICK"]["rebuild_blocker"] is None
              and dry["kits"]["KICK"]["load_path"]["blocked_as"] == "PRESET_LOAD_REQUIRED" and dry["kits"]["KICK"]["expected_pad_notes"]
              and all(w["status"] == "target_verification_required" and w.get("reason") == "PRESET_LOAD_REQUIRED" for w in dry["writes"] if w["track"] == "KICK" and w["status"] != "muted_by_plan")
              and not any("kit_resolution" in t for t in dry["tracks"]), dry.get("kits"))
        check("the dry run stays under the text limit as one JSON value", len(server.render_tool_text(dry)[0]) < server.MAX_RESPONSE_CHARS and server.render_tool_text(dry)[1] is None, len(json.dumps(dry, indent=2)))
        big = {"status": "dry_run", "blob": ["x" * 100] * 400}
        text, notice = server.render_tool_text(big)
        envelope = json.loads(text)
        check("an oversized answer is a complete JSON envelope, never a cut-off object",
              envelope["truncated"] is True and envelope["status"] == "dry_run" and len(text) <= server.MAX_RESPONSE_CHARS and notice.startswith("[truncated]")
              and json.loads(Path(envelope["overflow_path"]).read_text(encoding="utf-8")) == big, (len(text), list(envelope)))
        # A plan kit is a real preset file. project_build never rebuilds it
        # from samples and never inserts a stand-in: without a verified load
        # path (the OS loader is disabled under a redirected bridge root, i.e.
        # here) the drum track is created EMPTY and its writes are blocked as
        # PRESET_LOAD_REQUIRED. allow_lossy_kit is reported as ignored.
        built = server.dispatch_tool("project_build", {"plan_path": str(kit_plan_path), "dry_run": False, "kit": kit_adg, "allow_lossy_kit": True, "tracks": ["KICK", "BASS", "PAD"], "device_map": {"PAD": "Wavetable"}, "wait_seconds": 5})
        kick = next(t for t in built["tracks"] if t["track"] == "KICK")
        by_track = {}
        for w in built["writes"]:
            by_track.setdefault(w["track"], set()).add((w["status"], w.get("code")))
        check("a plan kit that cannot be loaded is PRESET_LOAD_REQUIRED: the track is created empty, nothing is rebuilt, no stand-in device, no drum write",
              kick["instrument"] == "skipped" and bridge.live.track("KICK")["devices"] == [] and "build_drum_kit" not in bridge.live.applied
              and kick.get("kit", {}).get("status") == "PRESET_LOAD_REQUIRED" and kick["preset"]["status"] == "PRESET_LOAD_REQUIRED"
              and by_track.get("KICK") == {("blocked", "PRESET_LOAD_REQUIRED")} and built.get("allow_lossy_kit", "").startswith("ignored"), (kick, by_track.get("KICK"), built.get("allow_lossy_kit")))
        applied = bridge.live.applied
        clip_idx = [i for i, op in enumerate(applied) if op == "write_arrangement_clip"]
        locator_idx = [i for i, op in enumerate(applied) if op == "create_locator"]
        if bass_corpus:
            check("the other tracks are unaffected: bass (native Operator) is written, the device-mapped chord track is blocked by EVIDENCE, the build is partial",
                  by_track.get("BASS") == {("OK", None)} and all("no_native_role_and_genre_candidate" in w.get("reason", "") for w in built["writes"] if w["track"] == "PAD") and built["status"] == "partial", by_track)
            check("... and the op order is tempo, tracks, clips, locators -- locators last, no kit build anywhere",
                  applied[0] == "set_tempo" and clip_idx and locator_idx and applied.index("create_midi_track") < min(clip_idx)
                  and max(clip_idx) < min(locator_idx), applied)
        else:
            # A clean checkout (CI) has no Sensei bass corpus, so the bass
            # track has nothing to write; the ordering that remains provable
            # is tempo first, tracks before locators, and no kit build.
            print("  --  SKIPPED: bass write and clip-order checks need the Sensei bass corpus (not on this machine)")
            check("without a bass corpus no clip is written and the op order is still tempo, tracks, locators -- no kit build anywhere",
                  applied[0] == "set_tempo" and not clip_idx and "build_drum_kit" not in applied
                  and (not locator_idx or applied.index("create_midi_track") < min(locator_idx)), applied)

    # The OS load path, simulated: the loader is what a Finder double-click
    # does, so a fake one puts the preset into the fake set. What is proven is
    # the MCP's gating around it -- where the preset must land, what is read
    # back, and that no MIDI goes anywhere without that.
    kit_pads = sorted(int(p["note"]) for p in server.resolve_kit_reference(kit_adg)["pads"])
    kit_name = server.resolve_kit_reference(kit_adg)["kit"]
    real_available = server._os_preset_loader_available
    real_open = server._os_open
    try:
        server._os_preset_loader_available = lambda: (True, "simulated loader (test)")
        reset_root()
        with FakeExtensionBridge(BRIDGE_ROOT, FakeSet()) as bridge:
            def load_onto_kick(app, path):
                track = bridge.live.track("KICK")
                track["devices"].append(kit_name)
                track["pad_notes"] = list(kit_pads)
                # what Live would report per chain: the preset's own devices (+ the chain mixer)
                kit_file = server.resolve_kit_reference(kit_adg)
                # what Live reports per chain (measured with 0.4.3): className "Device", name = the pad's sample
                track["pad_devices"] = {int(p["note"]): ["Device", "AudioBranchMixerDevice"] for p in kit_file["pads"]}
                track["pad_device_names"] = {int(p["note"]): [Path(p["sample"]).stem, "Chain Mixer"] for p in kit_file["pads"]}
                bridge.publish_state()
            server._os_open = load_onto_kick
            built = server.dispatch_tool("project_build", {"plan_path": str(kit_plan_path), "dry_run": False, "kit": kit_adg, "tracks": ["KICK", "BASS"], "wait_seconds": 5})
            kick = next(t for t in built["tracks"] if t["track"] == "KICK")
            check("the preset landing on the target track is verified (index and name, only that track changed) and the kit is read back: pads equal the file's decoded pads, each pad's device is named after the file's sample, the device CLASS is stated as unreadable through the SDK",
                  kick["preset"]["verified"] is True and kick["preset"]["via"] == "os_open" and kick.get("kit", {}).get("status") == "native_preset_loaded"
                  and kick["kit"]["pad_notes"] == kit_pads and kick["kit"]["device_evidence"]["verified"] is True and kick["kit"]["device_evidence"]["device_class_verified"] is None
                  and kick["kit"]["device_evidence"]["pads_whose_device_is_named_after_the_files_sample"] == len(kit_pads) and kick["preset_identity_verified"] is True, kick)
            kick_writes = [w for w in built["writes"] if w["track"] == "KICK"]
            either("drums are written only after that, onto the kit's own pads, and every written note is one of Live's reported pads",
                   lambda: kick_writes and all(w["status"] == "OK" for w in kick_writes) and "build_drum_kit" not in bridge.live.applied
                   and kick.get("target_notes") and set(kick["target_notes"]) <= set(kit_pads) and kick.get("notes_outside_pads") == []
                   and kick["pad_targets"] and all(t["target_note"] in kit_pads for t in kick["pad_targets"]),
                   lambda: kick_writes and all(w["status"] == "blocked" for w in kick_writes) and "build_drum_kit" not in bridge.live.applied,
                   (kick.get("target_notes"), kick.get("notes_outside_pads"), [w["status"] for w in kick_writes]))
        reset_root()
        with FakeExtensionBridge(BRIDGE_ROOT, FakeSet()) as bridge:
            bridge.live.add_track("Other", devices=[])  # a track that was selected when the file arrived
            def load_onto_other(app, path):
                bridge.live.track("Other")["devices"].append(kit_name)  # the user clicked another track meanwhile
                bridge.publish_state()
            server._os_open = load_onto_other
            built = server.dispatch_tool("project_build", {"plan_path": str(kit_plan_path), "dry_run": False, "kit": kit_adg, "tracks": ["KICK", "BASS"], "wait_seconds": 5})
            kick = next(t for t in built["tracks"] if t["track"] == "KICK")
            check("a preset that lands on another track is PRESET_LANDED_ELSEWHERE: the build aborts before any clip, nothing is written anywhere",
                  kick["preset"]["status"] == "PRESET_LANDED_ELSEWHERE" and "write_arrangement_clip" not in bridge.live.applied and "create_locator" not in bridge.live.applied
                  and all(w["status"] in ("blocked", "aborted", "muted_by_plan") for w in built["writes"]) and built["status"] in ("blocked", "failed", "partial", "indeterminate"), (kick["preset"], built["status"], bridge.live.applied))
    finally:
        server._os_preset_loader_available = real_available
        server._os_open = real_open
    with FakeExtensionBridge(BRIDGE_ROOT, FakeSet()) as bridge:
        # Two sections with the same name (a set with two "ES" cues, measured): every clip and locator still gets its own key.
        twin_plan = dict(kit_plan, locators=[{"id": "a", "name": "ES", "start_bar": 1, "end_bar": 4, "energy": 40}, {"id": "b", "name": "Verse", "start_bar": 5, "end_bar": 8, "energy": 80},
                                             {"id": "c", "name": "ES", "start_bar": 9, "end_bar": 12, "energy": 40}],
                         tracks=[{"ableton_name": "BASS", "sensei_role": "bass", "instrument_family": "Operator"}])
        twin_path = kit_dir / "twin_plan.json"; twin_path.write_text(json.dumps(twin_plan), encoding="utf-8")
        applied_before = len(bridge.live.applied)  # the enclosing fake bridge stays; a second consumer on the same root would be another session
        twin = server.dispatch_tool("project_build", {"plan_path": str(twin_path), "dry_run": False, "wait_seconds": 5})
        es = [w for w in twin["writes"] if w["section"] == "ES"]
        either("two sections with the same name are both written and both locators exist: repeated names get their start bar in the key, unique names keep theirs",
               lambda: len(es) == 2 and all(w["status"] == "OK" for w in es) and bridge.live.applied[applied_before:].count("create_locator") == 3
               and all(s.get("verified") for s in twin["session_steps"] if s["kind"] == "locator"),
               lambda: len(es) == 2 and all(w["status"] == "blocked" for w in es) and bridge.live.applied[applied_before:].count("create_locator") == 3
               and all(s.get("verified") for s in twin["session_steps"] if s["kind"] == "locator"),
               (es, twin.get("session_steps"), bridge.live.applied[applied_before:]))
        preset_plan = dict(kit_plan, tracks=[{"ableton_name": "KEYS2", "sensei_role": "chord", "instrument_family": "Some Browser Preset"}])
        preset_path = kit_dir / "preset_plan.json"; preset_path.write_text(json.dumps(preset_plan), encoding="utf-8")
        blocked = server.dispatch_tool("project_build", {"plan_path": str(preset_path), "dry_run": False, "wait_seconds": 5})
        pad = blocked["tracks"][0]
        check("a preset-named instrument the SDK cannot load is reported as needs_preset with the two ways out, its writes blocked",
              pad.get("needs_preset") and "device_map" in pad["needs_preset"]["options"][0] and all(w["status"] == "blocked" for w in blocked["writes"]), pad)
    shutil.rmtree(kit_dir, ignore_errors=True)

    # ---- 10) two plan_create calls never share files (needs node) --------------
    if shutil.which("node") and (ROOT / "ArrangementGPS" / "node_modules" / "openai").is_dir():
        results: dict[str, dict] = {}

        def make(label, prompt):
            results[label] = server.dispatch_tool("plan_create", {"prompt": prompt})

        threads = [threading.Thread(target=make, args=("a", "dark rolling tech house, 126 bpm, in F minor")),
                   threading.Thread(target=make, args=("b", "karanlık gece şarkısı, 90 bpm, in D minor"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=240)
        a, b = results.get("a") or {}, results.get("b") or {}
        try:
            check("parallel plans get separate run directories", a.get("run_dir") and b.get("run_dir") and a["run_dir"] != b["run_dir"], (a.get("run_dir"), b.get("run_dir")))
            plan_a = json.loads(Path(a["plan_path"]).read_text(encoding="utf-8"))
            plan_b = json.loads(Path(b["plan_path"]).read_text(encoding="utf-8"))
            check("each plan carries its own prompt's tempo", plan_a["project"]["bpm"] == 126 and plan_b["project"]["bpm"] == 90, (plan_a["project"]["bpm"], plan_b["project"]["bpm"]))
            check("a Turkish prompt builds a package directory that exists", Path(b["build_dir"]).is_dir() and b["action_list_file"], b.get("build_dir"))
            check("every stage of a run wrote inside that run's directory", Path(a["build_dir"]).is_relative_to(Path(a["run_dir"])) and Path(a["plan_path"]).is_relative_to(Path(a["run_dir"])), a)
        finally:
            for r in (a, b):
                if r.get("run_dir") and Path(r["run_dir"]).is_relative_to(ROOT / "ArrangementGPS" / "engine" / "runs"):
                    shutil.rmtree(r["run_dir"], ignore_errors=True)

    else:
        print("  --  SKIPPED: parallel plan_create checks need node and `npm ci` in ArrangementGPS/ (the engine imports the openai package)")
    # --- deletion: what an earlier build left behind (delete_track / delete_locator) ---
    reset_root()
    with FakeExtensionBridge(BRIDGE_ROOT, FakeSet()) as bridge:
        made = server.dispatch_tool("live_command", {"op": "create_midi_track", "name": "10-Riser Basic", "instrument_family": "Operator", "wait_seconds": 5})
        check("the stray track exists before the delete checks", made["status"] == "OK" and bridge.live.track("10-Riser Basic")["devices"] == ["Operator"], made)
        refused = server.dispatch_tool("live_command", {"op": "delete_track", "name": "10-Riser Basic", "wait_seconds": 5})
        check("a track with devices is refused until the caller names them", refused["status"] == "REFUSED_IN_LIVE" and "track_has_devices" in str(refused.get("error")) and len(bridge.live.tracks) == 1, refused)
        wrong = server.dispatch_tool("live_command", {"op": "delete_track", "name": "10-Riser Basic", "expected_devices": ["Riser Basic"], "wait_seconds": 5})
        check("a wrong device expectation is refused", wrong["status"] == "REFUSED_IN_LIVE" and "devices_differ" in str(wrong.get("error")), wrong)
        bridge.live.track("10-Riser Basic")["arrangement"].append({"name": "user", "start": 0, "end": 4, "notes": []})
        clipped = server.dispatch_tool("live_command", {"op": "delete_track", "name": "10-Riser Basic", "expected_devices": ["Operator"], "wait_seconds": 5})
        check("a track holding a clip is never deleted, even with the devices named", clipped["status"] == "REFUSED_IN_LIVE" and "track_has_clips" in str(clipped.get("error")) and len(bridge.live.tracks) == 1, clipped)
        bridge.live.track("10-Riser Basic")["arrangement"].clear()
        gone = server.dispatch_tool("live_command", {"op": "delete_track", "name": "10-Riser Basic", "index": 0, "expected_devices": ["Operator"], "wait_seconds": 5})
        check("an empty track with its devices named exactly is deleted and verified", gone["status"] == "OK" and gone["result"]["deleted"] is True and gone["result"]["verified"] is True and bridge.live.tracks == [], gone)
        server.dispatch_tool("live_command", {"op": "create_locator", "beat": 64, "name": "Drop", "wait_seconds": 5})
        wrongname = server.dispatch_tool("live_command", {"op": "delete_locator", "beat": 64, "name": "Verse", "wait_seconds": 5})
        check("a locator is not deleted under another name", wrongname["status"] == "REFUSED_IN_LIVE" and "name_mismatch" in str(wrongname.get("error")) and len(bridge.live.cues) == 1, wrongname)
        cut = server.dispatch_tool("live_command", {"op": "delete_locator", "beat": 64, "name": "Drop", "wait_seconds": 5})
        check("the locator at the beat with that name is deleted and verified", cut["status"] == "OK" and cut["result"]["deleted"] is True and bridge.live.cues == [], cut)
        missing = server.dispatch_tool("live_command", {"op": "delete_locator", "beat": 64, "wait_seconds": 5})
        check("deleting a locator that is not there is refused, not silently ok", missing["status"] == "REFUSED_IN_LIVE" and "locator_not_found" in str(missing.get("error")), missing)
finally:
    shutil.rmtree(BRIDGE_ROOT, ignore_errors=True)
    shutil.rmtree(OUTPUT_ROOT, ignore_errors=True)
    shutil.rmtree(plan_dir, ignore_errors=True)

print("%d checks passed:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
if failures:
    print("FAILED:")
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("EXTENSION-ONLY MCP PATH WORKS")
