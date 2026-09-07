#!/usr/bin/env python3
"""The MCP against the REAL extension queue code: bridge.ts over a fake Live.

tests/fake_live_host.ts in the extension package runs the extension's own
bridge (src/bridge.ts, unchanged) as a separate Node process over a fake
set, in a temporary directory. The MCP talks to it exactly as it talks to
Live: through bridge_client, and once over real stdio. What is proven, on
both sides of the file contract at once:

  * a write applied by the real consumer comes back OK with a structured outcome
  * a request the consumer claimed but has not finished is INDETERMINATE
  * a host that dies after the mutation and before the outcome leaves the
    MCP with INDETERMINATE; after the host restarts, a retry with the same
    key is INDETERMINATE (indeterminate_earlier_attempt), never re-applied,
    and project_build carries that up as status "indeterminate" with the
    side effects listed -- no automatic retry anywhere
  * a Live session change between resolving the target and sending is
    STALE_SESSION and nothing is written
  * the same answers arrive intact through the MCP's stdio protocol

Needs node and the extension's node_modules (tsx). Without them the suite
reports SKIPPED and exits 0 -- it is never counted as passed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXT = ROOT / "extension"
HOST = EXT / "tests" / "fake_live_host.ts"
TSX = EXT / "node_modules" / ".bin" / "tsx"

if not shutil.which("node") or not TSX.exists():
    print("SKIPPED: needs node and the extension's node_modules (npm install in extension/)")
    sys.exit(0)

BRIDGE_ROOT = Path(tempfile.mkdtemp(prefix="loom_real_bridge_"))
os.environ["LOOM_BRIDGE_ROOT"] = str(BRIDGE_ROOT)
spec = importlib.util.spec_from_file_location("loom_server_real", ROOT / "mcp_server" / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
bc = server.bridge_client

checks, failures = [], []


def check(label, condition, detail=""):
    (checks if condition else failures).append(label if condition else "%s  %s" % (label, str(detail)[:500]))


class Host:
    """One fake_live_host.ts process."""

    def __init__(self, session: str, *, hold_file: Path | None = None, crash_after_mutation: bool = False) -> None:
        self.session = session
        self.snapshot = BRIDGE_ROOT / f"snapshot_{session}.json"
        self.snapshot.unlink(missing_ok=True)  # {} until THIS process mutates something
        argv = [str(TSX), str(HOST), "--root", str(BRIDGE_ROOT), "--session", session, "--snapshot", str(self.snapshot)]
        if hold_file:
            argv += ["--hold-file", str(hold_file)]
        if crash_after_mutation:
            argv.append("--crash-after-mutation")
        self.process = subprocess.Popen(argv, cwd=str(EXT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.lines: list[str] = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                break
            self.lines.append(line.rstrip())
            if line.startswith("ready "):
                break
        # Wait for THIS session's first state publish. "ready" is printed
        # before startBridge's async publish lands, and the file may still
        # hold the previous host's session: under CI load the MCP then read
        # the old session id, sent the request, and the consumer refused it
        # as session_mismatch instead of the client refusing STALE_SESSION.
        state = BRIDGE_ROOT / "state" / "live_state.json"
        while time.monotonic() < deadline:
            try:
                if json.loads(state.read_text(encoding="utf-8")).get("session_id") == session:
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(0.05)
        else:
            raise RuntimeError(f"fake host {session} never published its state")

    def snapshot_data(self) -> dict:
        try:
            return json.loads(self.snapshot.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def wait_exit(self, seconds: float = 10) -> int | None:
        try:
            return self.process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return None

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)

    def __enter__(self) -> "Host":
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()


def request_files() -> list[str]:
    return sorted(p.name for p in (BRIDGE_ROOT / "requests").glob("*.json")) if (BRIDGE_ROOT / "requests").exists() else []


NOTES = [{"pitch": 36, "start": 0, "duration": 0.5, "velocity": 100}, {"pitch": 38, "start": 1, "duration": 0.5, "velocity": 90}]
plan_path = BRIDGE_ROOT / "plan.json"
plan_path.write_text(json.dumps({
    "project": {"name": "Real Consumer", "bpm": 126, "key": "F Minor", "genre": "Trap", "total_bars": 8},
    "locators": [{"id": "intro", "name": "Intro", "start_bar": 1, "end_bar": 4, "energy": 30},
                 {"id": "hook", "name": "Hook", "start_bar": 5, "end_bar": 8, "energy": 90}],
    "tracks": [{"ableton_name": "KICK", "sensei_role": "drum", "instrument_family": "Drum Rack"},
               {"ableton_name": "Vocal", "sensei_role": None}],
}), encoding="utf-8")

try:
    # ---- 1) the happy path through the real consumer -------------------------
    with Host("live-A") as host:
        status = server.dispatch_tool("live_bridge_status", {})
        check("the real extension bridge publishes the protocol this client speaks",
              status["mutations_allowed"] is True and status["protocol"]["published"] == "loom.bridge/3" and status["bridge"]["session_id"] == "live-A", status.get("protocol"))
        answer = server.dispatch_tool("live_command", {"op": "set_tempo", "bpm": 131, "wait_seconds": 10})
        check("set_tempo is applied by bridge.ts and comes back OK with a structured outcome",
              answer["status"] == "OK" and answer["outcome"]["kind"] == "applied" and answer["result"]["after"] == 131 and host.snapshot_data().get("tempo") == 131, answer)
        written = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 1, "length_beats": 4, "name": "Intro", "notes": NOTES, "idempotency_key": "real:clip:KICK:Intro", "wait_seconds": 10})
        check("an arrangement clip is written and verified note-for-note by the real consumer",
              written["status"] == "OK" and written["result"]["verified_notes_match"] is True and host.snapshot_data()["tracks"][0]["arrangement"][0]["notes"] == 2, written)
        again = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 1, "length_beats": 4, "name": "Intro", "notes": NOTES, "idempotency_key": "real:clip:KICK:Intro", "wait_seconds": 10})
        check("the same key is replayed from the real journal, not written twice",
              again["status"] == "OK" and again.get("replayed") is True and len(host.snapshot_data()["tracks"][0]["arrangement"]) == 1, again)
        refused = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 1, "length_beats": 4, "name": "Other", "notes": NOTES, "wait_seconds": 10})
        check("a second clip over Loom's own is refused without on_conflict, as REFUSED_IN_LIVE with a code",
              refused["status"] == "REFUSED_IN_LIVE" and refused["outcome"]["code"] == "owned_clip_needs_policy" and refused["outcome"]["applied"] is False, refused)
        unsupported = server.dispatch_tool("live_command", {"op": "transport", "action": "play", "wait_seconds": 2})
        check("an SDK-less op never reaches the consumer", unsupported["status"] == "UNSUPPORTED_BY_SDK" and request_files() == [], unsupported)
        journal = json.loads((BRIDGE_ROOT / "state" / "live_state.json").read_text())["journal"]
        check("the state carries the journal summary", journal["state"] == "present" and journal["entries"] >= 2, journal)

    # ---- 2) claimed and in flight: INDETERMINATE, then the outcome lands ------
    hold = BRIDGE_ROOT / "hold"
    hold.write_text("hold")
    with Host("live-A", hold_file=hold) as host:
        answer = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 5, "length_beats": 4, "name": "Hook", "notes": NOTES, "idempotency_key": "real:clip:KICK:Hook", "wait_seconds": 0.8})
        check("a request the real consumer claimed but has not finished is INDETERMINATE with side effects and a next step",
              answer["status"] == "INDETERMINATE" and answer["consumed"] is True and answer["outcome"]["kind"] == "indeterminate" and answer["outcome"]["side_effects"] and answer["outcome"]["next_step"], answer)
        check("the claim is in processing/, out of the MCP's reach", (BRIDGE_ROOT / "processing" / Path(answer["request_file"]).name).exists() and request_files() == [])
        hold.unlink()
        deadline = time.monotonic() + 10
        while not (BRIDGE_ROOT / "done" / Path(answer["request_file"]).name).exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        check("the in-flight mutation completes after the MCP gave up", (BRIDGE_ROOT / "done" / Path(answer["request_file"]).name).exists() and host.snapshot_data()["tracks"][0]["arrangement"][0]["name"] == "Hook", host.snapshot_data())
        retry = server.dispatch_tool("midi_write_arrangement", {"track": "KICK", "start_bar": 5, "length_beats": 4, "name": "Hook", "notes": NOTES, "idempotency_key": "real:clip:KICK:Hook", "wait_seconds": 10})
        check("a retry with the same key after the outcome landed is replayed OK, not applied again", retry["status"] == "OK" and retry.get("replayed") is True and len(host.snapshot_data()["tracks"][0]["arrangement"]) == 1, retry)

    # ---- 3) the host dies after the mutation, before the outcome --------------
    crashed = Host("live-B", crash_after_mutation=True)
    try:
        answer = server.dispatch_tool("midi_write_arrangement", {"track": "BASS", "start_bar": 1, "length_beats": 4, "name": "Riff", "notes": NOTES, "idempotency_key": "real:clip:BASS:Riff", "wait_seconds": 1.5})
        exit_code = crashed.wait_exit(5)
        check("the host exited hard after applying the mutation", exit_code == 137 and crashed.snapshot_data()["tracks"][1]["arrangement"] != [], (exit_code, crashed.snapshot_data()))
        check("the MCP is left with INDETERMINATE (real bridge.ts, real crash)", answer["status"] == "INDETERMINATE" and answer["outcome"]["kind"] == "indeterminate", answer)
        claimed = BRIDGE_ROOT / "processing" / Path(answer["request_file"]).name
        check("the claim file and the journal's started line are what remain", claimed.exists() and '"status":"started"' in (BRIDGE_ROOT / "state" / "journal.jsonl").read_text(), sorted(p.name for p in (BRIDGE_ROOT / "processing").glob("*")))
    finally:
        crashed.kill()

    # the host restarts (same Live session id: same set, host restarted)
    with Host("live-B") as host:
        recovered = BRIDGE_ROOT / "errors" / claimed.name
        check("on restart bridge.ts answers the interrupted request indeterminate_after_restart and re-applies nothing",
              recovered.exists() and json.loads(recovered.read_text())["outcome"]["code"] == "indeterminate_after_restart" and host.snapshot_data() == {}, recovered.exists())
        retry = server.dispatch_tool("midi_write_arrangement", {"track": "BASS", "start_bar": 1, "length_beats": 4, "name": "Riff", "notes": NOTES, "idempotency_key": "real:clip:BASS:Riff", "wait_seconds": 10})
        check("a retry with the same key is INDETERMINATE through the MCP: indeterminate_earlier_attempt, applied unknown, nothing written",
              retry["status"] == "INDETERMINATE" and retry["outcome"]["code"] == "indeterminate_earlier_attempt" and retry["outcome"]["applied"] is None
              and retry["outcome"]["earlier_request_id"] == Path(answer["request_file"]).stem and host.snapshot_data() == {}, retry)
        # project_build over the same key space: the build's tempo key never ran, but the crash left a started clip key
        # under a different key space; here the build's own steps run and the interrupted key is simulated by the journal.
        target = server.resolve_bridge_target()
        plan_digest = server.hashlib.sha256(plan_path.read_bytes()).hexdigest()[:12]
        run_key = f"{target.session_id}:{plan_digest}:reuse-{plan_digest}"
        # Pre-seed the journal with a started (interrupted) entry for the build's first clip key,
        # exactly as a crash mid-build would leave it.
        started = {"key": f"key:{run_key}:clip:KICK:Intro", "status": "started", "request_id": "req_interrupted", "op": "write_arrangement_clip",
                   "session": "live-B", "payload_hash": "unknown", "at": time.time()}
        with (BRIDGE_ROOT / "state" / "journal.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(started) + "\n")
        build = server.dispatch_tool("project_build", {"plan_path": str(plan_path), "dry_run": False, "wait_seconds": 10})
        kick_writes = [w for w in build["writes"] if w["track"] == "KICK"]
        check("project_build carries INDETERMINATE up: overall status indeterminate, the interrupted clip listed with its side effects, later writes aborted",
              build["status"] == "indeterminate" and kick_writes[0]["status"] == "INDETERMINATE" and kick_writes[0]["bridge_outcome"]["code"] == "indeterminate_earlier_attempt"
              and any(item["step"] == "clip:KICK/Intro" and item["side_effects"] for item in build["indeterminate"])
              and all(w["status"] in ("aborted", "INDETERMINATE", "muted_by_plan") for w in kick_writes), {"status": build.get("status"), "writes": build.get("writes"), "indeterminate": build.get("indeterminate")})
        snapshot = host.snapshot_data()
        check("the build did not retry the interrupted clip: KICK holds no arrangement clip",
              not any(t["name"] == "KICK" and t["arrangement"] for t in snapshot.get("tracks", [])), snapshot)

    # ---- 4) session change between resolve and send ---------------------------
    with Host("live-C"):
        target = server.resolve_bridge_target()
    with Host("live-D") as host:
        answer = bc.submit_request({"op": "set_tempo", "bpm": 77}, 5, target=target)
        check("a target resolved for an earlier Live session is refused as STALE_SESSION, nothing written",
              answer["status"] == "STALE_SESSION" and request_files() == [] and host.snapshot_data() == {}, answer)

    # ---- 5) the same answers over real stdio ----------------------------------
    hold.write_text("hold")
    with Host("live-E", hold_file=hold) as host:
        env = dict(os.environ, LOOM_BRIDGE_ROOT=str(BRIDGE_ROOT))
        proc = subprocess.Popen([sys.executable, str(ROOT / "mcp_server" / "server.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=str(ROOT), env=env)
        next_id = [0]

        def call(method, params=None):
            next_id[0] += 1
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": next_id[0], "method": method, "params": params or {}}) + "\n")
            proc.stdin.flush()
            while True:
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError("server closed")
                message = json.loads(line)
                if message.get("id") == next_id[0]:
                    return message

        def tool(name, arguments):
            result = call("tools/call", {"name": name, "arguments": arguments})["result"]
            return result.get("isError", False), result.get("structuredContent") or json.loads(result["content"][0]["text"])

        try:
            call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "real-consumer-test", "version": "1"}})
            is_error, payload = tool("live_command", {"op": "set_tempo", "bpm": 140, "wait_seconds": 10})
            check("over stdio: a mutation applied by the real consumer is OK with its outcome in structuredContent",
                  not is_error and payload["status"] == "OK" and payload["outcome"]["kind"] == "applied" and host.snapshot_data().get("tempo") == 140, payload)
            is_error, payload = tool("midi_write_arrangement", {"track": "KICK", "start_bar": 1, "length_beats": 4, "name": "Held", "notes": NOTES, "wait_seconds": 0.8})
            check("over stdio: a claimed, unfinished request is INDETERMINATE with the structured outcome intact",
                  not is_error and payload["status"] == "INDETERMINATE" and payload["outcome"]["kind"] == "indeterminate" and payload["outcome"]["next_step"], payload)
            is_error, payload = tool("live_bridge_status", {})
            check("over stdio: the diagnosis shows the request in flight", not is_error and len(payload.get("in_flight") or []) == 1, payload.get("in_flight"))
        finally:
            hold.unlink(missing_ok=True)
            proc.stdin.close()
            proc.wait(timeout=10)
finally:
    shutil.rmtree(BRIDGE_ROOT, ignore_errors=True)

print("%d checks passed:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
if failures:
    print("FAILED:")
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("REAL EXTENSION CONSUMER + MCP WORK TOGETHER")
