// Runs the extension against the fake host, no Ableton Live required.
// Usage: npx tsx test/smoke.ts

import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import { buildFakeActivationContext, type FakeTrackSeed } from "./fakeHost.ts";
import { activate } from "../src/extension.ts";

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

let failures = 0;
function check(label: string, condition: boolean) {
  if (condition) {
    console.log(`PASS  ${label}`);
  } else {
    failures += 1;
    console.log(`FAIL  ${label}`);
  }
}

async function readResponses(responsesPath: string, expectedCount: number, timeoutMs: number) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const content = await fs.readFile(responsesPath, "utf-8");
      const lines = content.split("\n").filter((line) => line.trim().length > 0);
      if (lines.length >= expectedCount) {
        return lines.map((line) => JSON.parse(line));
      }
    } catch {
      // file not created yet
    }
    await sleep(50);
  }
  throw new Error(`Timed out waiting for ${expectedCount} responses in ${responsesPath}`);
}

async function main() {
  const tempDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "aimixmaster-live-mix-"));
  const seeds: FakeTrackSeed[] = JSON.parse(
    await fs.readFile(path.join(import.meta.dirname, "fixtures", "ded2_audio_tracks.json"), "utf-8"),
  );

  const { activation, tracks } = buildFakeActivationContext(seeds, tempDirectory);
  const firstInstance = activate(activation);

  const commandsPath = path.join(tempDirectory, "live_mix_commands.jsonl");
  const responsesPath = path.join(tempDirectory, "live_mix_responses.jsonl");
  const statePath = path.join(tempDirectory, "live_mix_state.json");
  const offsetPath = path.join(tempDirectory, "live_mix_commands.offset");

  const kickIndex = seeds.findIndex((seed) => seed.name === "KİCK");
  check("fixture contains a KİCK track", kickIndex >= 0);

  const commands = [
    { id: "1", action: "list_tracks" },
    { id: "2", action: "set_volume", track: "KİCK", value: 0.5, intent: "kick çok yüksek geliyordu" },
    { id: "3", action: "set_mute", track: "KİCK", value: true },
    { id: "4", action: "set_volume", track: "KİCK", value: 999 }, // out of range, should clamp
    { id: "5", action: "set_volume", track: "no such track", value: 0.5 }, // should error
  ];
  await fs.writeFile(commandsPath, commands.map((c) => JSON.stringify(c)).join("\n") + "\n", "utf-8");

  const responses = await readResponses(responsesPath, commands.length, 3000);
  const byId = new Map(responses.map((r) => [r.id, r]));

  check("list_tracks ok", byId.get("1")?.ok === true);
  check("list_tracks returns 28 tracks", byId.get("1")?.result?.length === seeds.length);
  check("set_volume ok", byId.get("2")?.ok === true);
  check("set_volume applied 0.5", byId.get("2")?.result?.volume_raw === 0.5);
  check("set_mute ok", byId.get("3")?.ok === true);
  check("set_mute applied true", byId.get("3")?.result?.mute === true);
  check("out-of-range volume clamps to max (1.0)", byId.get("4")?.result?.volume_raw === 1.0);
  check("unknown track name reports an error, not a crash", byId.get("5")?.ok === false);

  const kickTrack = tracks.find((track) => track.name === "KİCK");
  check("fake model volume actually mutated", kickTrack !== undefined);

  await sleep(1200); // let the 1s state-snapshot interval fire at least once
  const state = JSON.parse(await fs.readFile(statePath, "utf-8"));
  check("live_mix_state.json was written", Array.isArray(state.tracks) && state.tracks.length === seeds.length);
  const kickState = state.tracks.find((t: { name: string }) => t.name === "KİCK");
  check("live_mix_state.json reflects the mute we set", kickState?.mute === true);

  const sessionLogPath = path.join(tempDirectory, "live_mix_session_log.jsonl");
  const sessionLogLines = (await fs.readFile(sessionLogPath, "utf-8")).split("\n").filter((l) => l.trim().length > 0);
  const sessionLog = sessionLogLines.map((line) => JSON.parse(line));
  check("session log skips list_tracks (4 entries, not 5)", sessionLog.length === 4);
  check("session log keeps the intent text", sessionLog[0]?.intent === "kick çok yüksek geliyordu");
  check("session log entry without intent is null, not missing", sessionLog[1]?.intent === null);

  // Simulate a restart (reinstall, Live relaunch): a brand new fake host, same
  // storage directory. Old commands must NOT be replayed; only a genuinely
  // new command should produce a new response/log entry.
  const responsesBeforeRestart = (await fs.readFile(responsesPath, "utf-8")).split("\n").filter((l) => l.trim().length > 0).length;
  const sessionLogBeforeRestart = (await fs.readFile(sessionLogPath, "utf-8")).split("\n").filter((l) => l.trim().length > 0).length;

  firstInstance?.stop();
  const restarted = buildFakeActivationContext(seeds, tempDirectory);
  const secondInstance = activate(restarted.activation);
  await sleep(500); // give the restarted extension time to load its offset and poll once

  const responsesRightAfterRestart = (await fs.readFile(responsesPath, "utf-8")).split("\n").filter((l) => l.trim().length > 0).length;
  check("restart does not replay old commands", responsesRightAfterRestart === responsesBeforeRestart);

  await fs.appendFile(commandsPath, JSON.stringify({ id: "6", action: "set_mute", track: "KİCK", value: false }) + "\n", "utf-8");
  const responsesAfterNewCommand = await readResponses(responsesPath, responsesBeforeRestart + 1, 3000);
  check("a genuinely new command after restart is still processed", responsesAfterNewCommand.find((r) => r.id === "6")?.ok === true);

  const sessionLogAfterNewCommand = (await fs.readFile(sessionLogPath, "utf-8")).split("\n").filter((l) => l.trim().length > 0);
  check("restart + one new command adds exactly one session-log entry", sessionLogAfterNewCommand.length === sessionLogBeforeRestart + 1);

  // Simulate a poll landing mid-write: the line is appended without its
  // trailing newline yet (as if the writer hasn't flushed it). The poller
  // must not consume it -- consuming a truncated line would strand the rest
  // of that command forever, which is exactly the bug this guards against.
  const responsesBeforeTornWrite = (await fs.readFile(responsesPath, "utf-8")).split("\n").filter((l) => l.trim().length > 0).length;
  const tornCommand = JSON.stringify({ id: "7", action: "set_solo", track: "KİCK", value: true });
  await fs.appendFile(commandsPath, tornCommand.slice(0, -5), "utf-8"); // no trailing "e":true} + newline yet
  await sleep(500);
  const responsesDuringTornWrite = (await fs.readFile(responsesPath, "utf-8")).split("\n").filter((l) => l.trim().length > 0).length;
  check("an incomplete trailing line is not consumed", responsesDuringTornWrite === responsesBeforeTornWrite);

  await fs.appendFile(commandsPath, tornCommand.slice(-5) + "\n", "utf-8"); // completes the line
  const responsesAfterTornWriteCompletes = await readResponses(responsesPath, responsesBeforeTornWrite + 1, 3000);
  check("the line is processed once it's actually complete", responsesAfterTornWriteCompletes.find((r) => r.id === "7")?.ok === true);

  // Turkish text (ı/ğ/ö/ü/ş/ç) is multi-byte in UTF-8 but single-length as a
  // JS string. Persisting a string-length offset instead of a byte offset
  // desyncs it from the real file size -- every future poll then reads a
  // "complete" slice that's actually a few bytes short of a real line
  // boundary, permanently stranding whatever comes after. This is guaranteed
  // to happen in real usage since intents are typed in Turkish.
  const responsesBeforeTurkish = await fs.readFile(responsesPath, "utf-8");
  const responsesBeforeTurkishCount = responsesBeforeTurkish.split("\n").filter((l) => l.trim().length > 0).length;
  const turkishCommand = { id: "8", action: "set_pan", track: "KİCK", value: 0.1, intent: "kick biraz sağa kaydı, düzeltiyorum ığöüşç" };
  await fs.appendFile(commandsPath, JSON.stringify(turkishCommand) + "\n", "utf-8");
  await readResponses(responsesPath, responsesBeforeTurkishCount + 1, 3000);

  const commandsFileBytes = (await fs.stat(commandsPath)).size;
  const persistedOffset = Number((await fs.readFile(offsetPath, "utf-8")).trim());
  check("persisted offset matches the real file byte size, not string length", persistedOffset === commandsFileBytes);

  // Prove it isn't just cosmetic: a command sent right after the Turkish one
  // must still be picked up. If the offset had desynced, this would hang.
  const responsesBeforeFollowUp = (await fs.readFile(responsesPath, "utf-8")).split("\n").filter((l) => l.trim().length > 0).length;
  await fs.appendFile(commandsPath, JSON.stringify({ id: "9", action: "set_mute", track: "KİCK", value: false }) + "\n", "utf-8");
  const responsesAfterFollowUp = await readResponses(responsesPath, responsesBeforeFollowUp + 1, 3000);
  check("a command right after Turkish text is still processed, not stranded", responsesAfterFollowUp.find((r) => r.id === "9")?.ok === true);

  secondInstance?.stop();
  await fs.rm(tempDirectory, { recursive: true, force: true });

  // Real Live IPC has real latency, unlike the instant fake host used above.
  // A slow enough round-trip makes writeStateSnapshot take longer than its
  // own poll interval -- if the loop were plain setInterval, a second call
  // would start before the first finishes and both would race on the same
  // .tmp file (one call's rename stealing the file the other just wrote,
  // failing with ENOENT). Reproduce that here with artificial IPC latency.
  const latencyTempDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "aimixmaster-live-mix-latency-"));
  const capturedErrors: unknown[] = [];
  const originalConsoleError = console.error;
  console.error = (...args: unknown[]) => {
    capturedErrors.push(args);
  };
  const latencyHost = buildFakeActivationContext(seeds, latencyTempDirectory, { ipcLatencyMs: 300 });
  const latencyInstance = activate(latencyHost.activation);
  await sleep(3500); // several snapshot cycles at ~1s each, each call taking ~1.2s+ under this latency
  latencyInstance?.stop();
  await sleep(500); // stop() cancels future scheduling, not an in-flight call -- let one finish before we delete its directory
  console.error = originalConsoleError;

  const stateSnapshotErrors = capturedErrors.filter((args) =>
    Array.isArray(args) && typeof args[0] === "string" && args[0].includes("failed to write state snapshot"),
  );
  check("no overlapping-write races under realistic IPC latency", stateSnapshotErrors.length === 0);

  const latencyState = JSON.parse(await fs.readFile(path.join(latencyTempDirectory, "live_mix_state.json"), "utf-8"));
  check("state snapshot still gets written under latency", Array.isArray(latencyState.tracks) && latencyState.tracks.length === seeds.length);

  await fs.rm(latencyTempDirectory, { recursive: true, force: true });

  // insert_device: on both an AudioTrack and a group/bus track (the SDK has
  // no dedicated GroupTrack class -- a group resolves as the base Track, and
  // this command set must reach it too, e.g. "Kick buss").
  const deviceTempDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "aimixmaster-live-mix-devices-"));
  const deviceCommandsPath = path.join(deviceTempDirectory, "live_mix_commands.jsonl");
  const deviceResponsesPath = path.join(deviceTempDirectory, "live_mix_responses.jsonl");
  const deviceSessionLogPath = path.join(deviceTempDirectory, "live_mix_session_log.jsonl");
  const deviceSeeds: FakeTrackSeed[] = [
    { name: "Kick", kind: "Track", devices: [] },
    { name: "Kick Sub", kind: "AudioTrack", devices: ["Eq8"] },
  ];
  const deviceHost = buildFakeActivationContext(deviceSeeds, deviceTempDirectory);
  const deviceInstance = activate(deviceHost.activation);

  const deviceCommands = [
    { id: "d1", action: "list_tracks" },
    { id: "d2", action: "insert_device", track: "Kick", device_name: "Eq8", intent: "kick buss'a EQ koy" },
    { id: "d3", action: "insert_device", track: "Kick", device_name: "Saturator" },
    { id: "d4", action: "insert_device", track: "Kick Sub", device_name: "GlueCompressor", index: 0 },
  ];
  await fs.writeFile(deviceCommandsPath, deviceCommands.map((c) => JSON.stringify(c)).join("\n") + "\n", "utf-8");
  const deviceResponses = await readResponses(deviceResponsesPath, deviceCommands.length, 3000);
  const deviceById = new Map(deviceResponses.map((r) => [r.id, r]));

  check("list_tracks includes the group/bus track, not just audio tracks", deviceById.get("d1")?.result?.length === 2);
  check("insert_device on a group track succeeds", deviceById.get("d2")?.ok === true);
  check("device appears in the track's device list after insert", deviceById.get("d2")?.result?.devices?.includes("Eq8"));
  check("a second insert appends after the first", JSON.stringify(deviceById.get("d3")?.result?.devices) === JSON.stringify(["Eq8", "Saturator"]));
  check("an explicit index inserts before the existing device", JSON.stringify(deviceById.get("d4")?.result?.devices) === JSON.stringify(["GlueCompressor", "Eq8"]));

  const deviceSessionLog = (await fs.readFile(deviceSessionLogPath, "utf-8")).split("\n").filter((l) => l.trim().length > 0).map((l) => JSON.parse(l));
  check("insert_device entries keep device_name and intent in the session log", deviceSessionLog.some((entry) => entry.device_name === "Eq8" && entry.intent === "kick buss'a EQ koy"));

  // list_device_parameters / set_device_parameter -- reading/writing a
  // device's actual parameters (e.g. EQ band freq/gain), not just the
  // track's mixer, and entirely through the SDK (no XML involved).
  const parameterCommands = [
    { id: "d5", action: "list_device_parameters", track: "Kick Sub", device_index: 0 }, // GlueCompressor after the index:0 insert above
    { id: "d6", action: "set_device_parameter", track: "Kick Sub", device_index: 0, parameter: "Param A", value: 0.75, intent: "test parametresi ayarlanıyor" },
    { id: "d7", action: "set_device_parameter", track: "Kick Sub", device_index: 0, parameter: 1, value: 999 }, // out of range, should clamp to max (12)
    { id: "d8", action: "set_device_parameter", track: "Kick Sub", device_index: 0, parameter: "no such parameter", value: 0 },
    { id: "d9", action: "list_device_parameters", track: "Kick Sub", device_index: 99 }, // no device at that index
  ];
  await fs.appendFile(deviceCommandsPath, parameterCommands.map((c) => JSON.stringify(c)).join("\n") + "\n", "utf-8");
  const parameterResponses = await readResponses(deviceResponsesPath, deviceCommands.length + parameterCommands.length, 3000);
  const parameterById = new Map(parameterResponses.map((r) => [r.id, r]));

  check("list_device_parameters returns the device's parameters", parameterById.get("d5")?.result?.parameters?.length === 2);
  check("list_device_parameters names them", parameterById.get("d5")?.result?.parameters?.[0]?.name === "Param A");
  check("set_device_parameter by name applies the value", parameterById.get("d6")?.result?.value === 0.75);
  check("set_device_parameter by index clamps out-of-range to max", parameterById.get("d7")?.result?.value === 12);
  check("unknown parameter name reports an error, not a crash", parameterById.get("d8")?.ok === false);
  check("unknown device index reports an error, not a crash", parameterById.get("d9")?.ok === false);

  deviceInstance?.stop();
  await fs.rm(deviceTempDirectory, { recursive: true, force: true });

  console.log(failures === 0 ? "\nAll checks passed." : `\n${failures} check(s) failed.`);
  process.exit(failures === 0 ? 0 : 1);
}

void main();
