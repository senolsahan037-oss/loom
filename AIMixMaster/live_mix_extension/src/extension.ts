import {
  initialize,
  MidiTrack,
  type ActivationContext,
  type Device,
  type DeviceParameter,
  type ExtensionContext,
  type Track,
} from "@ableton-extensions/sdk";

import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

const COMMAND_POLL_MS = 250;
const STATE_SNAPSHOT_MS = 1000;

interface Command {
  id: string;
  action:
    | "list_tracks"
    | "set_volume"
    | "set_pan"
    | "set_mute"
    | "set_solo"
    | "insert_device"
    | "list_device_parameters"
    | "set_device_parameter";
  track?: string | number;
  value?: number | boolean;
  /** insert_device only: the built-in Live device name, e.g. "EQ Eight". */
  device_name?: string;
  /** insert_device only: 0-based position in the device chain; appended at the end when omitted. */
  index?: number;
  /** list_device_parameters / set_device_parameter: which device on the track, by position in its device list. */
  device_index?: number;
  /** set_device_parameter only: parameter name (as returned by list_device_parameters) or its index in that list. */
  parameter?: string | number;
  /** The natural-language request that led to this command, e.g. "kick çok yüksek geliyordu". Logged verbatim for later review; not used by the extension itself. */
  intent?: string;
}

interface Response {
  id: string;
  ok: boolean;
  result?: unknown;
  error?: string;
}

function findTrack(tracks: Track<"1.0.0">[], selector: string | number | undefined): Track<"1.0.0"> {
  if (selector === undefined) {
    throw new Error("track is required");
  }
  if (typeof selector === "number") {
    const track = tracks[selector];
    if (!track) throw new Error(`No track at index ${selector}`);
    return track;
  }
  const matches = tracks.filter((track) => track.name === selector);
  if (matches.length === 0) throw new Error(`No track named ${JSON.stringify(selector)}`);
  if (matches.length > 1) throw new Error(`${matches.length} tracks named ${JSON.stringify(selector)}; use a track index instead`);
  return matches[0]!;
}

function findDevice(track: Track<"1.0.0">, deviceIndex: number | undefined): Device<"1.0.0"> {
  if (deviceIndex === undefined) throw new Error("device_index is required");
  const device = track.devices[deviceIndex];
  if (!device) throw new Error(`No device at index ${deviceIndex} on track ${JSON.stringify(track.name)}`);
  return device;
}

function findParameter(device: Device<"1.0.0">, selector: string | number | undefined): DeviceParameter<"1.0.0"> {
  if (selector === undefined) throw new Error("parameter is required");
  if (typeof selector === "number") {
    const parameter = device.parameters[selector];
    if (!parameter) throw new Error(`No parameter at index ${selector} on device ${JSON.stringify(device.name)}`);
    return parameter;
  }
  const matches = device.parameters.filter((parameter) => parameter.name === selector);
  if (matches.length === 0) throw new Error(`No parameter named ${JSON.stringify(selector)} on device ${JSON.stringify(device.name)}`);
  if (matches.length > 1) throw new Error(`${matches.length} parameters named ${JSON.stringify(selector)}; use a parameter index instead`);
  return matches[0]!;
}

async function deviceParameterSnapshot(parameter: DeviceParameter<"1.0.0">, index: number) {
  return {
    index,
    name: parameter.name,
    min: parameter.min,
    max: parameter.max,
    is_quantized: parameter.isQuantized,
    default_value: parameter.defaultValue,
    value: await parameter.getValue(),
  };
}

// Audio tracks and group (bus) tracks -- the SDK has no dedicated GroupTrack
// class, a group resolves as the base Track. MIDI tracks are excluded: they
// aren't a mixing target for this command set.
function mixableTracks(context: ExtensionContext<"1.0.0">): Track<"1.0.0">[] {
  return context.application.song!.tracks.filter((track) => !(track instanceof MidiTrack));
}

async function trackSnapshot(track: Track<"1.0.0">, index: number) {
  return {
    index,
    name: track.name,
    mute: track.mute,
    solo: track.solo,
    volume_raw: await track.mixer.volume.getValue(),
    volume_min: track.mixer.volume.min,
    volume_max: track.mixer.volume.max,
    pan_raw: await track.mixer.panning.getValue(),
    devices: track.devices.map((device) => device.name),
  };
}

async function handleCommand(context: ExtensionContext<"1.0.0">, command: Command): Promise<Response> {
  try {
    const tracks = mixableTracks(context);
    switch (command.action) {
      case "list_tracks": {
        const snapshots = await Promise.all(tracks.map((track, index) => trackSnapshot(track, index)));
        return { id: command.id, ok: true, result: snapshots };
      }
      case "set_volume": {
        const track = findTrack(tracks, command.track);
        const value = command.value;
        if (typeof value !== "number") throw new Error("value must be a number");
        const clamped = Math.min(track.mixer.volume.max, Math.max(track.mixer.volume.min, value));
        await track.mixer.volume.setValue(clamped);
        return { id: command.id, ok: true, result: await trackSnapshot(track, tracks.indexOf(track)) };
      }
      case "set_pan": {
        const track = findTrack(tracks, command.track);
        const value = command.value;
        if (typeof value !== "number") throw new Error("value must be a number");
        const clamped = Math.min(track.mixer.panning.max, Math.max(track.mixer.panning.min, value));
        await track.mixer.panning.setValue(clamped);
        return { id: command.id, ok: true, result: await trackSnapshot(track, tracks.indexOf(track)) };
      }
      case "set_mute": {
        const track = findTrack(tracks, command.track);
        if (typeof command.value !== "boolean") throw new Error("value must be a boolean");
        track.mute = command.value;
        return { id: command.id, ok: true, result: await trackSnapshot(track, tracks.indexOf(track)) };
      }
      case "set_solo": {
        const track = findTrack(tracks, command.track);
        if (typeof command.value !== "boolean") throw new Error("value must be a boolean");
        track.solo = command.value;
        return { id: command.id, ok: true, result: await trackSnapshot(track, tracks.indexOf(track)) };
      }
      case "insert_device": {
        const track = findTrack(tracks, command.track);
        const deviceName = command.device_name;
        if (typeof deviceName !== "string" || deviceName.trim().length === 0) {
          throw new Error("device_name is required");
        }
        const index = command.index ?? track.devices.length;
        await track.insertDevice(deviceName, index);
        return { id: command.id, ok: true, result: await trackSnapshot(track, tracks.indexOf(track)) };
      }
      case "list_device_parameters": {
        const track = findTrack(tracks, command.track);
        const device = findDevice(track, command.device_index);
        const parameters = await Promise.all(device.parameters.map((parameter, index) => deviceParameterSnapshot(parameter, index)));
        return { id: command.id, ok: true, result: { device: device.name, parameters } };
      }
      case "set_device_parameter": {
        const track = findTrack(tracks, command.track);
        const device = findDevice(track, command.device_index);
        const parameter = findParameter(device, command.parameter);
        const value = command.value;
        if (typeof value !== "number") throw new Error("value must be a number");
        const clamped = Math.min(parameter.max, Math.max(parameter.min, value));
        await parameter.setValue(clamped);
        return {
          id: command.id,
          ok: true,
          result: await deviceParameterSnapshot(parameter, device.parameters.indexOf(parameter)),
        };
      }
      default:
        throw new Error(`Unknown action ${JSON.stringify((command as Command).action)}`);
    }
  } catch (error) {
    return { id: command.id, ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

export function activate(activation: ActivationContext) {
  const context = initialize(activation, "1.0.0");
  const baseDir = context.environment.storageDirectory ?? context.environment.tempDirectory ?? os.tmpdir();
  const commandsPath = path.join(baseDir, "live_mix_commands.jsonl");
  const responsesPath = path.join(baseDir, "live_mix_responses.jsonl");
  const statePath = path.join(baseDir, "live_mix_state.json");
  const sessionLogPath = path.join(baseDir, "live_mix_session_log.jsonl");
  const offsetPath = path.join(baseDir, "live_mix_commands.offset");

  // How far into commandsPath we've already read, persisted to offsetPath so a
  // restart (reinstall, Live relaunch) resumes instead of replaying every
  // command ever sent since the file began.
  let commandOffset = 0;
  let offsetReady = false;

  async function loadInitialOffset(): Promise<number> {
    try {
      const saved = Number((await fs.readFile(offsetPath, "utf-8")).trim());
      if (Number.isFinite(saved) && saved >= 0) return saved;
    } catch {
      // no saved offset yet -- fall through
    }
    try {
      // First run ever: don't replay whatever history already sits in the
      // commands file, only react to commands written from now on.
      return (await fs.stat(commandsPath)).size;
    } catch {
      return 0;
    }
  }

  async function pollCommands() {
    if (!offsetReady) return;
    let content: Buffer;
    try {
      // Read as raw bytes, not a decoded string. commandOffset is a byte
      // offset (it has to match fs.stat().size from loadInitialOffset) --
      // decoding to UTF-8 first and using the resulting string's .length
      // undercounts every multi-byte character (Turkish ı/ğ/ö/ü/ş/ç included),
      // desyncing the offset from the real file position.
      content = await fs.readFile(commandsPath);
    } catch {
      return;
    }
    if (content.length <= commandOffset) return;
    const unseen = content.subarray(commandOffset);
    // Only consume up to the last complete line. A poll can land mid-write
    // (the appender wrote the line but not the trailing newline yet, or the
    // OS hasn't flushed it); advancing past an incomplete tail would parse
    // garbage, skip it, and permanently strand the rest of that command.
    const lastNewline = unseen.lastIndexOf(0x0a); // "\n"
    if (lastNewline === -1) return; // nothing complete yet -- try again next poll
    const complete = unseen.subarray(0, lastNewline + 1);
    commandOffset += complete.length;
    await fs.writeFile(offsetPath, String(commandOffset), "utf-8");
    const lines = complete.toString("utf-8").split("\n").filter((line) => line.trim().length > 0);
    for (const line of lines) {
      let command: Command;
      try {
        command = JSON.parse(line);
      } catch {
        continue;
      }
      const response = await handleCommand(context, command);
      await fs.appendFile(responsesPath, JSON.stringify(response) + "\n", "utf-8");
      if (command.action !== "list_tracks" && command.action !== "list_device_parameters") {
        const entry = {
          timestamp: new Date().toISOString(),
          intent: command.intent ?? null,
          action: command.action,
          track: command.track ?? null,
          value: command.value ?? null,
          device_name: command.device_name ?? null,
          device_index: command.device_index ?? null,
          parameter: command.parameter ?? null,
          ok: response.ok,
          result: response.result ?? null,
          error: response.error ?? null,
        };
        await fs.appendFile(sessionLogPath, JSON.stringify(entry) + "\n", "utf-8");
      }
    }
  }

  async function writeStateSnapshot() {
    try {
      const tracks = mixableTracks(context);
      const snapshots = await Promise.all(tracks.map((track, index) => trackSnapshot(track, index)));
      const payload = { timestamp: Date.now(), tracks: snapshots };
      const tmp = statePath + ".tmp";
      await fs.writeFile(tmp, JSON.stringify(payload), "utf-8");
      await fs.rename(tmp, statePath);
    } catch (error) {
      console.error("AIMixMaster: failed to write state snapshot", error);
    }
  }

  loadInitialOffset().then((offset) => {
    commandOffset = offset;
    offsetReady = true;
  });

  // Real Live IPC round-trips take real time (unlike the fake host, which
  // resolves instantly). setInterval would fire the next tick before a slow
  // call finishes, so two calls could overlap and race on the same .tmp file
  // (one call's rename stealing the file the other just wrote). A
  // self-rescheduling loop -- next call only starts once this one is fully
  // done -- makes that impossible.
  let stopped = false;
  let pollHandle: ReturnType<typeof setTimeout> | undefined;
  let snapshotHandle: ReturnType<typeof setTimeout> | undefined;

  async function pollLoop() {
    if (stopped) return;
    await pollCommands();
    if (!stopped) pollHandle = setTimeout(() => void pollLoop(), COMMAND_POLL_MS);
  }

  async function snapshotLoop() {
    if (stopped) return;
    await writeStateSnapshot();
    if (!stopped) snapshotHandle = setTimeout(() => void snapshotLoop(), STATE_SNAPSHOT_MS);
  }

  void pollLoop();
  void snapshotLoop();

  console.log(`AIMixMaster Live Mix extension active. Commands: ${commandsPath}`);

  // Not part of the SDK's extension contract; only used by tests to cleanly
  // stop one fake "process" before starting another to simulate a restart.
  return {
    stop: () => {
      stopped = true;
      clearTimeout(pollHandle);
      clearTimeout(snapshotHandle);
    },
  };
}
