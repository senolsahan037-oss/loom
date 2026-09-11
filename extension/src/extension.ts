// The Loom extension: Live's side of the one Loom connection.
//
// Two things happen here and nothing else:
//   1. The bridge (src/bridge.ts) is started inside the extension's storage
//      directory; every Live read and write the Loom MCP makes arrives there.
//   2. One context-menu command, "Loom: Generate", lets the person at the
//      keyboard generate into the clip slot they right-clicked. Its write goes
//      through the same applyOperation and the same ownership ledger as the
//      MCP's writes: a clip Loom did not write is never overwritten.
// Everything Live-specific meets bridge.ts's structural interfaces in the
// wrappers below; those wrappers are the only place the SDK objects are touched.
import {
  AudioClip,
  AudioTrack,
  ClipSlot,
  DataModelObject,
  DrumChain,
  DrumRack,
  MidiClip,
  MidiTrack,
  RackDevice,
  Simpler,
  initialize,
  type ActivationContext,
  type CuePoint,
  type Device,
  type DeviceParameter,
  type ExtensionContext,
  type Handle,
  type NoteDescription,
  type Track,
} from "@ableton-extensions/sdk";
import {
  ClipLedger,
  applyOperation,
  bridgeDirs,
  newSessionId,
  startBridge,
  type ArrangementClipLike,
  type AudioClipLike,
  type BridgeClipLike,
  type CueLike,
  type DeviceLike,
  type DrumChainLike,
  type DrumRackLike,
  type LiveLike,
  type ParamLike,
  type SlotLike,
  type TrackLike,
} from "./bridge.js";
import { execFile } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { promisify } from "node:util";
import { gunzipSync } from "node:zlib";
import embeddedRuntime from "sensei:runtime";

type SenseiNote = { pitch: number; time: number; duration: number; velocity: number };
type SenseiPayload = { schema_version?: string; notes: SenseiNote[]; clip_length?: number; provenance?: Record<string, unknown> };
type GenerateReport = {
  schema_version: string;
  status: string;
  write_authorized: boolean;
  report?: { code?: string; detail?: string };
  payload: SenseiPayload | null;
};
type DrumLiveTarget = {
  role: "drum";
  device_name: string;
  device_classes: string[];
  verified_pad_map: boolean;
  verified_pad_notes: number[];
  bars: number;
  seed: number;
  variation_amount: number;
};
type InstrumentLiveTarget = {
  device_names: string[];
  bars: number;
  seed: number;
  variation_amount: number;
};

const execFileAsync = promisify(execFile);
// Bumped whenever the embedded Python runtime changes: the extension unpacks
// it once per version into storage and reuses it, so an unbumped change would
// leave Live running the old CLI while the source says otherwise.
const RUNTIME_VERSION = "phase6-v13";

function messageDialog(title: string, detail: string) {
  const html = `<!doctype html><html><body style="background:#292929;color:#ddd;font:13px system-ui;padding:16px">
  <h3 style="margin-top:0">${escapeHtml(title)}</h3><p style="white-space:pre-wrap;color:#bbb">${escapeHtml(detail)}</p>
  <div style="text-align:right;margin-top:18px"><button onclick="closeDialog()">OK</button></div>
  <script>function closeDialog(){const message={method:'close_and_send',params:['ok']};if(window.webkit?.messageHandlers?.live)window.webkit.messageHandlers.live.postMessage(message);else if(window.chrome?.webview)window.chrome.webview.postMessage(message)}</script>
  </body></html>`;
  return `data:text/html,${encodeURIComponent(html)}`;
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[character] ?? character);
}

function validateGenerateReport(value: unknown): GenerateReport {
  if (!value || typeof value !== "object") throw new Error("Generate returned an invalid report.");
  const report = value as GenerateReport;
  if (report.schema_version !== "sensei.generate-report.v1") throw new Error("Generate returned an unsupported report schema.");
  if (typeof report.status !== "string" || typeof report.write_authorized !== "boolean") throw new Error("Generate report is incomplete.");
  if (report.status === "ready_to_write" && report.write_authorized === true && report.payload) return report;
  const code = report.report?.code ?? "generation_blocked";
  const detail = report.report?.detail ?? "Sensei did not authorize a MIDI write.";
  throw new Error(`${code}: ${detail}`);
}

async function generatePayload(storageDirectory: string): Promise<SenseiPayload> {
  const runtimeRoot = join(storageDirectory, "runtime", RUNTIME_VERSION);
  const markerPath = join(runtimeRoot, ".ready");
  if (!existsSync(markerPath)) {
    for (const [relativePath, compressed] of Object.entries(embeddedRuntime)) {
      const destination = join(runtimeRoot, relativePath);
      mkdirSync(join(destination, ".."), { recursive: true });
      writeFileSync(destination, gunzipSync(Buffer.from(compressed, "base64")));
    }
    writeFileSync(markerPath, RUNTIME_VERSION, "utf8");
  }
  const targetPath = join(storageDirectory, "current_live_target.json");
  const cliPath = join(runtimeRoot, "tools", "generate_cli.py");
  const dataRoot = join(runtimeRoot, "data");
  const { stdout } = await execFileAsync("/usr/bin/python3", [cliPath, "--live-target", targetPath, "--data-root", dataRoot], {
    cwd: runtimeRoot,
    encoding: "utf8",
    timeout: 30_000,
    maxBuffer: 4 * 1024 * 1024,
  });
  const report = validateGenerateReport(JSON.parse(stdout));
  return report.payload as SenseiPayload;
}

// Every Drum Rack a track holds, wherever it sits. Some factory "kit"
// presets don't resolve to the SDK's DrumRack class -- Live's data model
// reports a plain RackDevice whose chains are genuine DrumChains -- and
// most Core Library kits ("BNYX Boot Kit", measured 2026-09-07) wrap
// their Drum Rack inside an Instrument Rack chain: the pads are one rack
// deeper than track.devices. Both are read; the pad evidence comes from
// the DrumChain instances, not from the wrapper class or the nesting.
function drumRacksOf(devices: readonly DataModelObject<"1.0.0">[], depth = 0): DrumRack<"1.0.0">[] {
  const found: DrumRack<"1.0.0">[] = [];
  for (const device of devices) {
    if (device instanceof DrumRack) {
      found.push(device);
      continue;
    }
    // Not instanceof: Live's data model hands some racks over as plain
    // objects (measured for Drum Racks); anything exposing a chains array
    // is a rack for this purpose.
    let chains: readonly DataModelObject<"1.0.0">[];
    try {
      const candidate = (device as { chains?: unknown }).chains;
      if (!Array.isArray(candidate)) continue;
      chains = candidate as readonly DataModelObject<"1.0.0">[];
    } catch {
      continue;
    }
    if (chains.length > 0 && chains.every((chain) => chain instanceof DrumChain)) {
      found.push(device as DrumRack<"1.0.0">);
      continue;
    }
    if (depth >= 4) continue;
    for (const chain of chains) {
      const inner = (chain as { devices?: readonly DataModelObject<"1.0.0">[] }).devices;
      if (Array.isArray(inner)) found.push(...drumRacksOf(inner, depth + 1));
    }
  }
  return found;
}

// Target evidence, read from the device -- never assumed from a track name.
function verifiedDrumTarget(track: MidiTrack<"1.0.0">): DrumLiveTarget | null {
  const racks = drumRacksOf(track.devices);
  if (racks.length === 0) return null;
  if (racks.length > 1) throw new Error("multiple_drum_racks: The selected track has more than one Drum Rack; target binding is ambiguous.");
  const padNotes = [...new Set(racks[0].chains.map((chain) => chain.receivingNote))].filter((note) => Number.isInteger(note) && note >= 0 && note <= 127).sort((a, b) => a - b);
  if (padNotes.length === 0) throw new Error("drum_rack_has_no_verified_pads: The selected Drum Rack has no readable pad chains.");
  return {
    role: "drum",
    device_name: racks[0].name,
    device_classes: ["DrumGroupDevice"],
    verified_pad_map: true,
    verified_pad_notes: padNotes,
    bars: 4,
    seed: 1,
    variation_amount: 0.35,
  };
}

function verifiedInstrumentTarget(track: MidiTrack<"1.0.0">): InstrumentLiveTarget | null {
  // The SDK exposes no device-class evidence for non-Drum-Rack instruments,
  // only a display name. Every non-rack device name is forwarded; the Python
  // runtime matches names against the release-pinned bass/chord identity
  // catalog and rejects anything that doesn't resolve to exactly one role on
  // exactly one device (audio effects never match, so they are harmless).
  const deviceNames = track.devices
    .filter((device) => !(device instanceof DrumRack))
    .map((device) => device.name)
    .filter((name) => name.trim().length > 0);
  if (deviceNames.length === 0) return null;
  return {
    device_names: deviceNames,
    bars: 4,
    seed: 1,
    variation_amount: 0.35,
  };
}

// ---------------------------------------------------------------------------
// The bridge sees Live through bridge.ts's structural interfaces; these
// wrappers are the only place the real SDK objects meet them. Each wrapper
// is a thin view -- getters read Live at access time, nothing is cached.

function wrapParam(param: DeviceParameter<"1.0.0">): ParamLike {
  return {
    get name() { return param.name; },
    get min() { return param.min; },
    get max() { return param.max; },
    getValue: () => param.getValue(),
    setValue: (value: number) => param.setValue(value),
  };
}

function wrapDevice(device: Device<"1.0.0">): DeviceLike {
  const wrapped: DeviceLike = {
    get name() { return device.name; },
    get className() { return String((device.constructor as { className?: string }).className ?? "Device"); },
    get parameters() { return device.parameters.map(wrapParam); },
  };
  if (device instanceof Simpler) {
    wrapped.replaceSample = (filePath: string) => device.replaceSample(filePath).then((sample) => sample.filePath);
  }
  return wrapped;
}

function wrapDrumChain(chain: DrumChain<"1.0.0">): DrumChainLike {
  return {
    get receivingNote() { return chain.receivingNote; },
    set receivingNote(value: number) { chain.receivingNote = value; },
    get devices() { return chain.devices.map(wrapDevice); },
    insertDevice: (deviceName: string, index: number) => chain.insertDevice(deviceName, index).then(wrapDevice),
  };
}

function padNotesOf(rack: DrumRack<"1.0.0">): number[] {
  return [...new Set(rack.chains.map((chain) => chain.receivingNote))].filter((note) => Number.isInteger(note) && note >= 0 && note <= 127);
}

function wrapDrumRack(rack: DrumRack<"1.0.0">): DrumRackLike {
  return {
    get name() { return rack.name; },
    get padNotes() { return padNotesOf(rack); },
    get chains() { return rack.chains.map(wrapDrumChain); },
    // A chain inserted into a Drum Rack is a DrumChain in Live's model even
    // when the SDK types it as Chain; its receivingNote is what makes it a pad.
    insertChain: (index: number) => rack.insertChain(index).then((chain) => wrapDrumChain(chain as DrumChain<"1.0.0">)),
  };
}

function wrapClip(clip: MidiClip<"1.0.0">): BridgeClipLike {
  return {
    get notes() { return clip.notes; },
    set notes(value: NoteDescription[]) { clip.notes = value; },
    get name() { return clip.name; },
    set name(value: string) { clip.name = value; },
    get startTime() { return clip.startTime; },
    get endTime() { return clip.endTime; },
    // Handle.id is the SDK's object identity for this session; a clip deleted
    // and drawn again gets a new one.
    get handleId() { return String(clip.handle.id); },
    get lengthBeats() { return clip.looping ? clip.loopEnd - clip.loopStart : clip.endMarker - clip.startMarker; },
  };
}

function wrapAudioClip(clip: AudioClip<"1.0.0">): AudioClipLike {
  return {
    get filePath() { return clip.filePath; },
    get name() { return clip.name; },
    set name(value: string) { clip.name = value; },
  };
}

function wrapSlot(slot: ClipSlot<"1.0.0">): SlotLike {
  return {
    deleteClip: () => slot.deleteClip(),
    createAudioClip: (filePath: string, isWarped?: boolean) =>
      slot.createAudioClip({ filePath, isWarped: isWarped ?? true }).then(wrapAudioClip),
    get clip() {
      const clip = slot.clip;
      if (!clip) return null;
      if (clip instanceof MidiClip) return wrapClip(clip);
      return {
        get notes(): NoteDescription[] { return []; },
        set notes(_value: NoteDescription[]) { throw new Error("slot holds an audio clip"); },
        get name() { return clip.name; },
        set name(_value: string) { throw new Error("slot holds an audio clip"); },
      };
    },
    createMidiClip: (length: number) => slot.createMidiClip(length).then(wrapClip),
  };
}

function wrapTrack(track: Track<"1.0.0">): TrackLike {
  const midi = track instanceof MidiTrack ? track : null;
  const audio = track instanceof AudioTrack ? track : null;
  return {
    get arrangementClips() {
      return track.arrangementClips.map((clip) => ({ name: clip.name, startTime: clip.startTime, endTime: clip.endTime, handleId: String(clip.handle.id) }));
    },
    arrangementMidiClip: (target: ArrangementClipLike) => {
      const clip = track.arrangementClips.find((c) => (target.handleId ? String(c.handle.id) === target.handleId : c.name === target.name && c.startTime === target.startTime && c.endTime === target.endTime));
      return clip instanceof MidiClip ? wrapClip(clip) : null;
    },
    get drumRacks() {
      // Including a Drum Rack nested in an Instrument Rack chain (see drumRacksOf).
      return drumRacksOf(track.devices).map(wrapDrumRack);
    },
    deleteArrangementClip: (target: ArrangementClipLike) => {
      const clip = track.arrangementClips.find((c) => (target.handleId ? String(c.handle.id) === target.handleId : c.name === target.name && c.startTime === target.startTime && c.endTime === target.endTime));
      return clip ? track.deleteClip(clip) : Promise.resolve();
    },
    isAudio: audio !== null,
    createAudioClipInArrangement: (startTime: number, filePath: string, duration?: number, isWarped?: boolean) =>
      audio
        ? audio.createAudioClip({ filePath, startTime, ...(duration === undefined ? {} : { duration }), isWarped: isWarped ?? true }).then(wrapAudioClip)
        : Promise.reject(new Error(`${track.name} is not an audio track`)),
    get name() { return track.name; },
    set name(value: string) { track.name = value; },
    get mute() { return track.mute; },
    set mute(value: boolean) { track.mute = value; },
    get solo() { return track.solo; },
    set solo(value: boolean) { track.solo = value; },
    get arm() { return track.arm; },
    set arm(value: boolean) { track.arm = value; },
    isMidi: midi !== null,
    get devices() { return track.devices.map(wrapDevice); },
    get mixer() { return { volume: wrapParam(track.mixer.volume), panning: wrapParam(track.mixer.panning) }; },
    get clipSlots() { return track.clipSlots.map(wrapSlot); },
    createMidiClip: (start: number, duration: number) =>
      midi ? midi.createMidiClip(start, duration).then(wrapClip) : Promise.reject(new Error(`${track.name} is not a MIDI track`)),
    insertDevice: (deviceName: string, index: number) => track.insertDevice(deviceName, index).then(wrapDevice),
  };
}

function wrapCue(cue: CuePoint<"1.0.0">): CueLike {
  return {
    get name() { return cue.name; },
    set name(value: string) { cue.name = value; },
    get time() { return cue.time; },
  };
}

function liveFromContext(context: ExtensionContext<"1.0.0">): LiveLike {
  const song = context.application.song;
  return {
    get tempo() { return song.tempo; },
    set tempo(value: number) { song.tempo = value; },
    get rootNote() { return song.rootNote; },
    get scaleName() { return song.scaleName; },
    get tracks() { return song.tracks.map(wrapTrack); },
    get cuePoints() { return song.cuePoints.map(wrapCue); },
    createCuePoint: (time: number) => song.createCuePoint(time).then(wrapCue),
    createMidiTrack: () => song.createMidiTrack().then(wrapTrack),
    deleteTrackAt: (index: number) => {
      const target = song.tracks[index];
      return target ? song.deleteTrack(target) : Promise.reject(new Error(`no track at index ${index}`));
    },
    deleteCuePointAt: (time: number) => {
      const target = song.cuePoints.find((cue) => Math.abs(cue.time - time) < 1e-6);
      return target ? song.deleteCuePoint(target) : Promise.reject(new Error(`no cue point at ${time}`));
    },
    withinTransaction: <T,>(fn: () => T) => context.withinTransaction(fn),
    importIntoProject: (filePath: string) => context.resources.importIntoProject(filePath),
    renderPreFxAudio: (trackName: string, startTime: number, endTime: number) => {
      const target = song.tracks.find((t) => t.name === trackName);
      if (!(target instanceof AudioTrack)) return Promise.reject(new Error(`${trackName} is not an audio track`));
      return context.resources.renderPreFxAudio(target, startTime, endTime);
    },
  };
}

export function activate(activation: ActivationContext) {
  const context = initialize(activation, "1.0.0");
  const sessionId = newSessionId();
  const storageDirectory = context.environment.storageDirectory;
  const live = liveFromContext(context);

  // The Loom bridge: the MCP's Live-side endpoint. A hosted extension may
  // only touch its own storage, so the queue lives there; mcp_server
  // discovers this directory under Extensions Data.
  const bridgeRoot = storageDirectory ? join(storageDirectory, "bridge") : null;
  if (bridgeRoot) {
    startBridge(live, bridgeRoot, (line) => console.log(line), { sessionId });
  } else {
    console.error("Loom bridge not started: Live provided no storage directory");
  }

  // "Sensei: Generate" on a Session clip slot. Generation runs the embedded
  // Sensei runtime against the device evidence read from the track; the write
  // is the bridge's own write_clip -- same validation, same ownership rules,
  // same ledger as a write the MCP asks for. A slot holding a clip Loom did
  // not write is refused; Loom's own clip there is replaced in place.
  context.commands.registerCommand("loom.generate", (arg: unknown) => void (async () => {
    try {
      if (!storageDirectory || !bridgeRoot) throw new Error("extension_storage_unavailable: Live did not provide a persistent storage directory.");
      const target = context.getObjectFromHandle(arg as Handle, DataModelObject);
      const slot = target instanceof ClipSlot ? target : target instanceof MidiClip && target.parent instanceof ClipSlot ? target.parent : null;
      if (!slot) throw new Error("clip_slot_unresolved: Sensei Generate must be invoked from a Session ClipSlot or MIDI clip.");
      const track = slot.parent;
      if (!(track instanceof MidiTrack)) throw new Error("target_profile_unresolved: Select a clip slot on a MIDI track.");
      const liveTarget = verifiedDrumTarget(track) ?? verifiedInstrumentTarget(track);
      if (!liveTarget) throw new Error("target_profile_unresolved: Select a clip slot on a MIDI track with a loaded Drum Rack, bass, or chord instrument.");
      const slotIndex = track.clipSlots.findIndex((candidate) => candidate.handle.id === slot.handle.id);
      if (slotIndex < 0) throw new Error("clip_slot_unresolved: the slot is not on its track's clip slot list.");
      writeFileSync(join(storageDirectory, "current_live_target.json"), JSON.stringify(liveTarget, null, 2) + "\n", "utf8");
      const payload = await generatePayload(storageDirectory);
      const notes = payload.notes.map((note) => ({ pitch: note.pitch, start: note.time, duration: note.duration, velocity: note.velocity }));
      const maximumEnd = Math.max(0, ...payload.notes.map((note) => note.time + note.duration));
      const clipLength = payload.clip_length ?? Math.ceil(maximumEnd / 4) * 4;
      const ledger = new ClipLedger(bridgeDirs(bridgeRoot).ledgerFile, sessionId);
      const result = await applyOperation(live, {
        op: "write_clip", track: track.name, slot: slotIndex, name: `Sensei ${"role" in liveTarget ? "drum" : "part"}`,
        length_beats: clipLength, notes, on_conflict: "replace_owned",
      }, { sessionId, ledger });
      console.log(`Loom Generate: wrote ${String(result.note_count)} notes into slot ${slotIndex} of ${track.name}${result.warning ? ` (${String(result.warning)})` : ""}`);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Loom Generate:", error);
      await context.ui.showModalDialog(messageDialog("Loom: Generate blocked", detail), 520, 260);
    }
  })());
  void Promise.all([
    context.ui.registerContextMenuAction("ClipSlot", "Loom: Generate", "loom.generate"),
    context.ui.registerContextMenuAction("MidiClip", "Loom: Generate", "loom.generate"),
  ]).catch((error) => console.error("Loom context-menu registration failed:", error));
}
