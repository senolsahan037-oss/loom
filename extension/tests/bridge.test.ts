// Headless proof of the extension-side bridge: fakes stand in for Live, a
// temp directory stands in for the extension's storage. Every op the MCP can
// send is exercised through the real file contract (requests/ -> done/ or
// errors/), so what is proven here is exactly what mcp_server/server.py sees.
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync, readdirSync, unlinkSync, renameSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  BRIDGE_PROTOCOL,
  BridgeError,
  CAPABILITIES,
  JOURNAL_COMPACT_LINES,
  JOURNAL_MAX_FOREIGN,
  JOURNAL_RETENTION_SECONDS,
  applyOperation,
  bridgeDirs,
  compactJournal,
  ensureBridgeDirs,
  processRequestFile,
  publishState,
  readJournal,
  recoverProcessing,
  ClipLedger,
  captureState,
  refusalFor,
  type JournalEntry,
  type Outcome,
} from "../src/bridge.js";
import { FakeClip, FakeLive, FakeSimpler, faults } from "./fakes.js";

const SESSION = "test-session";
const checks: string[] = [];
const failures: string[] = [];
// A failed check is recorded and the run goes on, so one run shows every
// broken guarantee instead of the first one; the exit code is set at the end.
function ok(label: string, condition: boolean, detail?: unknown) {
  if (!condition) {
    const line = `${label}${detail === undefined ? "" : ` -- ${JSON.stringify(detail, (_k, v) => (typeof v === "bigint" ? String(v) : v))}`}`;
    console.error(`FAILED: ${line}`);
    failures.push(line);
    return;
  }
  checks.push(label);
}
async function rejects(label: string, fn: () => Promise<unknown>, needle: string) {
  try {
    await fn();
    ok(label, false, "did not throw");
  } catch (error) {
    ok(label, error instanceof BridgeError && error.message.includes(needle), error instanceof Error ? error.message : error);
  }
}
const apply = (live: FakeLive, payload: Record<string, unknown>, ledger?: ClipLedger) => applyOperation(live, payload, { sessionId: SESSION, ledger });
const tempDirs = (prefix: string) => {
  const dirs = bridgeDirs(mkdtempSync(join(tmpdir(), prefix)));
  ensureBridgeDirs(dirs);
  return dirs;
};
const outcomeOf = (record: Record<string, unknown> | null) => (record?.outcome ?? {}) as Partial<Outcome>;

async function main() {
  const live = new FakeLive();

  // --- state --------------------------------------------------------------
  const state = await apply(live, { op: "get_state" });
  ok("state carries the schema the MCP already reads", state.schema_version === "sensei.bridge.v2" && state.track_count === 3);
  ok("state names the queue protocol, separately from the package and SDK versions",
     state.bridge_protocol === "loom.bridge/3" && String(state.surface_version).startsWith("loom-extension/") && state.sdk_api_version === "1.0.0" && Array.isArray(state.protocol_features), state);
  ok("what the SDK lacks is null, not guessed", state.is_playing === null && state.signature_numerator === null && state.current_song_time === null);
  ok("capabilities are published so the MCP can refuse early", (state.capabilities as typeof CAPABILITIES).transport === false && (state.capabilities as typeof CAPABILITIES).arrangement_clips === true);
  const tracks = state.tracks as Array<Record<string, unknown>>;
  ok("mixer values come from Live's own parameters", (tracks[0].volume as Record<string, unknown>).value === 0.85 && tracks[2].has_midi_input === false);

  // --- tempo / mixer / device parameters ----------------------------------
  const tempo = await apply(live, { op: "set_tempo", bpm: 126 });
  ok("set_tempo reports before/after from Live", tempo.before === 120 && tempo.after === 126 && live.tempo === 126);
  await rejects("a tempo outside Live's range is refused", () => apply(live, { op: "set_tempo", bpm: 5 }), "outside");
  const mixer = await apply(live, { op: "set_mixer", track: "BASS", volume: 0.5, mute: true });
  ok("set_mixer changes exactly what was asked", (mixer.changes as Record<string, { after: unknown }>).volume.after === 0.5 && live.tracks[1].mute === true);
  await rejects("a mixer value outside the parameter range is refused", () => apply(live, { op: "set_mixer", track: "BASS", pan: 2 }), "outside Live's range");
  const param = await apply(live, { op: "set_device_parameter", track: "KICK", device: "EQ Eight", parameter: "Gain A", value: -6 });
  ok("device parameter write is range-checked and read back", param.before === 0 && param.after === -6);
  await rejects("an unknown device is refused with the count", () => apply(live, { op: "set_device_parameter", track: "KICK", device: "Nope", parameter: "x", value: 1 }), "found 0");
  const listed = await apply(live, { op: "list_device_parameters", track: "KICK", device: "EQ Eight" });
  ok("parameters are listed with their ranges", (listed.parameters as Array<Record<string, unknown>>)[0].max === 15);

  // --- locators -----------------------------------------------------------
  const cue = await apply(live, { op: "create_locator", beat: 32, name: "Verse 1" });
  ok("a locator is created, named and verified from the cue list", cue.created === true && cue.verified === true && live.cuePoints[0].name === "Verse 1");
  const adopted = await apply(live, { op: "create_locator", beat: 32, name: "Verse" });
  ok("a cue already on the beat is adopted and renamed, never duplicated", adopted.adopted === true && live.cuePoints.length === 1 && live.cuePoints[0].name === "Verse");

  // --- deletion: only what is named exactly and holds no clip -----------------
  const refusedWith = async (label: string, fn: () => Promise<unknown>, code: string, kind: string = "refused") => {
    try {
      await fn();
      ok(label, false, "did not throw");
    } catch (error) {
      const outcome = error instanceof BridgeError ? error.outcome : {};
      ok(label, error instanceof BridgeError && outcome.code === code && (outcome.kind ?? "refused") === kind, error instanceof Error ? `${error.message} ${JSON.stringify(outcome)}` : error);
    }
  };
  ok("deletion is published as a capability", CAPABILITIES.track_delete === true && CAPABILITIES.locator_delete === true);
  const strayTrack = await apply(live, { op: "create_midi_track", name: "10-Riser Basic", instrument_family: "Operator" });
  const trackCount = live.tracks.length;
  await refusedWith("a track with devices is not deleted until the caller names them", () => apply(live, { op: "delete_track", name: "10-Riser Basic" }), "track_has_devices");
  await refusedWith("a wrong device expectation refuses the delete", () => apply(live, { op: "delete_track", name: "10-Riser Basic", expected_devices: ["Riser Basic"] }), "devices_differ");
  await refusedWith("a wrong index refuses the delete even with the right name", () => apply(live, { op: "delete_track", name: "10-Riser Basic", index: 0, expected_devices: ["Operator"] }), "index_mismatch");
  await refusedWith("a track with clips on it is never deleted (KICK holds the user's EQ and a clip)", async () => {
    live.tracks[0].arrangement.push(new FakeClip(0, 4));
    try { await apply(live, { op: "delete_track", name: "KICK", expected_devices: ["EQ Eight"] }); } finally { live.tracks[0].arrangement.pop(); }
  }, "track_has_clips");
  await refusedWith("a session clip alone blocks the delete", async () => {
    live.tracks[1].clipSlots[0].clip = new FakeClip(0, 4);
    try { await apply(live, { op: "delete_track", name: "BASS" }); } finally { live.tracks[1].clipSlots[0].clip = null; }
  }, "track_has_clips");
  live.deleteFails = "Live refused: transport running";
  await refusedWith("an SDK rejection is reported failed with the track verified still there", () => apply(live, { op: "delete_track", name: "10-Riser Basic", expected_devices: ["Operator"] }), "delete_failed", "failed");
  live.deleteFails = null;
  ok("nothing was deleted by the refused and failed attempts", live.tracks.length === trackCount && live.tracks.some((t) => t.name === "10-Riser Basic"));
  const deleted = await apply(live, { op: "delete_track", name: "10-Riser Basic", index: strayTrack.index as number, expected_devices: ["Operator"] });
  ok("an empty track with its devices named exactly is deleted and verified from the track list", deleted.deleted === true && deleted.verified === true && deleted.track_count_after === trackCount - 1 && !live.tracks.some((t) => t.name === "10-Riser Basic"), deleted);
  await refusedWith("deleting a track that is not there is refused, not silently ok", () => apply(live, { op: "delete_track", name: "10-Riser Basic" }), "track_not_found");
  await refusedWith("a locator that is not at the beat is refused", () => apply(live, { op: "delete_locator", beat: 33 }), "locator_not_found");
  await refusedWith("a locator is not deleted under another name", () => apply(live, { op: "delete_locator", beat: 32, name: "Chorus" }), "name_mismatch");
  const cueGone = await apply(live, { op: "delete_locator", beat: 32, name: "Verse" });
  ok("the locator at the beat with that name is deleted and verified from the cue list", cueGone.deleted === true && cueGone.verified === true && cueGone.name === "Verse" && live.cuePoints.length === 0, cueGone);

  // --- clip protection: the shared cases (tests/fixtures/clip_policy_cases.json) ---
  const cases = JSON.parse(readFileSync(join(process.cwd(), "..", "tests", "fixtures", "clip_policy_cases.json"), "utf8")) as { cases: Array<Record<string, any>> };
  const policyLive = new FakeLive();
  const bass = policyLive.tracks[1];
  const userRiff = new FakeClip(32, 16);
  userRiff.name = "user riff";
  bass.arrangement.push(userRiff);
  const owned = new FakeClip(64, 16);
  owned.name = "Verse";
  bass.arrangement.push(owned);
  const sessionUser = new FakeClip(0, 4);
  sessionUser.name = "user session clip";
  bass.clipSlots[0].clip = sessionUser;
  const policyLedger = new ClipLedger(null, SESSION);
  policyLedger.record("arrangement", "BASS", "Verse", 64, 80);
  for (const testCase of cases.cases) {
    const payload = JSON.parse(JSON.stringify(testCase.payload));
    for (const note of payload.notes ?? []) if (note.start === "nan") note.start = Number.NaN;
    let outcome: "written" | "refused" = "written";
    let detail: unknown = null;
    try {
      detail = await apply(policyLive, payload, policyLedger);
    } catch (error) {
      outcome = "refused";
      detail = error instanceof Error ? error.message : String(error);
    }
    const reasonOk = testCase.expect === "refused" ? String(detail).includes(testCase.reason_contains) : true;
    const replacedOk = testCase.replaced === undefined || (detail as Record<string, unknown>).replaced === testCase.replaced;
    const countOk = testCase.clip_count === undefined || bass.arrangement.length === testCase.clip_count;
    ok(`policy case ${testCase.id}`, outcome === testCase.expect && reasonOk && replacedOk && countOk, { outcome, detail, clips: bass.arrangement.length });
  }
  ok("the user's clips are exactly where they were", bass.arrangement.some((c) => c.name === "user riff" && c.startTime === 32) && bass.clipSlots[0].clip === sessionUser);

  // --- rollback: a failed note write leaves nothing behind -------------------
  const rollbackLive = new FakeLive();
  const kick = rollbackLive.tracks[0];
  const before = new FakeClip(0, 8);
  before.name = "existing";
  kick.arrangement.push(before);
  faults.failNextNoteWrite = true;
  let rollbackOutcome: Partial<Outcome> = {};
  try {
    await apply(rollbackLive, { op: "write_arrangement_clip", track: "KICK", start_beat: 16, length_beats: 8, name: "Intro", notes: [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }] });
    ok("a note write Live refuses is reported", false);
  } catch (error) {
    rollbackOutcome = error instanceof BridgeError ? error.outcome : {};
    ok("a note write Live refuses is reported as failed with nothing applied", rollbackOutcome.kind === "failed" && rollbackOutcome.applied === false, rollbackOutcome);
  }
  ok("after the failure the new clip is gone and the old one untouched", kick.arrangement.length === 1 && kick.arrangement[0] === before, kick.arrangement.map((c) => c.name));
  faults.failNextNoteWrite = true;
  try {
    await apply(rollbackLive, { op: "write_clip", track: "KICK", slot: 1, name: "s", length_beats: 4, notes: [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }] });
    ok("a failed session write throws", false);
  } catch {
    ok("a failed session write leaves the slot empty again", kick.clipSlots[1].clip === null);
  }

  // --- the happy path still works, and a rebuild replaces only its own clip --
  const notes = [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }, { pitch: 38, start: 1, duration: 0.5, velocity: 90 }];
  const happyLedger = new ClipLedger(null, SESSION);
  const written = await apply(live, { op: "write_arrangement_clip", track: "KICK", start_beat: 16, length_beats: 8, name: "Intro", notes }, happyLedger);
  ok("an arrangement clip is written and read back note-for-note", written.verified_note_count === 2 && written.clip_name === "Intro" && live.tracks[0].arrangement.length === 1);
  ok("notes reach the SDK in its own shape", live.tracks[0].arrangement[0].notes[0].startTime === 0 && live.tracks[0].arrangement[0].notes[1].pitch === 38);
  const rebuilt = await apply(live, { op: "write_arrangement_clip", track: "KICK", start_beat: 16, length_beats: 8, name: "Intro", on_conflict: "replace_owned", notes }, happyLedger);
  ok("rewriting the same range with replace_owned replaces this bridge's own clip", live.tracks[0].arrangement.length === 1 && rebuilt.replaced === 1);
  await rejects("an audio track is refused for MIDI", () => apply(live, { op: "write_arrangement_clip", track: "Vocal", start_beat: 0, length_beats: 4, notes }), "not a MIDI track");

  // --- session clip (the default op) ----------------------------------------
  const session = await apply(live, { track: "BASS", name: "probe", length_beats: 4, notes }, happyLedger);
  ok("a request without op writes a session clip into the first empty slot", session.slot === 0 && session.note_count === 2 && live.tracks[1].clipSlots[0].clip?.name === "probe");

  // --- content verification: count is not enough --------------------------
  const verifyLive = new FakeLive();
  const verified = await apply(verifyLive, { op: "write_arrangement_clip", track: "KICK", start_beat: 0, length_beats: 4, name: "v", notes: [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }, { pitch: 38, start: 1, duration: 0.5, velocity: 100 }] });
  ok("a write reports that Live holds the notes note-for-note", verified.verified_notes_match === true && verified.verified_note_count === 2);
  const sessionVerified = await apply(verifyLive, { op: "write_clip", track: "BASS", slot: 0, name: "sv", length_beats: 4, notes: [{ pitch: 40, start: 0, duration: 1, velocity: 90 }] });
  ok("a session write verifies its notes the same way", sessionVerified.verified_notes_match === true && sessionVerified.verified_note_count === 1);

  // --- drum pad evidence comes from the device, or is absent -----------------
  const padLive = new FakeLive();
  padLive.tracks[0].drumPadNotes = [36, 38, 42, 46, 49];
  const pads = await apply(padLive, { op: "drum_pads", track: "KICK" });
  ok("drum_pads reads the pad notes the Drum Rack's chains receive", JSON.stringify(pads.pad_notes) === "[36,38,42,46,49]" && pads.verified === true);
  const noPads = await apply(padLive, { op: "drum_pads", track: "BASS" });
  ok("a track without a Drum Rack has no pads and says why", noPads.verified === false && (noPads.pad_notes as number[]).length === 0 && String(noPads.reason).includes("no Drum Rack"));
  ok("the state publishes the session id it was given and the capabilities", (await captureState(padLive, { session_id: "abc" })).session_id === "abc" && CAPABILITIES.drum_pads === true && CAPABILITIES.recording === false && CAPABILITIES.transport === false);

  // --- queue protocol: expiry, session, replay, atomic outcomes ---------------
  const now = Date.now() / 1000;
  ok("an expired request is refused before Live is touched", refusalFor({ op: "set_tempo", bpm: 1, expires_at: now - 5 }, SESSION, now)?.code === "expired_request");
  ok("a request for another Live session is refused", refusalFor({ op: "set_tempo", bpm: 1, target_session: "other" }, SESSION, now)?.code === "session_mismatch");
  ok("a live request for this session passes", refusalFor({ op: "set_tempo", bpm: 1, expires_at: now + 5, target_session: SESSION }, SESSION, now) === null);
  const queueLive = new FakeLive();
  const queueDirs = tempDirs("loom-queue-");
  const tempoBefore = queueLive.tempo;
  writeFileSync(join(queueDirs.requests, "req_expired.json"), JSON.stringify({ id: "req_expired", op: "set_tempo", bpm: 77, expires_at: now - 1 }));
  const expired = await processRequestFile(queueLive, queueDirs, "req_expired.json", SESSION);
  ok("an expired request lands in errors/ as a refusal and changes nothing", expired?.status === "error" && outcomeOf(expired).kind === "refused" && outcomeOf(expired).code === "expired_request" && existsSync(join(queueDirs.errors, "req_expired.json")) && queueLive.tempo === tempoBefore, expired);
  const clipRequest = { id: "req_clip_1", op: "write_arrangement_clip", track: "KICK", start_beat: 0, length_beats: 4, name: "once", idempotency_key: "build-1:clip:KICK:Intro", notes: [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }] };
  writeFileSync(join(queueDirs.requests, "req_clip_1.json"), JSON.stringify(clipRequest));
  const first = await processRequestFile(queueLive, queueDirs, "req_clip_1.json", SESSION);
  ok("an applied write carries the structured outcome", first?.status === "ok" && outcomeOf(first).kind === "applied" && outcomeOf(first).applied === true && outcomeOf(first).verified === true, first?.outcome);
  writeFileSync(join(queueDirs.requests, "req_clip_2.json"), JSON.stringify({ ...clipRequest, id: "req_clip_2" }));
  const second = await processRequestFile(queueLive, queueDirs, "req_clip_2.json", SESSION);
  ok("a retry with the same idempotency_key is answered from the journal, not written twice", first?.status === "ok" && second?.status === "ok" && second?.replayed === true && second?.id === "req_clip_2" && second?.replayed_from === "req_clip_1" && queueLive.tracks[0].arrangement.length === 1, { second, clips: queueLive.tracks[0].arrangement.length });
  ok("outcome files are published whole", !readdirSync(queueDirs.done).some((name) => name.endsWith(".tmp")) && JSON.parse(readFileSync(join(queueDirs.done, "req_clip_2.json"), "utf8")).id === "req_clip_2");
  // the result body survives a cleaned done/ directory
  for (const name of readdirSync(queueDirs.done)) unlinkSync(join(queueDirs.done, name));
  writeFileSync(join(queueDirs.requests, "req_clip_3.json"), JSON.stringify({ ...clipRequest, id: "req_clip_3" }));
  const third = await processRequestFile(queueLive, queueDirs, "req_clip_3.json", SESSION);
  ok("after done/ was cleaned the retry is still answered with the stored result, not re-applied", third?.status === "ok" && third?.replayed === true && (third?.result as Record<string, unknown>).verified_note_count === 1 && queueLive.tracks[0].arrangement.length === 1, third);
  const readAgain = await processRequestFile(queueLive, queueDirs, "req_state.json", SESSION).catch(() => null);
  ok("a read op is not journaled", readAgain === null && !readJournal(queueDirs).entries.has("id:req_state"));

  // --- 1) claim before apply: a request being applied is not withdrawable ---
  {
    const claimLive = new FakeLive();
    const claimDirs = tempDirs("loom-claim-");
    let release!: () => void;
    claimLive.tracks[0].holdCreate = new Promise<void>((resolve) => { release = resolve; });
    writeFileSync(join(claimDirs.requests, "req_held.json"), JSON.stringify({ id: "req_held", op: "write_arrangement_clip", track: "KICK", start_beat: 0, length_beats: 4, name: "held", notes: [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }] }));
    const inFlight = processRequestFile(claimLive, claimDirs, "req_held.json", SESSION);
    await new Promise((resolve) => setTimeout(resolve, 30));
    const stillInQueue = existsSync(join(claimDirs.requests, "req_held.json"));
    const claimed = existsSync(join(claimDirs.root, "processing", "req_held.json"));
    ok("a request is claimed (moved out of requests/) before Live is touched, so a withdrawal cannot succeed mid-mutation", !stillInQueue && claimed, { stillInQueue, claimed });
    let withdrawn = true;
    try { unlinkSync(join(claimDirs.requests, "req_held.json")); } catch { withdrawn = false; }
    ok("the MCP's withdrawal of a claimed request fails (so it cannot report NOT_CONSUMED)", !withdrawn);
    release();
    const heldRecord = await inFlight;
    ok("the held request still completes with its outcome in done/", heldRecord?.status === "ok" && existsSync(join(claimDirs.done, "req_held.json")) && !existsSync(join(claimDirs.root, "processing", "req_held.json")), heldRecord);
  }

  // --- 2) replace_owned must not lose the previous content ------------------
  {
    const lossLive = new FakeLive();
    const kick = lossLive.tracks[0];
    const lossLedger = new ClipLedger(null, SESSION);
    const oldNotes = [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }, { pitch: 38, start: 1, duration: 0.5, velocity: 90 }, { pitch: 42, start: 2, duration: 0.25, velocity: 80 }];
    const written = await apply(lossLive, { op: "write_arrangement_clip", track: "KICK", start_beat: 8, length_beats: 4, name: "Verse", notes: oldNotes }, lossLedger);
    ok("setup: Loom's own clip with content exists", written.verified_note_count === 3 && kick.arrangement.length === 1);
    const before = JSON.stringify(kick.arrangement[0].notes);
    faults.failNextNoteWrite = true;
    let replaceError = "";
    let replaceOutcome: Partial<Outcome> = {};
    try {
      await apply(lossLive, { op: "write_arrangement_clip", track: "KICK", start_beat: 8, length_beats: 4, name: "Verse", on_conflict: "replace_owned", notes: [{ pitch: 40, start: 0, duration: 1, velocity: 100 }] }, lossLedger);
    } catch (error) {
      replaceError = error instanceof Error ? error.message : String(error);
      replaceOutcome = error instanceof BridgeError ? error.outcome : {};
    }
    faults.failNextNoteWrite = false;
    const survivor = kick.arrangement.find((c) => c.startTime === 8);
    ok("a failed replace_owned leaves the previous clip and its content exactly as they were", survivor !== undefined && JSON.stringify(survivor.notes) === before && survivor.name === "Verse", { clips: kick.arrangement.map((c) => [c.name, c.notes.length]), replaceError });
    ok("the failure says the previous clip was kept, as a failed (not indeterminate) outcome", replaceError.length > 0 && /restored|kept|intact/.test(replaceError) && replaceOutcome.kind === "failed" && replaceOutcome.applied === false, { replaceError, replaceOutcome });
    const sessionLive = new FakeLive();
    const sessionLedger = new ClipLedger(null, SESSION);
    await apply(sessionLive, { op: "write_clip", track: "BASS", slot: 0, name: "loop", length_beats: 4, notes: oldNotes }, sessionLedger);
    const sessionBefore = JSON.stringify((sessionLive.tracks[1].clipSlots[0].clip as FakeClip).notes);
    faults.failNextNoteWrite = true;
    try {
      await apply(sessionLive, { op: "write_clip", track: "BASS", slot: 0, name: "loop", length_beats: 4, on_conflict: "replace_owned", notes: [{ pitch: 40, start: 0, duration: 1, velocity: 100 }] }, sessionLedger);
    } catch {
      // expected
    }
    faults.failNextNoteWrite = false;
    ok("a failed session replace_owned keeps the previous session clip and its notes", sessionLive.tracks[1].clipSlots[0].clip !== null && JSON.stringify((sessionLive.tracks[1].clipSlots[0].clip as FakeClip).notes) === sessionBefore, sessionLive.tracks[1].clipSlots[0].clip);
    const otherRange = new FakeLive();
    const otherLedger = new ClipLedger(null, SESSION);
    await apply(otherRange, { op: "write_arrangement_clip", track: "KICK", start_beat: 8, length_beats: 4, name: "Verse", notes: oldNotes }, otherLedger);
    let rangeError = "";
    try {
      await apply(otherRange, { op: "write_arrangement_clip", track: "KICK", start_beat: 10, length_beats: 4, name: "Verse", on_conflict: "replace_owned", notes: oldNotes }, otherLedger);
    } catch (error) {
      rangeError = error instanceof Error ? error.message : String(error);
    }
    ok("a replace that would need deleting the clip (different range) is refused before anything changes, because the SDK cannot restore a deleted clip", otherRange.tracks[0].arrangement.length === 1 && otherRange.tracks[0].arrangement[0].notes.length === 3 && /cannot restore|refus/.test(rangeError), { clips: otherRange.tracks[0].arrangement.length, rangeError });
  }

  // --- 3) ownership is not "same name and range" ----------------------------
  {
    const notesA = [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }];
    // A: recorded in another Live session (a ledger file left by an earlier run); B: the user's clip with the same name and range
    const ledgerPath = join(mkdtempSync(join(tmpdir(), "loom-ledger-")), "owned_clips.json");
    const earlier = new ClipLedger(ledgerPath, "an-earlier-live");  // the ledger a previous Live session left on disk
    earlier.record("arrangement", "KICK", "Verse", 8, 12);
    const setB = new FakeLive();
    const userClip = new FakeClip(8, 4);
    userClip.name = "Verse";
    userClip.notes = [{ pitch: 60, startTime: 0, duration: 4, velocity: 100 }];
    setB.tracks[0].arrangement.push(userClip);
    const laterLedger = new ClipLedger(ledgerPath, SESSION);
    let crossError = "";
    try {
      await apply(setB, { op: "write_arrangement_clip", track: "KICK", start_beat: 8, length_beats: 4, name: "Verse", on_conflict: "replace_owned", notes: notesA }, laterLedger);
    } catch (error) {
      crossError = error instanceof Error ? error.message : String(error);
    }
    ok("a user's clip with the same name and range as an entry from an earlier session is not treated as Loom's", setB.tracks[0].arrangement[0] === userClip && userClip.notes[0].pitch === 60, { crossError, notes: userClip.notes });
    // deleted and recreated in the same set
    const recreated = new FakeLive();
    const recLedger = new ClipLedger(null, SESSION);
    await apply(recreated, { op: "write_arrangement_clip", track: "KICK", start_beat: 8, length_beats: 4, name: "Verse", notes: notesA }, recLedger);
    recreated.tracks[0].arrangement = [];  // the user deletes it ...
    const fresh = new FakeClip(8, 4);       // ... and draws a new one in the same place
    fresh.name = "Verse";
    fresh.notes = [{ pitch: 61, startTime: 0, duration: 4, velocity: 100 }];
    recreated.tracks[0].arrangement.push(fresh);
    try {
      await apply(recreated, { op: "write_arrangement_clip", track: "KICK", start_beat: 8, length_beats: 4, name: "Verse", on_conflict: "replace_owned", notes: notesA }, recLedger);
    } catch {
      // expected
    }
    ok("a clip the user recreated where Loom's used to be is not overwritten", recreated.tracks[0].arrangement[0] === fresh && fresh.notes[0].pitch === 61, fresh.notes);
    // the user edited Loom's own clip afterwards
    const edited = new FakeLive();
    const edLedger = new ClipLedger(null, SESSION);
    await apply(edited, { op: "write_arrangement_clip", track: "KICK", start_beat: 8, length_beats: 4, name: "Verse", notes: notesA }, edLedger);
    const loomClip = edited.tracks[0].arrangement[0];
    loomClip.notes = [...loomClip.notes, { pitch: 72, startTime: 2, duration: 1, velocity: 70 }];  // the user's own addition
    try {
      await apply(edited, { op: "write_arrangement_clip", track: "KICK", start_beat: 8, length_beats: 4, name: "Verse", on_conflict: "replace_owned", notes: notesA }, edLedger);
    } catch {
      // expected
    }
    ok("a Loom clip the user edited afterwards is not silently overwritten", loomClip.notes.length === 2 && loomClip.notes.some((n) => n.pitch === 72), loomClip.notes);
    // a ledger that cannot be written refuses BEFORE the mutation ...
    const unwritable = new ClipLedger("/nonexistent-root-for-loom/owned_clips.json", SESSION);
    let ledgerError = "";
    try {
      unwritable.record("arrangement", "KICK", "x", 0, 4);
    } catch (error) {
      ledgerError = error instanceof Error ? error.message : String(error);
    }
    ok("a ledger write failure is reported, not swallowed", ledgerError.length > 0, ledgerError);
    const probeLive = new FakeLive();
    await rejects("a write is refused before Live is touched when the ledger cannot be written", () => apply(probeLive, { op: "write_arrangement_clip", track: "KICK", start_beat: 0, length_beats: 4, name: "x", notes: notesA }, unwritable), "ledger");
    ok("... and nothing was written", probeLive.tracks[0].arrangement.length === 0);
  }

  // --- 4) replay journal: intent before mutation, key bound to content and session ---
  {
    const jLive = new FakeLive();
    const jDirs = tempDirs("loom-journal-");
    let release!: () => void;
    jLive.tracks[0].holdCreate = new Promise<void>((resolve) => { release = resolve; });
    const request = { id: "req_j1", op: "write_arrangement_clip", track: "KICK", start_beat: 0, length_beats: 4, name: "j", idempotency_key: "build-9:clip:KICK:Intro", notes: [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }] };
    writeFileSync(join(jDirs.requests, "req_j1.json"), JSON.stringify(request));
    const inFlight = processRequestFile(jLive, jDirs, "req_j1.json", SESSION);
    await new Promise((resolve) => setTimeout(resolve, 30));
    const during = readJournal(jDirs);
    const startedEntry = during.entries.get("key:build-9:clip:KICK:Intro");
    ok("the journal records the key as started BEFORE the mutation runs, and a marker exists", startedEntry !== undefined && startedEntry.status === "started" && existsSync(jDirs.journalMarker), during);
    release();
    await inFlight;
    // same key, different payload
    writeFileSync(join(jDirs.requests, "req_j2.json"), JSON.stringify({ ...request, id: "req_j2", notes: [{ pitch: 40, start: 0, duration: 1, velocity: 100 }] }));
    const conflict = await processRequestFile(jLive, jDirs, "req_j2.json", SESSION);
    ok("the same key with different contents is a conflict, not a replay and not a second write", conflict?.status === "error" && outcomeOf(conflict).code === "idempotency_conflict" && outcomeOf(conflict).kind === "refused" && jLive.tracks[0].arrangement.length === 1, conflict);
    // same key, another session: the journal file left by an earlier Live
    const otherSetLive = new FakeLive();
    writeFileSync(join(jDirs.requests, "req_j3.json"), JSON.stringify({ ...request, id: "req_j3" }));
    const otherSet = await processRequestFile(otherSetLive, jDirs, "req_j3.json", "a-later-live");
    ok("an earlier session's success is not replayed into this session", otherSet?.status === "error" && otherSet?.replayed !== true && outcomeOf(otherSet).code === "replay_refused_other_session" && otherSetLive.tracks[0].arrangement.length === 0, otherSet);
    // a crash after the mutation, before the outcome was recorded
    const crashDirs = tempDirs("loom-crash-");
    writeFileSync(join(crashDirs.root, "processing", "req_c1.json"), JSON.stringify({ ...request, id: "req_c1" }));
    writeFileSync(crashDirs.journalMarker, JSON.stringify({ created_at: now }));
    writeFileSync(crashDirs.journalFile, `${JSON.stringify({ key: "key:build-9:clip:KICK:Intro", status: "started", request_id: "req_c1", session: SESSION, payload_hash: "x", at: now })}\n`);
    const crashLive = new FakeLive();
    writeFileSync(join(crashDirs.requests, "req_c2.json"), JSON.stringify({ ...request, id: "req_c2" }));
    const afterCrash = await processRequestFile(crashLive, crashDirs, "req_c2.json", SESSION);
    ok("after a crash mid-way, a retry with the same key is answered INDETERMINATE (structured) and NOT applied again",
       afterCrash?.status === "indeterminate" && outcomeOf(afterCrash).kind === "indeterminate" && outcomeOf(afterCrash).code === "indeterminate_earlier_attempt" && outcomeOf(afterCrash).applied === null
       && outcomeOf(afterCrash).earlier_request_id === "req_c1" && typeof outcomeOf(afterCrash).next_step === "string" && crashLive.tracks[0].arrangement.length === 0, afterCrash);
    const otherSessionRetry = await (async () => {
      writeFileSync(join(crashDirs.requests, "req_c2b.json"), JSON.stringify({ ...request, id: "req_c2b" }));
      return processRequestFile(crashLive, crashDirs, "req_c2b.json", "restarted-live");
    })();
    ok("the interrupted attempt is reported indeterminate even from a new session (a restarted host), not as 'other session'",
       outcomeOf(otherSessionRetry).code === "indeterminate_earlier_attempt" && outcomeOf(otherSessionRetry).earlier_session === SESSION && crashLive.tracks[0].arrangement.length === 0, otherSessionRetry);
    // an unreadable journal must not read as "never ran"
    writeFileSync(crashDirs.journalFile, `{corrupt\n${JSON.stringify({ key: "key:other", status: "ok", session: SESSION, payload_hash: "y", at: now })}\n`);
    writeFileSync(join(crashDirs.requests, "req_c3.json"), JSON.stringify({ ...request, id: "req_c3" }));
    const corrupt = await processRequestFile(crashLive, crashDirs, "req_c3.json", SESSION);
    ok("a corrupt journal refuses keyed requests instead of applying them as new, and does not tell the caller to delete it",
       corrupt?.status === "error" && outcomeOf(corrupt).code === "journal_unreadable" && /do not delete/.test(String(outcomeOf(corrupt).next_step)) && crashLive.tracks[0].arrangement.length === 0, corrupt);
  }

  // === The three gaps closed on 2026-09-06 (regressions written before the fix) ===

  // --- A) the journal does not forget: retention, loss, torn tail, interrupted work ---
  {
    const memLive = new FakeLive();
    const memDirs = tempDirs("loom-memory-");
    const firstKey = "build-mem:tempo:first";
    writeFileSync(join(memDirs.requests, "req_m0.json"), JSON.stringify({ id: "req_m0", op: "set_tempo", bpm: 100, idempotency_key: firstKey }));
    await processRequestFile(memLive, memDirs, "req_m0.json", SESSION);
    // enough keyed requests to cross the compaction threshold several times
    const count = JOURNAL_COMPACT_LINES + 50;
    for (let index = 1; index <= count; index++) {
      writeFileSync(join(memDirs.requests, `req_m${index}.json`), JSON.stringify({ id: `req_m${index}`, op: "set_tempo", bpm: 100 + (index % 50), idempotency_key: `build-mem:tempo:${index}` }));
      await processRequestFile(memLive, memDirs, `req_m${index}.json`, SESSION);
    }
    memLive.tempo = 55;
    writeFileSync(join(memDirs.requests, "req_m_replay.json"), JSON.stringify({ id: "req_m_replay", op: "set_tempo", bpm: 100, idempotency_key: firstKey }));
    const oldKey = await processRequestFile(memLive, memDirs, "req_m_replay.json", SESSION);
    ok(`after ${count} later keyed requests the first key is still replayed, not re-applied`, oldKey?.replayed === true && memLive.tempo === 55, { oldKey, tempo: memLive.tempo });
    const memJournal = readJournal(memDirs);
    ok("the journal was compacted to one line per key, not grown two lines per request", memJournal.lines <= memJournal.entries.size + 2 && memJournal.entries.size === count + 1 && memJournal.lines < 2 * count, { lines: memJournal.lines, entries: memJournal.entries.size });

    // retention policy, applied directly
    const retDirs = tempDirs("loom-retention-");
    const entry = (key: string, status: JournalEntry["status"], session: string, at: number): JournalEntry => ({ key, status, session, payload_hash: "h", at });
    const old = now - JOURNAL_RETENTION_SECONDS - 10;
    const seeded: JournalEntry[] = [
      entry("key:mine-old", "ok", SESSION, old),
      entry("key:foreign-old-ok", "ok", "old-live", old),
      entry("key:foreign-old-started", "started", "old-live", old),
      entry("key:foreign-old-indeterminate", "indeterminate", "old-live", old),
      entry("key:foreign-recent-ok", "ok", "old-live", now - 10),
    ];
    for (let index = 0; index < JOURNAL_MAX_FOREIGN + 20; index++) seeded.push(entry(`key:foreign-many-${index}`, "ok", "busy-live", now - 1000 + index / 1000));
    writeFileSync(retDirs.journalFile, seeded.map((e) => JSON.stringify(e)).join("\n") + "\n");
    compactJournal(retDirs, readJournal(retDirs), SESSION, now);
    const kept = readJournal(retDirs).entries;
    ok("retention keeps this session's entries and every unknown-outcome entry, drops old finished foreign entries",
       kept.has("key:mine-old") && !kept.has("key:foreign-old-ok") && kept.has("key:foreign-old-started") && kept.has("key:foreign-old-indeterminate") && kept.has("key:foreign-recent-ok"), [...kept.keys()].filter((k) => !k.includes("many")));
    ok("finished foreign entries are capped at JOURNAL_MAX_FOREIGN, newest first",
       [...kept.keys()].filter((k) => k.startsWith("key:foreign-many")).length === JOURNAL_MAX_FOREIGN - 1 && !kept.has("key:foreign-many-0") && kept.has(`key:foreign-many-${JOURNAL_MAX_FOREIGN + 19}`), kept.size);

    // a lost journal is not a fresh install
    const lostLive = new FakeLive();
    const lostDirs = tempDirs("loom-lost-");
    writeFileSync(join(lostDirs.requests, "req_l1.json"), JSON.stringify({ id: "req_l1", op: "set_tempo", bpm: 90, idempotency_key: "k1" }));
    await processRequestFile(lostLive, lostDirs, "req_l1.json", SESSION);
    unlinkSync(lostDirs.journalFile);
    writeFileSync(join(lostDirs.requests, "req_l2.json"), JSON.stringify({ id: "req_l2", op: "set_tempo", bpm: 91, idempotency_key: "k1" }));
    const lost = await processRequestFile(lostLive, lostDirs, "req_l2.json", SESSION);
    ok("a journal that is gone while its marker remains refuses keyed mutations (journal_missing) instead of re-applying",
       lost?.status === "error" && outcomeOf(lost).code === "journal_missing" && lostLive.tempo === 90 && /nothing needs to be deleted/.test(String(outcomeOf(lost).next_step)), lost);
    writeFileSync(join(lostDirs.requests, "req_l3.json"), JSON.stringify({ id: "req_l3", op: "get_state" }));
    const lostRead = await processRequestFile(lostLive, lostDirs, "req_l3.json", SESSION);
    ok("reads still work on a bridge whose journal is lost", lostRead?.status === "ok" && (lostRead?.result as Record<string, unknown>).tempo === 90);
    ok("the published state says the journal is missing", (JSON.parse(readFileSync(lostDirs.stateFile, "utf8")).journal as Record<string, unknown>).state === "missing");
    const freshDirs = tempDirs("loom-fresh-");
    ok("a root that never had a journal is fresh", readJournal(freshDirs).state === "fresh" && !existsSync(freshDirs.journalMarker));

    // a torn last line (an interrupted append) is skipped and repaired; the next append does not glue onto it
    const tornLive = new FakeLive();
    const tornDirs = tempDirs("loom-torn-");
    writeFileSync(join(tornDirs.requests, "req_t1.json"), JSON.stringify({ id: "req_t1", op: "set_tempo", bpm: 70, idempotency_key: "t1" }));
    await processRequestFile(tornLive, tornDirs, "req_t1.json", SESSION);
    writeFileSync(tornDirs.journalFile, readFileSync(tornDirs.journalFile, "utf8") + '{"key":"key:t2","status":"star');
    ok("a torn tail is reported and the readable entries are kept", readJournal(tornDirs).torn_tail && readJournal(tornDirs).entries.has("key:t1"));
    writeFileSync(join(tornDirs.requests, "req_t3.json"), JSON.stringify({ id: "req_t3", op: "set_tempo", bpm: 71, idempotency_key: "t3" }));
    const afterTorn = await processRequestFile(tornLive, tornDirs, "req_t3.json", SESSION);
    const repaired = readJournal(tornDirs);
    ok("the next keyed request runs, and the journal is whole again with both keys", afterTorn?.status === "ok" && repaired.state === "present" && !repaired.torn_tail && repaired.entries.has("key:t1") && repaired.entries.has("key:t3"), repaired);
    writeFileSync(join(tornDirs.requests, "req_t1b.json"), JSON.stringify({ id: "req_t1b", op: "set_tempo", bpm: 70, idempotency_key: "t1" }));
    ok("the key before the torn line is still replayed", (await processRequestFile(tornLive, tornDirs, "req_t1b.json", SESSION))?.replayed === true);

    // interrupted work survives cleanup: started at compaction, claim file after restart
    const intDirs = tempDirs("loom-interrupted-");
    const lines: JournalEntry[] = [entry("key:interrupted", "started", "earlier-live", old)];
    for (let index = 0; index < JOURNAL_COMPACT_LINES + 10; index++) lines.push(entry(`key:filler-${index}`, "ok", "earlier-live", now - 100 + index / 1000));
    writeFileSync(intDirs.journalFile, lines.map((e) => JSON.stringify(e)).join("\n") + "\n");
    writeFileSync(intDirs.journalMarker, JSON.stringify({ created_at: old }));
    compactJournal(intDirs, readJournal(intDirs), SESSION, now);
    const intLive = new FakeLive();
    writeFileSync(join(intDirs.requests, "req_i1.json"), JSON.stringify({ id: "req_i1", op: "set_tempo", bpm: 66, idempotency_key: "interrupted" }));
    const interrupted = await processRequestFile(intLive, intDirs, "req_i1.json", SESSION);
    ok("an interrupted (started) entry survives compaction and a retry after it is INDETERMINATE, not applied", outcomeOf(interrupted).code === "indeterminate_earlier_attempt" && intLive.tempo === 120, interrupted);
    // a claim file left by a dead host, no journal line for it (unkeyed by key, keyed by id)
    const restartDirs = tempDirs("loom-restart-");
    writeFileSync(join(restartDirs.processing, "req_r1.json"), JSON.stringify({ id: "req_r1", op: "set_tempo", bpm: 67, target_session: "dead-live" }));
    const recovered = recoverProcessing(restartDirs, SESSION);
    const recoveredRecord = JSON.parse(readFileSync(join(restartDirs.errors, "req_r1.json"), "utf8"));
    ok("a claimed request found at startup is answered indeterminate_after_restart with a structured outcome and journaled",
       recovered.length === 1 && recoveredRecord.status === "indeterminate" && recoveredRecord.outcome.code === "indeterminate_after_restart" && recoveredRecord.outcome.applied === null
       && readJournal(restartDirs).entries.get("id:req_r1")?.status === "indeterminate", recoveredRecord);
    const restartLive = new FakeLive();
    writeFileSync(join(restartDirs.requests, "req_r1.json"), JSON.stringify({ id: "req_r1", op: "set_tempo", bpm: 67 }));
    const again = await processRequestFile(restartLive, restartDirs, "req_r1.json", SESSION);
    ok("the same request id resubmitted after the restart is refused as indeterminate, not applied", outcomeOf(again).code === "indeterminate_earlier_attempt" && restartLive.tempo === 120, again);
    // a claim file whose outcome the journal already holds is answered from the journal
    const journaledDirs = tempDirs("loom-journaled-claim-");
    writeFileSync(join(journaledDirs.processing, "req_k1.json"), JSON.stringify({ id: "req_k1", op: "set_tempo", bpm: 68, idempotency_key: "k-done" }));
    writeFileSync(journaledDirs.journalMarker, JSON.stringify({ created_at: now }));
    writeFileSync(journaledDirs.journalFile, `${JSON.stringify({ ...entry("key:k-done", "ok", SESSION, now), request_id: "req_k1", result: { before: 120, after: 68 }, outcome: { kind: "applied", code: "applied", applied: true, verified: true } })}\n`);
    recoverProcessing(journaledDirs, SESSION);
    const fromJournal = JSON.parse(readFileSync(join(journaledDirs.done, "req_k1.json"), "utf8"));
    ok("a claim whose outcome was journaled before the host died is answered with that outcome, not indeterminate", fromJournal.status === "ok" && fromJournal.recovered === "outcome_from_journal" && fromJournal.result.after === 68, fromJournal);
  }

  // --- B) indeterminate is structured, never just an error string ---------------
  {
    const kinds = new Set<string>();
    const errLive = new FakeLive();
    const errDirs = tempDirs("loom-kinds-");
    writeFileSync(join(errDirs.requests, "req_e1.json"), JSON.stringify({ id: "req_e1", op: "set_tempo", bpm: 5 }));
    const refused = await processRequestFile(errLive, errDirs, "req_e1.json", SESSION);
    kinds.add(String(outcomeOf(refused).kind));
    ok("a validation refusal is kind=refused, applied=false, status=error", refused?.status === "error" && outcomeOf(refused).kind === "refused" && outcomeOf(refused).applied === false, refused?.outcome);
    faults.failNextNoteWrite = true;
    writeFileSync(join(errDirs.requests, "req_e2.json"), JSON.stringify({ id: "req_e2", op: "write_arrangement_clip", track: "KICK", start_beat: 0, length_beats: 4, name: "f", notes: [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }] }));
    const failed = await processRequestFile(errLive, errDirs, "req_e2.json", SESSION);
    faults.failNextNoteWrite = false;
    kinds.add(String(outcomeOf(failed).kind));
    ok("a rolled-back failure is kind=failed, applied=false, status=error", failed?.status === "error" && outcomeOf(failed).kind === "failed" && outcomeOf(failed).applied === false && errLive.tracks[0].arrangement.length === 0, failed?.outcome);
    writeFileSync(join(errDirs.requests, "req_e3.json"), JSON.stringify({ id: "req_e3", op: "transport", action: "play" }));
    const unsupported = await processRequestFile(errLive, errDirs, "req_e3.json", SESSION);
    ok("an SDK-unsupported op is a refusal with its own code", outcomeOf(unsupported).kind === "refused" && outcomeOf(unsupported).code === "unsupported_in_extension", unsupported?.outcome);
    ok("the three kinds a caller must tell apart are all distinct values", kinds.size === 2 && !kinds.has("indeterminate"));
    // ledger failure after a verified write is applied-with-warning, never an error that invites a retry
    const warnLive = new FakeLive();
    const warnDirs = tempDirs("loom-warn-");
    mkdirSync(warnDirs.ledgerFile, { recursive: true });  // a directory where the ledger file should be: unwritable, unreadable? no -- missing, then unwritable
    writeFileSync(join(warnDirs.requests, "req_w1.json"), JSON.stringify({ id: "req_w1", op: "write_arrangement_clip", track: "KICK", start_beat: 0, length_beats: 4, name: "w", notes: [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }] }));
    const warned = await processRequestFile(warnLive, warnDirs, "req_w1.json", SESSION);
    ok("an unwritable ledger refuses the write before Live is touched", warned?.status === "error" && outcomeOf(warned).kind === "refused" && warnLive.tracks[0].arrangement.length === 0, warned?.outcome);
  }

  // --- a kit from files: chains + Simplers on an empty Drum Rack -------------
  {
    const kitLive = new FakeLive();
    const kitTrack = kitLive.tracks[0];
    await rejects("a kit needs a Drum Rack on the track", () => apply(kitLive, { op: "build_drum_kit", track: "KICK", pads: [{ note: 36, sample: "/s/kick.wav" }] }), "Drum Rack");
    kitTrack.drumPadNotes = [];
    await rejects("a relative sample path is refused before anything is built", () => apply(kitLive, { op: "build_drum_kit", track: "KICK", pads: [{ note: 36, sample: "kick.wav" }] }), "absolute");
    ok("... and the rack is still empty", kitTrack.drumRacks[0].padNotes.length === 0);
    const kit = await apply(kitLive, { op: "build_drum_kit", track: "KICK", pads: [{ note: 36, sample: "/s/kick.wav" }, { note: 38, sample: "/s/snare.wav" }, { note: 42, sample: "/s/hat.wav" }] });
    ok("three pads are built as chains with Simplers and read back from the device", kit.verified === true && JSON.stringify(kit.pad_notes) === "[36,38,42]" && (kitTrack.drumRacks[0].chains[1].devices[0] as { sample?: string }).sample === "/s/snare.wav", kit);
    const pads = await apply(kitLive, { op: "drum_pads", track: "KICK" });
    ok("drum_pads now reports the built kit as live evidence", pads.verified === true && JSON.stringify(pads.pad_notes) === "[36,38,42]");
    await rejects("an existing pad is never replaced", () => apply(kitLive, { op: "build_drum_kit", track: "KICK", pads: [{ note: 38, sample: "/s/other.wav" }] }), "already have a chain");
    let partialOutcome: Partial<Outcome> = {};
    try {
      await apply(kitLive, { op: "build_drum_kit", track: "KICK", pads: [{ note: 46, sample: "/s/openhat.wav" }, { note: 49, sample: "/s/missing.wav" }] });
      ok("a pad whose sample cannot be loaded fails", false);
    } catch (error) {
      partialOutcome = error instanceof BridgeError ? error.outcome : {};
    }
    ok("a failure after chains exist is reported as a partial kit (indeterminate, applied) naming the built pads", partialOutcome.kind === "indeterminate" && partialOutcome.code === "kit_partial" && /46/.test(String(partialOutcome.side_effects)) && kitTrack.drumRacks[0].padNotes.includes(46), partialOutcome);
  }

  // --- journal import: an earlier extension id's history is carried over -----
  {
    const mismatchLive = new FakeLive();
    mismatchLive.tracks[0].drumPadNotes = [];
    const original = FakeSimpler.prototype.replaceSample;
    FakeSimpler.prototype.replaceSample = async () => "/wrong/sample.wav";
    try {
      await apply(mismatchLive, { op: "build_drum_kit", track: "KICK", pads: [{ note: 36, sample: "/s/kick.wav" }] });
      ok("a different loaded sample is not verified success", false);
    } catch (error) {
      ok("a different loaded sample is indeterminate and not retried", error instanceof BridgeError && error.outcome.kind === "indeterminate" && error.outcome.code === "kit_partial", error);
    } finally {
      FakeSimpler.prototype.replaceSample = original;
    }
  }

  {
    const impDirs = tempDirs("loom-import-");
    const impLive = new FakeLive();
    writeFileSync(join(impDirs.requests, "req_own.json"), JSON.stringify({ id: "req_own", op: "set_tempo", bpm: 100, idempotency_key: "own-key" }));
    await processRequestFile(impLive, impDirs, "req_own.json", SESSION);
    const entries = [
      { key: "key:old-build:clip:KICK:Intro", status: "started", session: "old-live", payload_hash: "h1", at: now - 100, request_id: "req_o1", op: "write_arrangement_clip" },
      { key: "key:old-build:tempo", status: "ok", session: "old-live", payload_hash: "h2", at: now - 90, result: { after: 90 } },
      { key: "key:own-key", status: "ok", session: "old-live", payload_hash: "x", at: now },
      { key: "key:mine", status: "ok", session: SESSION, payload_hash: "y", at: now },
      { nonsense: true },
    ];
    writeFileSync(join(impDirs.requests, "req_imp.json"), JSON.stringify({ id: "req_imp", op: "journal_import", source: "loom.sensei-midi-writer", entries }));
    const imported = await processRequestFile(impLive, impDirs, "req_imp.json", SESSION);
    const r = (imported?.result ?? {}) as Record<string, unknown>;
    ok("journal_import carries foreign entries over, skips existing keys, this session's entries and junk",
       imported?.status === "ok" && r.imported === 2 && r.skipped_existing === 1 && r.skipped_own_session === 1 && r.skipped_invalid === 1, r);
    writeFileSync(join(impDirs.requests, "req_old_retry.json"), JSON.stringify({ id: "req_old_retry", op: "write_arrangement_clip", track: "KICK", start_beat: 0, length_beats: 4, name: "Intro", idempotency_key: "old-build:clip:KICK:Intro", notes: [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }] }));
    const oldRetry = await processRequestFile(impLive, impDirs, "req_old_retry.json", SESSION);
    ok("an interrupted key from the old extension id is still INDETERMINATE here, not re-applied", outcomeOf(oldRetry).code === "indeterminate_earlier_attempt" && impLive.tracks[0].arrangement.length === 0, oldRetry);
    writeFileSync(join(impDirs.requests, "req_old_tempo.json"), JSON.stringify({ id: "req_old_tempo", op: "set_tempo", bpm: 90, idempotency_key: "old-build:tempo" }));
    const oldTempo = await processRequestFile(impLive, impDirs, "req_old_tempo.json", SESSION);
    ok("a finished key from the old id is refused as another session's, not replayed and not applied", outcomeOf(oldTempo).code === "replay_refused_other_session" && impLive.tempo === 100, oldTempo);
    ok("imported lines say where they came from", [...readJournal(impDirs).entries.values()].some((e) => e.imported_from === "loom.sensei-midi-writer"));
  }

  // --- tracks ---------------------------------------------------------------
  const made = await apply(live, { op: "create_midi_track", name: "Kit", instrument_family: "Drum Rack" });
  ok("a missing track is created, named, and a native device inserted", made.created === true && live.tracks[3].name === "Kit" && String(made.instrument).startsWith("inserted: Drum Rack"));
  const preset = await apply(live, { op: "create_midi_track", name: "Keys", instrument_family: "Electric Piano Daze" });
  ok("a preset name is reported as not loadable here, never pretended", String(preset.instrument).startsWith("not_loadable_in_extension"));
  const again = await apply(live, { op: "create_midi_track", name: "Kit", instrument_family: "Drum Rack" });
  ok("a rebuild adopts the track", again.adopted === true && live.tracks.filter((t) => t.name === "Kit").length === 1);
  await rejects("an audio track wearing the name is refused", () => apply(live, { op: "create_midi_track", name: "Vocal" }), "non-MIDI");
  const silent = await apply(live, { op: "create_midi_track", name: "Bass", instrument_family: "Electric Piano Daze" });
  const bassTrack = () => live.tracks.find((t) => t.name === "Bass")!;
  ok("a track created for an unloadable preset is left with an empty chain", silent.created === true && bassTrack().devices.length === 0);
  const completed = await apply(live, { op: "create_midi_track", name: "Bass", instrument_family: "Operator" });
  ok("an adopted track whose chain is empty gets the native device it is asked for -- a later build can complete it",
    completed.adopted === true && String(completed.instrument).startsWith("inserted: Operator") && bassTrack().devices.length === 1);
  const kept = await apply(live, { op: "create_midi_track", name: "Bass", instrument_family: "Drum Rack" });
  ok("an adopted track that already has a device is kept, nothing inserted", String(kept.instrument).startsWith("kept") && bassTrack().devices.length === 1 && bassTrack().devices[0].name === "Operator");

  // --- audio import and pre-fx render (the SDK permission questions) --------
  const imported = await apply(live, { op: "import_audio_clip", track: "Vocal", path: "/packs/x/bars/001.wav", name: "bar 1" });
  ok("an audio file is imported by Live and dropped into the first empty slot",
     imported.placed === "session" && imported.slot === 0 && String(imported.imported_path).startsWith("/Project/Samples/Imported/")
     && live.imports[0] === "/packs/x/bars/001.wav" && live.tracks[2].clipSlots[0].clip !== null, imported);
  const arranged = await apply(live, { op: "import_audio_clip", track: "Vocal", path: "/packs/x/bars/002.wav", start_beat: 32, duration_beats: 8, warped: false });
  ok("an audio file can be placed in the arrangement with duration and warp settings",
     arranged.placed === "arrangement" && arranged.start_beat === 32 && live.tracks[2].audioClips[0].duration === 8 && live.tracks[2].audioClips[0].warped === false, arranged);
  await rejects("importing onto a MIDI track is refused", () => apply(live, { op: "import_audio_clip", track: "BASS", path: "/x.wav" }), "not an audio track");
  await rejects("an occupied slot is refused, never overwritten", () => apply(live, { op: "import_audio_clip", track: "Vocal", path: "/x.wav", slot: 0 }), "occupied");
  const rendered = await apply(live, { op: "render_pre_fx", track: "Vocal", start_beat: 0, end_beat: 16 });
  ok("a pre-fx render returns the path Live wrote", String(rendered.path).endsWith("render_Vocal_0_16.wav") && live.renders.length === 1, rendered);
  await rejects("a render with an empty range is refused", () => apply(live, { op: "render_pre_fx", track: "Vocal", start_beat: 8, end_beat: 8 }), "start_beat < end_beat");

  // --- honest refusals ------------------------------------------------------
  await rejects("transport is refused with the SDK reason", () => apply(live, { op: "transport", action: "play" }), "unsupported_in_extension");
  await rejects("set_key is refused with the SDK reason", () => apply(live, { op: "set_key", root: "F", mode: "Minor" }), "read-only");
  await rejects("an unknown op is refused", () => apply(live, { op: "fly" }), "unknown op");

  // --- the file contract ----------------------------------------------------
  const dirs = tempDirs("loom-bridge-");
  writeFileSync(join(dirs.requests, "req_1.json"), JSON.stringify({ op: "set_tempo", bpm: 100, id: "req_1" }));
  writeFileSync(join(dirs.requests, "req_2.json"), JSON.stringify({ op: "transport", action: "play", id: "req_2" }));
  writeFileSync(join(dirs.requests, "req_3.json"), "{not json");
  const logs: string[] = [];
  await processRequestFile(live, dirs, "req_1.json", SESSION, (line) => logs.push(line));
  await processRequestFile(live, dirs, "req_2.json", SESSION, (line) => logs.push(line));
  await processRequestFile(live, dirs, "req_3.json", SESSION, (line) => logs.push(line));
  const done = JSON.parse(readFileSync(join(dirs.done, "req_1.json"), "utf8"));
  ok("a good request lands in done/ with the result inside the request", done.status === "ok" && done.result.after === 100 && done.id === "req_1" && !existsSync(join(dirs.requests, "req_1.json")));
  ok("every answer names the protocol and the session that answered", done.bridge_protocol === BRIDGE_PROTOCOL && done.session_id === SESSION);
  const failed = JSON.parse(readFileSync(join(dirs.errors, "req_2.json"), "utf8"));
  ok("a refused request lands in errors/ with the reason", failed.status === "error" && String(failed.error).includes("unsupported_in_extension"));
  ok("an unreadable request is moved out of the queue too", existsSync(join(dirs.errors, "req_3.json")) && readdirSync(dirs.requests).length === 0);
  ok("the surface's own log lines are kept", logs[0] === "Loom ok: set_tempo" && logs[1].startsWith("Loom refused: BridgeError"), logs);
  const afterRequest = JSON.parse(readFileSync(dirs.stateFile, "utf8"));
  ok("answering a request republishes the state file at once", afterRequest.tempo === 100 && String(afterRequest.surface_version).startsWith("loom-extension/") && afterRequest.session_id === SESSION && afterRequest.bridge_protocol === BRIDGE_PROTOCOL);
  const published = await publishState(live, dirs, SESSION);
  const onDisk = JSON.parse(readFileSync(dirs.stateFile, "utf8"));
  ok("state is published atomically where live_state expects it", onDisk.captured_at === published.captured_at && onDisk.tempo === 100 && !existsSync(`${dirs.stateFile}.tmp`));
  ok("the state carries a journal summary", typeof onDisk.journal === "object" && onDisk.journal.state === "present" && onDisk.journal.entries >= 1, onDisk.journal);

  // --- migration of the v2 journal --------------------------------------------
  {
    const migDirs = tempDirs("loom-migrate-");
    writeFileSync(join(migDirs.state, "processed_requests.json"), JSON.stringify({ entries: [{ key: "key:old-1", status: "ok", request_id: "req_old", session: "old-live", payload_hash: "p", started_at: now - 5, record: { id: "req_old", op: "set_tempo", status: "ok", result: { after: 99 } } }] }));
    const { migrateLegacyJournal } = await import("../src/bridge.js");
    const migrated = migrateLegacyJournal(migDirs);
    const afterMigration = readJournal(migDirs);
    ok("a v2 journal is carried over once and its keys are kept", migrated === 1 && afterMigration.entries.get("key:old-1")?.status === "ok" && !existsSync(join(migDirs.state, "processed_requests.json")) && migrateLegacyJournal(migDirs) === 0, afterMigration);
    rmSync(migDirs.root, { recursive: true, force: true });
  }
  void renameSync;

  console.log(`${checks.length} checks passed:`);
  for (const label of checks) console.log(`  ok  ${label}`);
  if (failures.length > 0) {
    console.log(`FAILED (${failures.length}):`);
    for (const line of failures) console.log(`  - ${line}`);
    process.exit(1);
  }
  console.log("EXTENSION BRIDGE WORKS");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
