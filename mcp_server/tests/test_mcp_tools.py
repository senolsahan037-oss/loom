#!/usr/bin/env python3
"""Call every MCP tool over real stdio. Live is not required.

What this proves: the server is up, every tool's schema and handler line up,
and each one runs against real data and returns the fields it promises.
What it does not prove: what Ableton Live does with any of those outputs.

The two tools with side effects (writing a bridge request, appending to the gap
log) are called and then cleaned up; the tool that writes an .als is dry-run
only.
"""
import json
import tempfile
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "mcp_server" / "server.py"
# The server has no third-party dependency, so it runs on whatever Python is
# running this test. Hardcoding a venv path breaks a clean clone and CI.
PYTHON = Path(sys.executable)
GAP_LOG = ROOT / "Docs" / "MISSING_CONTROLS_LOG.md"
# The server's bridge directory is redirected to a scratch folder BEFORE the
# server is spawned, so no test request can reach a running Live. Until this,
# the two write checks below went to ~/Documents/SenseiV2Bridge on the
# assumption that Live was closed -- and with Live open they put a real clip
# named "MCP selftest" into the user's set every time the suite ran.
BRIDGE_ROOT = Path(tempfile.mkdtemp(prefix="loom_tools_bridge_"))
os.environ["LOOM_BRIDGE_ROOT"] = str(BRIDGE_ROOT)
BRIDGE_REQUESTS = BRIDGE_ROOT / "requests"
# A personal project (Turtle) is the richest sample and is used wherever it
# is found -- it has moved once already, so several locations are tried; the
# committed fixture is the fallback. Checks that need Turtle's own tracks
# stay tied to it.
_TURTLE_CANDIDATES = (
    Path.home() / "Desktop" / "solo" / "Turtle.als",
    Path.home() / "Desktop" / "solo" / "Ableton New" / "Turtle.als",
    Path.home() / "Desktop" / "solo" / "Turtle Project" / "Turtle.als",
)
_FIXTURE_ALS = ROOT / "AIMixMaster" / "tests" / "fixtures" / "drum_buss_before.als"
SAMPLE_ALS = next((p for p in _TURTLE_CANDIDATES if p.exists()), _FIXTURE_ALS)
# A project with ten automation envelopes; no committed fixture carries
# automation yet, so these checks run only where the project exists.
AUTOMATED_ALS = next((p for p in (
    Path.home() / "Desktop" / "solo" / "overdozz Project" / "overdozz.als",
    Path.home() / "Desktop" / "solo" / "Ableton New" / "overdozz Project" / "overdozz.als",
) if p.exists()), Path.home() / "Desktop" / "solo" / "overdozz Project" / "overdozz.als")

GAP_MARKER = "MCP_SELFTEST_ENTRY_DO_NOT_KEEP"


class Server:
    def __init__(self):
        self.process = subprocess.Popen(
            [str(PYTHON), str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, cwd=str(ROOT),
        )
        self.next_id = 0

    def call(self, method, params=None):
        self.next_id += 1
        request = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        # The server may interleave notifications (progress, cancellations)
        # with answers; only the line carrying this request's id is the answer.
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("server closed the connection")
            message = json.loads(line)
            if message.get("id") == self.next_id:
                return message

    def tool(self, name, arguments=None):
        response = self.call("tools/call", {"name": name, "arguments": arguments or {}})
        result = response.get("result", {})
        text = (result.get("content") or [{}])[0].get("text", "")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"_raw": text}
        return result.get("isError", False), payload

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)


checks = []
failures = []


def check(label, condition, detail=""):
    if condition:
        checks.append(label)
    else:
        failures.append("%s  %s" % (label, detail))


def main():
    server = Server()
    created_request = None
    # Each chain run leaves a new build directory and the renderer leaves a new
    # job file. Unless the test clears up after itself, every run adds another
    # folder to the repo.
    created_build_dir = None
    created_job_file = None
    try:
        init = server.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "selftest", "version": "1"}})
        check("initialize answers", init.get("result", {}).get("serverInfo", {}).get("name") == "loom-mcp", init)

        # The server paginates now; the client has to follow the cursor.
        listed = []
        cursor = None
        while True:
            page = server.call("tools/list", {"cursor": cursor} if cursor else {})["result"]
            listed.extend(page["tools"])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        names = [tool["name"] for tool in listed]
        # An exact count, so a tool quietly disappearing is caught. The message
        # carries the names because a bare number tells you something moved but
        # not what.
        check("44 tools are published", len(names) == 44, sorted(names))
        check("every tool has an inputSchema", all("inputSchema" in tool for tool in listed))
        check("tool names are unique", len(set(names)) == len(names))

        is_error, payload = server.tool("__does_not_exist__")
        check("an unknown tool returns an error", is_error, payload)

        # --- ArrangementGPS: the real chain ---
        is_error, payload = server.tool("plan_create", {"prompt": "dark rolling tech house, 126 bpm, hypnotic bassline"})
        check("the chain runs from a prompt", not is_error, payload)
        if not is_error:
            check("the tempo in the prompt reached the plan", payload["project"]["bpm"] == 126, payload["project"])
            check("all 5 chain steps ran", len(payload["steps"]) == 5, payload["steps"])
            check("17 tracks, 7 locators", payload["tracks_total"] == 17 and payload["locators"] == 7, payload)
            check("6 tracks are within Sensei's scope", payload["tracks_sensei_can_generate"] == 6, payload)
            check("the action list file was really written", payload["action_list_file"] and Path(payload["action_list_file"]).exists(), payload["action_list_file"])
            check("it does not produce an empty task list", payload["tracks_total"] > 0, payload)
            created_build_dir = Path(payload["build_dir"])

        is_error, payload = server.tool("plan_verify")
        check("plan verification runs", not is_error, payload)
        if not is_error:
            check("the plan passes Sensei's catalog", payload["ok"], payload["failures"])
            check("out-of-scope lanes do not count as failures", len(payload["out_of_scope"]) == 11, payload["out_of_scope"])

        is_error, payload = server.tool("library_search", {"role": "drum", "genre": "House", "limit": 5})
        check("library search works with role and genre", not is_error, payload)
        if not is_error:
            check("results are Sensei-verified", payload["results"] and all(r["sensei_verified"] for r in payload["results"]), payload)
            check("every result is in the requested role", all(r["role"] == "drum" for r in payload["results"]), payload)
            check("the catalog was really read", payload["catalog_size"] > 5000, payload["catalog_size"])

        # --- AIMixMaster: gercek .als ---
        if SAMPLE_ALS.exists():
            is_error, payload = server.tool("project_inspect", {"als_path": str(SAMPLE_ALS)})
            check("project inspection runs", not is_error and payload.get("tracks"), payload)

            is_error, payload = server.tool("project_inspect_arrangement", {"als_path": str(SAMPLE_ALS)})
            check("arrangement inspection runs", not is_error, payload)
            if not is_error:
                check("sections are inferred from clip boundaries", payload["section_count"] >= 1, payload)
                check("tempo is read", payload["tempo"], payload)

            is_error, payload = server.tool("render_plan", {"als_path": str(SAMPLE_ALS)})
            check("the render manifest is produced from a real project", not is_error, payload)
            if not is_error:
                check("there is a decision for every track", payload["track_count"] > 0, payload)
                check("the reason is stated for anything that cannot be rendered", all(item["reason"] for item in payload["excluded"]), payload["excluded"][:3])
                check("the manifest was written to disk", Path(payload["job_path"]).exists(), payload["job_path"])
                created_job_file = Path(payload["job_path"])

            is_error, payload = server.tool("project_analyze_mixer", {"als_path": str(SAMPLE_ALS)})
            check("mixer analysis runs", not is_error, str(payload)[:200])
            if not is_error:
                check("a real gain staging report comes back", payload.get("schema_version") and payload.get("markdown"), list(payload))
                check("the master chain is inspected", "limiter_status" in payload.get("master", {}), payload.get("master"))
                check("the hardcoded -6 dB target is gone", "gain_staging_target_db" not in payload, list(payload))
                check("a record is produced per track", payload["track_count"] > 0, payload["track_count"])

            is_error, payload = server.tool("project_analyze_clips", {"als_path": str(SAMPLE_ALS)})
            check("clip alignment analysis runs", not is_error, str(payload)[:200])
            if not is_error:
                check("the alignment report produces markdown", bool(payload.get("markdown")), list(payload))

            is_error, payload = server.tool("drumbuss_read", {"als_path": str(SAMPLE_ALS)})
            check("drum buss state is read", not is_error, str(payload)[:200])
            if not is_error:
                check("the absence of a drum buss is stated explicitly", "has_drum_buss" in payload, list(payload))

            is_error, payload = server.tool("drumbuss_build", {"als_path": str(SAMPLE_ALS)})
            # With no source track an error is the correct behaviour; what
            # matters is that it does not write.
            check("the drum buss dry run does not touch the .als", is_error or payload.get("applied") is False, payload)
        else:
            print("  --  sample .als checks skipped: neither the fixture nor a personal project is here (%s)" % SAMPLE_ALS)

        if AUTOMATED_ALS.exists():
            is_error, payload = server.tool("automation_read", {"als_path": str(AUTOMATED_ALS)})
            check("automation inspection runs", not is_error, str(payload)[:200])
            if not is_error:
                check("automation envelopes are found", payload["envelope_count"] == 10, payload["envelope_count"])
                check("every target resolves", payload["unresolved_targets"] == 0, payload["unresolved_targets"])
                check("the lack of automation writing is stated explicitly", payload["write_supported"] is False, payload)
        else:
            print("  --  automation checks skipped: no project with automation on this machine (%s)" % AUTOMATED_ALS)

        # --- Presetor ---
        is_error, payload = server.tool("chain_evidence", {"role": "kick"})
        check("chain evidence arrives per role", not is_error, str(payload)[:200])
        if not is_error:
            check("there is evidence for kick", payload["has_recommendation"], payload)
            check("every device carries how many tracks it was seen on",
                  all("presence" in item and "occurrences" in item for item in payload["devices"]), payload.get("devices"))
            check("the evidence states how many tracks back it", payload["role_sample"] >= 10, payload.get("role_sample"))

        is_error, payload = server.tool("chain_evidence", {"role": "__yok_boyle_bir_rol__"})
        check("a role with no evidence yields NO recommendation",
              not is_error and payload["has_recommendation"] is False, payload)

        if SAMPLE_ALS.exists():
            is_error, payload = server.tool("chain_plan", {"als_path": str(SAMPLE_ALS)})
            check("the chain plan is produced from the project", not is_error, str(payload)[:200])
            if not is_error:
                check("there is one plan row per track", payload["track_count"] > 0, payload["track_count"])
                check("plan statuses are counted", payload["status_counts"], payload.get("status_counts"))

            busy = next((p["track"] for p in payload.get("plans", []) if p["status"] == "already_has_chain"), None)
            if busy:
                is_error, payload = server.tool("chain_apply", {
                    "als_path": str(SAMPLE_ALS), "target_track": busy, "donor_track": busy,
                })
                check("even a dry run does not write to a track that has a chain",
                      is_error or payload.get("applied") is False, payload)

        # --- AISoundDesigner ---
        is_error, payload = server.tool("palette_read", {"role": "bass"})
        check("the sound palette arrives per role", not is_error, str(payload)[:200])
        if not is_error:
            check("there is a bass palette", payload["has_palette"], payload)
            check("every sample carries how many projects it spans",
                  all(item["projects"] >= 2 for item in payload["samples"]), payload.get("samples", [])[:3])

        is_error, payload = server.tool("palette_read", {})
        check("the palette summary reports the bounce share",
              not is_error and 0 < payload["bounce_share"] < 1, str(payload)[:200])

        if SAMPLE_ALS.exists():
            is_error, payload = server.tool("project_sound_sources", {"als_path": str(SAMPLE_ALS)})
            check("a project's sound sources are read", not is_error, str(payload)[:200])
            if not is_error:
                check("the track count is reported", payload["track_count"] > 0, payload["track_count"])

        is_error, payload = server.tool("projects_arrangement_shapes", {"roots": [str(Path.home() / "Desktop" / "solo")], "limit": 3})
        check("arrangement shape extraction runs", not is_error, payload)
        if not is_error:
            check("the number of scanned projects is reported", payload["scanned"] == 3, payload["scanned"])

        # --- Sensei ---
        is_error, payload = server.tool("midi_generate", {"role": "drum", "genre": "Hip Hop", "bars": 4, "seed": 7})
        check("MIDI variation generation answers", not is_error, str(payload)[:200])

        before = set(BRIDGE_REQUESTS.glob("*.json")) if BRIDGE_REQUESTS.exists() else set()
        # The bridge is the test's own scratch folder, so nothing consumes the
        # request: the tool waits and then says so, rather than claiming success.
        is_error, payload = server.tool("midi_write_to_live", {
            "name": "MCP selftest", "length_beats": 4.0,
            "notes": [{"pitch": 36, "start": 0.0, "duration": 0.5, "velocity": 100}],
            "wait_seconds": 1.0,
        })
        check("the bridge write reports its outcome", not is_error, payload)
        if not is_error:
            created_request = Path(payload["request_file"])
            check("the request file really exists on disk", created_request.exists(), str(created_request))
            check("if Live did not consume it, that is stated plainly",
                  payload["status"] in ("NOT_CONSUMED", "WRITTEN_TO_LIVE", "REJECTED_BY_LIVE"), payload["status"])
            check("the consumed field is measured, not guessed",
                  isinstance(payload["consumed"], bool), payload.get("consumed"))
            after = set(BRIDGE_REQUESTS.glob("*.json"))
            check("exactly one request was added to the queue", len(after - before) == 1, len(after - before))

        is_error, payload = server.tool("midi_write_to_live", {
            "name": "MCP selftest blind", "length_beats": 4.0,
            "notes": [{"pitch": 36, "start": 0.0, "duration": 0.5, "velocity": 100}],
            "wait_seconds": 0,
        })
        check("wait_seconds=0 is labelled as a blind enqueue",
              not is_error and payload["status"] == "QUEUED" and payload["consumed"] is None, payload)
        if not is_error:
            Path(payload["request_file"]).unlink(missing_ok=True)

        # --- Automation writing ---
        if SAMPLE_ALS.exists():
            is_error, payload = server.tool("automation_write", {
                "als_path": str(SAMPLE_ALS), "track": "1-Viral Kit",
                "parameter": "volume", "unit": "db",
                "points": [{"time": 0, "value": -12}, {"time": 16, "value": 0}],
            })
            check("the automation dry run runs", not is_error, str(payload)[:200])
            if not is_error:
                check("the dry run does not write to the .als", payload["applied"] is False, payload)
                check("the parameter's real range is read", payload["parameter_range"][1] > 1, payload.get("parameter_range"))
                check("the target PointeeId resolves", payload["pointee_id"], payload.get("pointee_id"))

            is_error, payload = server.tool("automation_write", {
                "als_path": str(SAMPLE_ALS), "track": "1-Viral Kit",
                "parameter": "volume", "unit": "db", "points": [{"time": 0, "value": 40}],
            })
            check("a value outside the range is refused", is_error and "outside the parameter range" in str(payload), str(payload)[:140])

            is_error, payload = server.tool("automation_write", {
                "als_path": str(SAMPLE_ALS), "track": "1-Viral Kit",
                "parameter": "pan", "points": [{"time": 16, "value": 0.5}, {"time": 4, "value": -0.5}],
            })
            check("time going backwards is refused", is_error and "backwards" in str(payload), str(payload)[:140])

            is_error, payload = server.tool("automation_list_targets", {
                "als_path": str(SAMPLE_ALS), "track": "1-Viral Kit", "contains": "gain", "limit": 5})
            check("automatable parameters are listed", not is_error, str(payload)[:160])
            if not is_error:
                check("every parameter carries a target id and a range",
                      all(p.get("pointee_id") and p.get("min") is not None for p in payload["parameters"]),
                      payload["parameters"][:2])
                check("parameters with no declared range are left out", payload["writable"] < payload["total"], (payload["writable"], payload["total"]))
                check("the filter is applied", all("gain" in p["tag"].lower() for p in payload["parameters"]), payload["parameters"][:2])
                check("the limit is not exceeded", payload["returned"] <= 5, payload["returned"])

            is_error, payload = server.tool("render_verify", {
                "als_path": str(SAMPLE_ALS), "renders_dir": str(Path.home() / "Desktop"),
            })
            check("render validation runs (soundfile installed)", not is_error, str(payload)[:160])

        # --- Mix Check (ported measurement engine) ---

        is_error, payload = server.tool("mix_profiles")

        check("mix_profiles lists the stored genre profiles", not is_error and len(payload.get("profiles") or []) >= 6, payload)

        try:

            import numpy as _np

            import soundfile as _sf

            tone_path = Path(tempfile.mkdtemp()) / "tone.wav"

            _t = _np.arange(48_000 * 2) / 48_000

            _sf.write(tone_path, _np.column_stack((0.1 * _np.sin(2 * _np.pi * 220 * _t), 0.08 * _np.sin(2 * _np.pi * 220 * _t))), 48_000, subtype="FLOAT")

            is_error, payload = server.tool("mix_measure", {"path": str(tone_path)})

            check("mix_measure returns direct signal values for a known tone",

                  not is_error and payload.get("channels") == 2 and payload.get("analysis_status") == "ok"

                  and abs((payload.get("sample_peak_dbfs") or 0) - (-20.0)) < 0.2, payload)

            is_error, payload = server.tool("mix_analyze", {"path": str(tone_path), "analysis_stage": "master", "use_closest_profile": True})

            check("mix_analyze compares a master against the nearest profile and says it is not a classification",

                  not is_error and payload.get("mode") in ("affinity", "general") and "not a genre classification" in str(payload.get("genre_affinity_notice")), payload if is_error else payload.get("mode"))

            check("the waveform envelope is omitted unless asked", (payload.get("mix") or {}).get("waveform", {}).get("omitted") is True, (payload.get("mix") or {}).get("waveform"))

            is_error, payload = server.tool("mix_analyze", {"path": str(tone_path), "genre": "no-such-genre"})

            check("an unknown genre profile is refused with the known ids", is_error and "known" in str(payload), payload)

        except ImportError:

            print("  --  mix_measure check skipped: numpy/soundfile not installed here")


        # --- Crate agent (reader + sampler under one trigger) ---


        try:


            import numpy as _np


            import soundfile as _sf


            crate_dir = Path(tempfile.mkdtemp())


            click = crate_dir / "click.wav"


            _sr, _bpm = 44_100, 120.0


            _sig = _np.zeros(int(_sr * 0.5 * 4 * 8), dtype=_np.float32)


            for _i in range(32):


                _s = int(_i * 0.5 * _sr); _n = int(0.06 * _sr)


                _sig[_s:_s + _n] += 0.6 * _np.exp(-_np.linspace(0, 6, _n)) * _np.sin(2 * _np.pi * 220 * _np.arange(_n) / _sr)


            _sf.write(click, _np.column_stack((_sig, _sig)), _sr, subtype="FLOAT")


            is_error, payload = server.tool("crate_read", {"path": str(click)})


            check("crate_read measures the file and carries the reader's refusal fields",


                  not is_error and payload.get("ok") is True and "tempo_reason" in payload, payload)


            is_error, payload = server.tool("crate_chop", {"path": str(click), "modes": ["fixed"], "out_dir": str(crate_dir / "packs"), "name": "mcpclick", "seconds": 2.0, "keep_source": False})


            check("crate_chop writes a pack with a manifest", not is_error and payload.get("slices_total", 0) > 0 and Path(payload.get("manifest", "")).exists(), payload)


            is_error, payload = server.tool("crate_agent", {"source": str(click), "dry_run": True})


            check("crate_agent dry run plans from the reading and writes nothing",


                  not is_error and payload.get("dry_run") is True and payload.get("plan", {}).get("reason") and "pack" not in payload, payload)


            is_error, payload = server.tool("crate_chop", {"path": str(click), "modes": ["magic"]})


            check("an unknown chop mode is refused", is_error and "unknown chop mode" in str(payload), payload)


        except ImportError:


            print("  --  crate checks skipped: numpy/soundfile not installed here")



        # --- crate_to_live / mix_from_live without a Live: honest refusals ---



        is_error, payload = server.tool("crate_to_live", {"path": "/nowhere/slice.wav", "track": "Vocal"})



        check("crate_to_live refuses a missing file before touching Live", is_error and "no audio file" in str(payload), payload)



        is_error, payload = server.tool("mix_from_live", {"track": "Vocal", "start_beat": 0, "end_beat": 8, "wait_seconds": 0.3})



        check("mix_from_live without a Live says no render came back", not is_error and payload.get("measurement") is None and payload.get("render", {}).get("status") in ("NOT_CONSUMED", "FAILED_IN_LIVE"), payload)




        # --- Telemetry ---
        is_error, payload = server.tool("live_bridge_status")
        check("bridge status is read", not is_error and "bridge_root" in payload, payload)
        check("live_bridge_status lists every bridge root with its freshness",
              isinstance(payload.get("bridge_candidates"), list) and any(c.get("active") for c in payload["bridge_candidates"]),
              payload.get("bridge_candidates"))

        gap_before = GAP_LOG.read_text(encoding="utf-8") if GAP_LOG.exists() else ""
        is_error, payload = server.tool("gap_record", {
            "category": "Telemetry",
            "description": GAP_MARKER,
            "observed_behavior": GAP_MARKER,
            "required_implementation": GAP_MARKER,
        })
        check("gap recording works", not is_error, payload)
        gap_after = GAP_LOG.read_text(encoding="utf-8") if GAP_LOG.exists() else ""
        check("the gap was really written to the right file", GAP_MARKER in gap_after, GAP_LOG)
        if GAP_MARKER in gap_after and gap_before:
            GAP_LOG.write_text(gap_before, encoding="utf-8")
            check("the test entry was cleaned out of the gap log", GAP_MARKER not in GAP_LOG.read_text(encoding="utf-8"))
    finally:
        if created_request and created_request.exists():
            created_request.unlink()
        if created_job_file and created_job_file.exists():
            created_job_file.unlink()
        if created_build_dir and created_build_dir.exists() and created_build_dir.parent.name == "Builds":
            shutil.rmtree(created_build_dir)
        # --- GAP-003: bars are converted with a real time signature, not an assumed 4/4 --
            plan_file = ROOT / "ArrangementGPS" / "engine" / "output" / "ableton_session_plan.json"
            if plan_file.exists():
                _, build = server.tool("project_build", {"plan_path": str(plan_file), "dry_run": True, "beats_per_bar": 3})
                beats = [step["beat"] for step in build.get("session_steps", []) if step.get("kind") == "locator"]
                check("an explicit beats_per_bar drives every locator beat",
                      beats == [(s - 1) * 3 for s in (1, 9, 25, 33, 49, 57, 73)], beats)
                check("the response says where the beats-per-bar came from",
                      build.get("beats_per_bar") == 3 and build.get("beats_per_bar_source") == "explicit",
                      (build.get("beats_per_bar"), build.get("beats_per_bar_source")))
                plan_tracks = json.loads(plan_file.read_text(encoding="utf-8")).get("tracks") or []
                statuses = {tr.get("status") for tr in build.get("tracks") or []}
                check("a dry run lists every plan track with a create/exists verdict",
                      len(build.get("tracks") or []) == len(plan_tracks)
                      and statuses <= {"exists", "would_create", "unknown_no_session"},
                      (len(build.get("tracks") or []), len(plan_tracks), statuses))
                check("track verdicts are totalled like the writes are",
                      sum((build.get("track_totals") or {}).values()) == len(plan_tracks), build.get("track_totals"))
                check("the song key is a session step before any write",
                      any(s.get("kind") == "key" and s.get("root") and s.get("mode") for s in build.get("session_steps", [])),
                      [s.get("kind") for s in build.get("session_steps", [])])
                _, fallback = server.tool("project_build", {"plan_path": str(plan_file), "dry_run": True})
                check("without a session or an explicit value, 4/4 is an admitted assumption",
                      fallback.get("beats_per_bar_source") in ("live_session", "live_session_via_surface", "assumed_4_4"), fallback.get("beats_per_bar_source"))
            else:
                print("  --  GAP-003 check skipped: no session plan on this machine (run plan_create once)")

        server.close()

    # --- role -> real profile id (the polyphonic default never existed) --------
    import importlib.util
    spec = importlib.util.spec_from_file_location("loom_server", ROOT / "mcp_server" / "server.py")
    loom_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loom_server)
    catalog_file = ROOT / "Sensei" / "data" / "instrument_capabilities" / "instrument_target_profiles.jsonl"
    known = set()
    if catalog_file.exists():
        known = {json.loads(line).get("profile_id") for line in catalog_file.read_text(encoding="utf-8").splitlines() if line.strip()}
    picks = {
        ("chord", "Electric Piano Daze"): "ableton.chord.electric-piano.v1",
        ("chord", "Warm Pad"): "ableton.chord.pad.v1",
        ("chord", None): "ableton.chord.piano.v1",
        ("bass", "Basic Analog Bass"): "ableton.bass.synth.v1",
        ("bass", "808 Sub"): "ableton.bass.808.v1",
        ("drum", "Drum Rack"): None,
    }
    for (role, family), expected in picks.items():
        got = loom_server._profile_for_role(role, family)
        check("profile for %s / %s is %s" % (role, family, expected), got == expected, got)
    if known:
        bad = {pid for _, pid in loom_server._CHORD_FAMILY_PROFILES + loom_server._BASS_FAMILY_PROFILES
               if pid not in known} | {pid for pid in loom_server._ROLE_DEFAULT_PROFILE.values() if pid not in known}
        check("every default profile id exists in this machine's catalogue", not bad, sorted(bad))
    else:
        print("  --  profile catalogue check skipped: no instrument_target_profiles.jsonl on this machine")

    print("%d checks passed:" % len(checks))
    for label in checks:
        print("  ok  %s" % label)
    if failures:
        print()
        print("FAILED:")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("ALL TOOLS WORK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
