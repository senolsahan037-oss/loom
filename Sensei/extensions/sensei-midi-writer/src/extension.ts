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
  initialize,
  type ActivationContext,
  type CuePoint,
  type Device,
  type DeviceParameter,
  type ExtensionContext,
  type Handle,
  type NoteDescription,
  type Song,
  type Track,
} from "@ableton-extensions/sdk";
import {
  startBridge,
  type AudioClipLike,
  type BridgeClipLike,
  type CueLike,
  type DeviceLike,
  type LiveLike,
  type ParamLike,
  type SlotLike,
  type TrackLike,
} from "./bridge.js";
import { execFile } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { promisify } from "node:util";
import { gunzipSync } from "node:zlib";
import embeddedRuntime from "sensei:runtime";
import {
  BEATS_PER_BAR,
  buildArrangementForTrack,
  ensureLocators,
  validatePayload,
  validateSections,
  type ArrangementBuildResult,
  type ArrangementGpsLastBuild,
  type GenerationOutcome,
  type SenseiPayload,
} from "./arrangement.js";
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
type BatchGenerationResult = {
  track: string;
  variation: number | null;
  status: "written" | "blocked" | "track_not_found";
  role?: string;
  genre?: string | string[];
  source?: unknown;
  target_root?: string;
  target_mode?: string;
  reason?: string;
};

const execFileAsync = promisify(execFile);
// Bumped whenever the embedded Python runtime changes: the extension unpacks
// it once per version into storage and reuses it, so an unbumped change would
// leave Live running the old CLI while the source says otherwise.
const RUNTIME_VERSION = "phase6-v12";

function payloadDialog() {
  const html = `<!doctype html><html><body style="background:#292929;color:#ddd;font:13px system-ui;padding:16px">
  <label>Sensei MIDI JSON</label><p style="color:#aaa">Validates and writes only to the selected clip slot.</p>
  <textarea id="payload" style="box-sizing:border-box;width:100%;height:260px;background:#171717;color:#eee;border:1px solid #555;padding:8px"></textarea>
  <div style="text-align:right;margin-top:12px"><button onclick="closeWith(null)">Cancel</button> <button onclick="submit()">Validate & write</button></div>
  <script>
  function send(message){if(window.webkit?.messageHandlers?.live)window.webkit.messageHandlers.live.postMessage(message);else if(window.chrome?.webview)window.chrome.webview.postMessage(message)}
  function closeWith(value){send({method:'close_and_send',params:[JSON.stringify(value)]})}
  function submit(){try{closeWith(JSON.parse(document.getElementById('payload').value))}catch{alert('Enter valid JSON.')}}
  </script></body></html>`;
  return `data:text/html,${encodeURIComponent(html)}`;
}

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

async function generatePayload(storageDirectory: string | undefined): Promise<SenseiPayload> {
  if (!storageDirectory) throw new Error("extension_storage_unavailable: Live did not provide a persistent storage directory.");
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

// Target verification only ever needed the track, never the slot -- the
// track-level form is what the arrangement path uses, where there is no
// ClipSlot at all. The slot wrappers below keep the Session commands
// calling exactly the same evidence rules.
function verifiedDrumTargetForTrack(track: MidiTrack<"1.0.0">): DrumLiveTarget | null {
  // Some factory "kit" presets (e.g. certain Hybrid Kits) don't resolve to
  // the SDK's DrumRack class directly -- Live's own data model reports a
  // plain RackDevice for them -- even though their chains are genuine
  // DrumChains with real pad-note mapping. Accept those too: the pad
  // evidence verified_pad_notes needs comes from the DrumChain instances,
  // not from which wrapper class the top-level device happened to resolve to.
  const racks = track.devices.filter((device): device is DrumRack<"1.0.0"> => {
    if (device instanceof DrumRack) return true;
    if (device instanceof RackDevice) {
      const chains = device.chains;
      return chains.length > 0 && chains.every((chain) => chain instanceof DrumChain);
    }
    return false;
  });
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

function verifiedInstrumentTargetForTrack(track: MidiTrack<"1.0.0">): InstrumentLiveTarget | null {
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

function verifiedDrumTarget(slot: ClipSlot<"1.0.0">): DrumLiveTarget | null {
  const track = slot.parent;
  return track instanceof MidiTrack ? verifiedDrumTargetForTrack(track) : null;
}

function verifiedInstrumentTarget(slot: ClipSlot<"1.0.0">): InstrumentLiveTarget | null {
  const track = slot.parent;
  return track instanceof MidiTrack ? verifiedInstrumentTargetForTrack(track) : null;
}

async function writePayload(context: ExtensionContext<"1.0.0">, slot: ClipSlot<"1.0.0">, payload: SenseiPayload) {
  const { notes, clipLength } = validatePayload(payload);
  const existingClip = slot.clip;
  if (existingClip && !(existingClip instanceof MidiClip)) throw new Error("Select an empty slot or a MIDI clip.");
  if (existingClip instanceof MidiClip && existingClip.duration < clipLength) {
    throw new Error(`The selected MIDI clip is ${existingClip.duration} beats; generated content requires ${clipLength} beats.`);
  }
  await context.withinTransaction(() => {
    if (existingClip instanceof MidiClip) { existingClip.notes = notes; return Promise.resolve(); }
    return slot.createMidiClip(clipLength).then((clip) => { clip.notes = notes; });
  });
}

function readLastArrangementGpsBuild(storageDirectory: string): ArrangementGpsLastBuild {
  // ArrangementGPSBuilder.py writes here too (not ~/Documents) -- the
  // Extension Host's Node runtime is permission-sandboxed and can only
  // read/write inside its own storage directory.
  const path = join(storageDirectory, "arrangementgps_last_build.json");
  if (!existsSync(path)) throw new Error("arrangementgps_plan_missing: No ArrangementGPSBuilder build found. Run ArrangementGPSBuilder in Live first.");
  const parsed = JSON.parse(readFileSync(path, "utf8")) as ArrangementGpsLastBuild;
  if (!Array.isArray(parsed.tracks) || parsed.tracks.length === 0) throw new Error("arrangementgps_plan_invalid: last_build.json has no tracks.");
  return parsed;
}

async function ensureScenes(song: Song<"1.0.0">, minimum: number) {
  while (song.scenes.length < minimum) await song.createScene(-1);
}

// Everything up to (but not including) the write: builds the live target,
// hands it to the Python runtime and reports provenance. Both the Session
// slot path and the Arrangement path go through this, so the two can never
// drift apart on which evidence Sensei is given.
async function generateForTarget(
  baseTarget: DrumLiveTarget | InstrumentLiveTarget,
  seed: number,
  storageDirectory: string,
  defaultGenre: string | undefined,
  targetRoot: string | undefined,
  targetMode: string | undefined,
  excludeReferenceIds: string[],
  section?: { density?: number; genreStyle?: string },
): Promise<{ payload: SenseiPayload; outcome: GenerationOutcome }> {
  const liveTarget: Record<string, unknown> = { ...baseTarget, seed };
  if (defaultGenre) liveTarget.default_genre = defaultGenre;
  // Section evidence for the arrangement path. Density is the section's
  // activity as 0..1 -- Sensei picks a pattern that is already that sparse or
  // that busy rather than thinning one. genre_style names the measured drum
  // pattern candidates are ranked against; an unmeasured style comes back
  // reported, not approximated. Both apply to every role, like
  // exclude_reference_ids.
  if (section?.density !== undefined) liveTarget.density = section.density;
  if (section?.genreStyle) liveTarget.genre_style = section.genreStyle;
  // A drum rack's pitch selects a pad, not a scale degree -- key/mode never
  // applies there, so it's only ever sent for the bass/chord instrument path.
  if (!("role" in baseTarget)) {
    if (targetRoot) liveTarget.target_root = targetRoot;
    if (targetMode) liveTarget.target_mode = targetMode;
  }
  // Diversity is a pool-size concern, not a musical-correctness one -- unlike
  // target_root/target_mode this applies to every role including drum.
  if (excludeReferenceIds.length > 0) liveTarget.exclude_reference_ids = excludeReferenceIds;
  writeFileSync(join(storageDirectory, "current_live_target.json"), JSON.stringify(liveTarget, null, 2) + "\n", "utf8");
  const payload = await generatePayload(storageDirectory);
  const provenance = payload.provenance ?? {};
  return {
    payload,
    outcome: {
      role: typeof provenance.source_role === "string" ? provenance.source_role : undefined,
      genre: (provenance.genre as string | undefined) ?? (provenance.genres as string[] | undefined),
      source: provenance.source_reference_id ?? provenance.source_reference_ids,
      target_root: typeof provenance.target_root === "string" ? provenance.target_root : undefined,
      target_mode: typeof provenance.target_mode === "string" ? provenance.target_mode : undefined,
    },
  };
}

async function attemptVariation(
  context: ExtensionContext<"1.0.0">,
  slot: ClipSlot<"1.0.0">,
  baseTarget: DrumLiveTarget | InstrumentLiveTarget,
  seed: number,
  storageDirectory: string,
  defaultGenre: string | undefined,
  targetRoot: string | undefined,
  targetMode: string | undefined,
  excludeReferenceIds: string[],
): Promise<GenerationOutcome> {
  const { payload, outcome } = await generateForTarget(baseTarget, seed, storageDirectory, defaultGenre, targetRoot, targetMode, excludeReferenceIds);
  await writePayload(context, slot, payload);
  return outcome;
}

async function generateVariationsForTrack(
  context: ExtensionContext<"1.0.0">,
  track: MidiTrack<"1.0.0">,
  trackName: string,
  variationCount: number,
  storageDirectory: string,
  defaultGenre: string | undefined,
  targetRoot: string | undefined,
  targetMode: string | undefined,
): Promise<BatchGenerationResult[]> {
  const results: BatchGenerationResult[] = [];
  // Diversity is scoped to one track's own variations, not shared across
  // tracks -- reset fresh for every call.
  const usedSources = new Set<string>();
  const recordSource = (source: unknown) => {
    if (typeof source === "string") usedSources.add(source);
    else if (Array.isArray(source)) for (const value of source) if (typeof value === "string") usedSources.add(value);
  };
  for (let variationIndex = 0; variationIndex < variationCount; variationIndex++) {
    const slot = track.clipSlots[variationIndex];
    if (!slot) {
      results.push({ track: trackName, variation: variationIndex + 1, status: "blocked", reason: "clip_slot_missing" });
      break;
    }
    const baseTarget = verifiedDrumTarget(slot) ?? verifiedInstrumentTarget(slot);
    if (!baseTarget) {
      results.push({ track: trackName, variation: variationIndex + 1, status: "blocked", reason: "no_recognized_instrument" });
      break;
    }
    try {
      const outcome = await attemptVariation(context, slot, baseTarget, variationIndex + 1, storageDirectory, undefined, targetRoot, targetMode, [...usedSources]);
      recordSource(outcome.source);
      results.push({ track: trackName, variation: variationIndex + 1, status: "written", ...outcome });
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      // genre_evidence_missing means the preset itself is fine but nothing in
      // the dataset ties it to a genre -- retry once with the project's
      // already-resolved genre (from an earlier track in this same batch)
      // rather than treating it the same as an unresolved/ambiguous target.
      if (defaultGenre && reason.includes("genre_evidence_missing")) {
        try {
          const outcome = await attemptVariation(context, slot, baseTarget, variationIndex + 1, storageDirectory, defaultGenre, targetRoot, targetMode, [...usedSources]);
          recordSource(outcome.source);
          results.push({ track: trackName, variation: variationIndex + 1, status: "written", ...outcome });
          continue;
        } catch (retryError) {
          const retryReason = retryError instanceof Error ? retryError.message : String(retryError);
          results.push({ track: trackName, variation: variationIndex + 1, status: "blocked", reason: retryReason });
          break;
        }
      }
      results.push({ track: trackName, variation: variationIndex + 1, status: "blocked", reason });
      // A failure here is track-level (no/ambiguous instrument evidence), not
      // variation-level — retrying the remaining slots would just repeat it.
      break;
    }
  }
  return results;
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
  return {
    get name() { return device.name; },
    get className() { return String((device.constructor as { className?: string }).className ?? "Device"); },
    get parameters() { return device.parameters.map(wrapParam); },
  };
}

function wrapClip(clip: MidiClip<"1.0.0">): BridgeClipLike {
  return {
    get notes() { return clip.notes; },
    set notes(value: NoteDescription[]) { clip.notes = value; },
    get name() { return clip.name; },
    set name(value: string) { clip.name = value; },
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
    clearClipsInRange: (start: number, end: number) => track.clearClipsInRange(start, end),
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
  context.commands.registerCommand("sensei.generate", (arg: unknown) => void (async () => {
    const target = context.getObjectFromHandle(arg as Handle, DataModelObject);
    const slot = target instanceof ClipSlot ? target : target instanceof MidiClip && target.parent instanceof ClipSlot ? target.parent : null;
    try {
      if (!slot) throw new Error("clip_slot_unresolved: Sensei Generate must be invoked from a Session ClipSlot or MIDI clip.");
      const liveTarget = verifiedDrumTarget(slot) ?? verifiedInstrumentTarget(slot);
      if (!liveTarget) throw new Error("target_profile_unresolved: Select a clip slot on a MIDI track with a loaded Drum Rack, bass, or chord instrument.");
      const storageDirectory = context.environment.storageDirectory;
      if (!storageDirectory) throw new Error("extension_storage_unavailable: Live did not provide a persistent storage directory.");
      writeFileSync(join(storageDirectory, "current_live_target.json"), JSON.stringify(liveTarget, null, 2) + "\n", "utf8");
      const payload = await generatePayload(context.environment.storageDirectory);
      await writePayload(context, slot, payload);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Sensei Generate:", error);
      await context.ui.showModalDialog(messageDialog("Sensei Generate blocked", detail), 520, 260);
    }
  })());
  context.commands.registerCommand("sensei.writeMidi", (arg: unknown) => void (async () => {
    const slot = context.getObjectFromHandle(arg as Handle, ClipSlot);
    const response = await context.ui.showModalDialog(payloadDialog(), 620, 420);
    const payload = JSON.parse(response) as SenseiPayload | null;
    if (!payload) return;
    await writePayload(context, slot, payload);
  })().catch((error) => console.error("Sensei MIDI Writer:", error)));
  context.commands.registerCommand("sensei.generateAllFromArrangementGpsPlan", (_arg: unknown) => void (async () => {
    try {
      const storageDirectory = context.environment.storageDirectory;
      if (!storageDirectory) throw new Error("extension_storage_unavailable: Live did not provide a persistent storage directory.");
      const plan = readLastArrangementGpsBuild(storageDirectory);
      const song = context.application.song;
      await ensureScenes(song, 5);
      const tracksByName = new Map<string, MidiTrack<"1.0.0">>();
      for (const track of song.tracks) if (track instanceof MidiTrack) tracksByName.set(track.name, track);

      const allResults: BatchGenerationResult[] = [];
      // The first track that resolves a real single genre becomes the
      // fallback for later tracks whose preset has no genre evidence of its
      // own -- see attemptVariation's genre_evidence_missing retry.
      let projectDefaultGenre: string | undefined;
      const targetRoot = plan.target_root ?? undefined;
      const targetMode = plan.target_mode ?? undefined;
      await context.ui.withinProgressDialog(
        "Sensei: generating from ArrangementGPS plan…",
        { progress: 0 },
        async (update, signal) => {
          for (let index = 0; index < plan.tracks.length; index++) {
            if (signal.aborted) break;
            const planTrack = plan.tracks[index];
            await update(`${planTrack.track_name} (${index + 1}/${plan.tracks.length})`, Math.round((index / plan.tracks.length) * 100));
            const track = tracksByName.get(planTrack.track_name);
            if (!track) {
              allResults.push({ track: planTrack.track_name, variation: null, status: "track_not_found" });
              continue;
            }
            const trackResults = await generateVariationsForTrack(context, track, planTrack.track_name, 5, storageDirectory, projectDefaultGenre, targetRoot, targetMode);
            allResults.push(...trackResults);
            if (!projectDefaultGenre) {
              const firstSingleGenre = trackResults.find((result) => result.status === "written" && typeof result.genre === "string");
              if (firstSingleGenre) projectDefaultGenre = firstSingleGenre.genre as string;
            }
          }
        },
      );

      writeFileSync(join(storageDirectory, "arrangementgps_generation_report.json"), JSON.stringify(allResults, null, 2) + "\n", "utf8");
      const written = allResults.filter((result) => result.status === "written").length;
      const blocked = allResults.filter((result) => result.status === "blocked").length;
      const notFound = allResults.filter((result) => result.status === "track_not_found").length;
      await context.ui.showModalDialog(
        messageDialog("Sensei: ArrangementGPS batch complete", `Written: ${written}\nBlocked: ${blocked}\nTracks not found: ${notFound}\n\nFull report: arrangementgps_generation_report.json`),
        520,
        260,
      );
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Sensei Generate All:", error);
      await context.ui.showModalDialog(messageDialog("Sensei Generate All blocked", detail), 520, 260);
    }
  })());
  context.commands.registerCommand("sensei.buildArrangementFromPlan", (_arg: unknown) => void (async () => {
    try {
      const storageDirectory = context.environment.storageDirectory;
      if (!storageDirectory) throw new Error("extension_storage_unavailable: Live did not provide a persistent storage directory.");
      const plan = readLastArrangementGpsBuild(storageDirectory);
      const sections = plan.sections ?? [];
      // The plan's genre names the measured drum pattern every section is
      // ranked against. Read defensively: older last_build files carry none.
      const planGenre = (plan as { genre?: unknown }).genre;
      const projectGenreStyle = typeof planGenre === "string" && planGenre.trim() ? planGenre.trim().toLowerCase() : undefined;
      if (sections.length === 0) throw new Error("arrangementgps_sections_missing: The plan has no sections. Re-run ArrangementGPSBuilder in Live to write a plan that includes them.");
      validateSections(sections);
      const song = context.application.song;
      const targetRoot = plan.target_root ?? undefined;
      const targetMode = plan.target_mode ?? undefined;
      let projectDefaultGenre: string | undefined;

      const locatorSummary = await ensureLocators(song, sections);

      const tracksByName = new Map<string, MidiTrack<"1.0.0">>();
      for (const track of song.tracks) if (track instanceof MidiTrack) tracksByName.set(track.name, track);

      const allResults: ArrangementBuildResult[] = [];
      await context.ui.withinProgressDialog(
        "Sensei: building arrangement from ArrangementGPS plan…",
        { progress: 0 },
        async (update, signal) => {
          for (let index = 0; index < plan.tracks.length; index++) {
            if (signal.aborted) break;
            const planTrack = plan.tracks[index];
            await update(`${planTrack.track_name} (${index + 1}/${plan.tracks.length})`, Math.round((index / plan.tracks.length) * 100));
            const track = tracksByName.get(planTrack.track_name);
            if (!track) {
              allResults.push({ track: planTrack.track_name, section: null, status: "track_not_found" });
              continue;
            }
            // Target verification stays here because it needs the SDK's
            // instanceof evidence; everything downstream of it is injected so
            // the build logic can be tested without Live.
            const baseTarget = verifiedDrumTargetForTrack(track) ?? verifiedInstrumentTargetForTrack(track);
            const trackResults = await buildArrangementForTrack(
              context,
              track,
              planTrack,
              sections,
              baseTarget !== null,
              (seed, excludeReferenceIds, section) => generateForTarget(baseTarget!, seed, storageDirectory, projectDefaultGenre, targetRoot, targetMode, excludeReferenceIds, { density: section.density, genreStyle: projectGenreStyle }),
            );
            allResults.push(...trackResults);
            // Same fallback the Session batch uses: the first track that
            // resolves one real genre becomes the default for later tracks
            // whose preset carries no genre evidence of its own.
            if (!projectDefaultGenre) {
              const firstSingleGenre = trackResults.find((result) => result.status === "written" && typeof result.genre === "string");
              if (firstSingleGenre) projectDefaultGenre = firstSingleGenre.genre as string;
            }
          }
        },
      );

      // Read back what Live actually holds now, rather than trusting the
      // write calls -- this is the only part of the report that is evidence.
      const verifiedLocators = song.cuePoints
        .map((cuePoint) => ({ name: cuePoint.name, beat: cuePoint.time, bar: cuePoint.time / BEATS_PER_BAR + 1 }))
        .sort((a, b) => a.beat - b.beat);
      const verifiedClips = plan.tracks.map((planTrack) => {
        const track = tracksByName.get(planTrack.track_name);
        return {
          track: planTrack.track_name,
          arrangement_clips: track ? track.arrangementClips.map((clip) => ({ name: clip.name, start_beat: clip.startTime, end_beat: clip.endTime })) : null,
        };
      });

      const written = allResults.filter((result) => result.status === "written").length;
      const blocked = allResults.filter((result) => result.status === "blocked").length;
      const notFound = allResults.filter((result) => result.status === "track_not_found").length;
      const skipped = allResults.filter((result) => result.status === "skipped").length;
      writeFileSync(
        join(storageDirectory, "arrangement_build_report.json"),
        JSON.stringify({
          built_at: new Date().toISOString(),
          project_name: plan.project_name,
          beats_per_bar: BEATS_PER_BAR,
          sections,
          locators: locatorSummary,
          totals: { written, blocked, track_not_found: notFound, skipped },
          results: allResults,
          verified_locators: verifiedLocators,
          verified_clips: verifiedClips,
        }, null, 2) + "\n",
        "utf8",
      );
      await context.ui.showModalDialog(
        messageDialog(
          "Sensei: arrangement build complete",
          `Locators: ${locatorSummary.created} created, ${locatorSummary.adopted} adopted (${verifiedLocators.length} in song)\n` +
            `Clips written: ${written}\nBlocked: ${blocked}\nSkipped: ${skipped}\nTracks not found: ${notFound}\n\n` +
            `Full report: arrangement_build_report.json`,
        ),
        560,
        300,
      );
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Sensei Build Arrangement:", error);
      await context.ui.showModalDialog(messageDialog("Sensei Build Arrangement blocked", detail), 560, 280);
    }
  })());
  void Promise.all([
    context.ui.registerContextMenuAction("ClipSlot", "Sensei: Generate", "sensei.generate"),
    context.ui.registerContextMenuAction("MidiClip", "Sensei: Generate", "sensei.generate"),
    context.ui.registerContextMenuAction("MidiTrack", "Sensei: Generate All (ArrangementGPS Plan)", "sensei.generateAllFromArrangementGpsPlan"),
    context.ui.registerContextMenuAction("MidiTrack", "Sensei: Build Arrangement (ArrangementGPS Plan)", "sensei.buildArrangementFromPlan"),
  ]).catch((error) => console.error("Sensei context-menu registration failed:", error));
  if (process.env.SENSEI_ENABLE_MANUAL_MIDI === "1") {
    void context.ui.registerContextMenuAction("ClipSlot", "Sensei: Write MIDI JSON…", "sensei.writeMidi");
  }

  // The Loom bridge: the MCP's Live-side endpoint, now inside the extension.
  // A hosted extension may only touch its own storage, so the queue lives
  // there; mcp_server reads LOOM_BRIDGE_ROOT or discovers this directory.
  const storageDirectory = context.environment.storageDirectory;
  if (storageDirectory) {
    startBridge(liveFromContext(context), join(storageDirectory, "bridge"), (line) => console.log(line));
  } else {
    console.error("Loom bridge not started: Live provided no storage directory");
  }
}
