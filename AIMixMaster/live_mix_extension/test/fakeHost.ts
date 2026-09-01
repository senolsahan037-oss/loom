// Minimal stand-in for the real Ableton Extension Host, used to develop and test
// extension.ts without opening Live. It implements exactly the low-level
// DataModelModule_1_0_0 surface the real @ableton-extensions/sdk calls into, then
// hands off to the real `initialize()` from the sdk package so all the typed
// wrapper classes (Song, AudioTrack, TrackMixer, DeviceParameter, ...) behave
// identically to a real session -- only the data underneath is fake.

import type { ActivationContext } from "@ableton-extensions/sdk";

export interface FakeTrackSeed {
  name: string;
  mute?: boolean;
  solo?: boolean;
  volume?: number; // internal range, defaults to 0.85 (Live's unity default)
  pan?: number; // internal range -1..1, default 0
  /** "Track" simulates a group/bus track -- the SDK has no dedicated GroupTrack class, a group resolves as the base Track. Defaults to "AudioTrack". */
  kind?: "AudioTrack" | "Track";
  /** Device names already on the track when the fake host is built, e.g. ["Eq8", "Saturator"]. */
  devices?: string[];
}

interface FakeTrack {
  kind: "AudioTrack" | "Track";
  id: bigint;
  name: string;
  mute: boolean;
  solo: boolean;
  mixerId: bigint;
  deviceIds: bigint[];
}

interface FakeDevice {
  kind: "Device";
  id: bigint;
  name: string;
  parameterIds: bigint[];
}

interface FakeMixer {
  kind: "MixerDevice";
  id: bigint;
  trackId: bigint;
  volumeId: bigint;
  panId: bigint;
}

interface FakeParameter {
  kind: "DeviceParameter";
  id: bigint;
  name: string;
  min: number;
  max: number;
  isQuantized: boolean;
  defaultValue: number;
  value: number;
}

type FakeObject = FakeTrack | FakeMixer | FakeParameter | FakeDevice | { kind: "Application" | "Song"; id: bigint };

function notImplemented(name: string) {
  return () => {
    throw new Error(`fakeHost: ${name} is not implemented in the fake extension host`);
  };
}

export function buildFakeActivationContext(seeds: FakeTrackSeed[], tempDirectory: string, options: { ipcLatencyMs?: number } = {}) {
  const ipcLatencyMs = options.ipcLatencyMs ?? 0;
  const withLatency = <T>(value: T): Promise<T> =>
    ipcLatencyMs > 0 ? new Promise((resolve) => setTimeout(() => resolve(value), ipcLatencyMs)) : Promise.resolve(value);
  let nextId = 1n;
  const objects = new Map<bigint, FakeObject>();

  function allocate<T extends FakeObject>(object: Omit<T, "id">): T {
    const id = nextId++;
    const full = { ...object, id } as T;
    objects.set(id, full);
    return full;
  }

  // Every fake device gets a couple of generic parameters -- enough to test
  // list/set-parameter logic without needing to model any real device's
  // actual parameter layout (that's Live's job, not this fake's).
  function createFakeDevice(name: string): FakeDevice {
    const paramA = allocate<FakeParameter>({
      kind: "DeviceParameter", name: "Param A", min: 0.0, max: 1.0, isQuantized: false, defaultValue: 0.5, value: 0.5,
    });
    const paramB = allocate<FakeParameter>({
      kind: "DeviceParameter", name: "Param B", min: -12.0, max: 12.0, isQuantized: false, defaultValue: 0.0, value: 0.0,
    });
    return allocate<FakeDevice>({ kind: "Device", name, parameterIds: [paramA.id, paramB.id] });
  }

  const applicationHandle = { id: nextId++ };
  objects.set(applicationHandle.id, { kind: "Application", id: applicationHandle.id });
  const songHandle = { id: nextId++ };
  objects.set(songHandle.id, { kind: "Song", id: songHandle.id });

  const tracks: FakeTrack[] = seeds.map((seed) => {
    const volumeParam = allocate<FakeParameter>({
      kind: "DeviceParameter",
      name: "Volume",
      min: 0.0,
      max: 1.0,
      isQuantized: false,
      defaultValue: 0.85,
      value: seed.volume ?? 0.85,
    });
    const panParam = allocate<FakeParameter>({
      kind: "DeviceParameter",
      name: "Pan",
      min: -1.0,
      max: 1.0,
      isQuantized: false,
      defaultValue: 0.0,
      value: seed.pan ?? 0.0,
    });
    const mixer = allocate<FakeMixer>({
      kind: "MixerDevice",
      trackId: 0n, // patched below
      volumeId: volumeParam.id,
      panId: panParam.id,
    });
    const deviceIds = (seed.devices ?? []).map((deviceName) => createFakeDevice(deviceName).id);
    const track = allocate<FakeTrack>({
      kind: seed.kind ?? "AudioTrack",
      name: seed.name,
      mute: seed.mute ?? false,
      solo: seed.solo ?? false,
      mixerId: mixer.id,
      deviceIds,
    });
    mixer.trackId = track.id;
    return track;
  });

  function get<T extends FakeObject>(id: bigint): T {
    const obj = objects.get(id);
    if (!obj) throw new Error(`fakeHost: unknown handle ${id}`);
    return obj as T;
  }

  const dataModel = {
    getObjectIsOfClass: (handle: { id: bigint }, className: string) => get(handle.id).kind === className,
    getObjectCanonicalParent: () => null,
    getRoot: () => applicationHandle,
    rootGetSong: () => songHandle,
    songGetTempo: () => 120,
    songSetTempo: notImplemented("songSetTempo"),
    songGetTracks: () => tracks.map((track) => ({ id: track.id })),
    songGetReturnTracks: () => [],
    songGetMainTrack: notImplemented("songGetMainTrack"),
    songGetGridQuantization: () => 0,
    songGetGridIsTriplet: () => false,
    songGetRootNote: () => 0n,
    songGetScaleName: () => "",
    songGetScaleMode: () => false,
    songGetScaleIntervals: () => [],
    trackGetName: (handle: { id: bigint }) => get<FakeTrack>(handle.id).name,
    trackSetName: (handle: { id: bigint }, value: string) => {
      get<FakeTrack>(handle.id).name = value;
    },
    trackGetMute: (handle: { id: bigint }) => get<FakeTrack>(handle.id).mute,
    trackSetMute: (handle: { id: bigint }, value: boolean) => {
      get<FakeTrack>(handle.id).mute = value;
    },
    trackGetSolo: (handle: { id: bigint }) => get<FakeTrack>(handle.id).solo,
    trackSetSolo: (handle: { id: bigint }, value: boolean) => {
      get<FakeTrack>(handle.id).solo = value;
    },
    trackGetMutedViaSolo: () => false,
    trackGetArm: () => false,
    trackSetArm: notImplemented("trackSetArm"),
    trackGetGroupTrack: () => null,
    trackGetClipSlots: () => [],
    trackGetTakeLanes: () => [],
    trackGetArrangementClips: () => [],
    trackGetDevices: (handle: { id: bigint }) => get<FakeTrack>(handle.id).deviceIds.map((id) => ({ id })),
    trackGetMixerDevice: (handle: { id: bigint }) => ({ id: get<FakeTrack>(handle.id).mixerId }),
    trackCreateTakeLane: notImplemented("trackCreateTakeLane"),
    trackCreateMidiClip: notImplemented("trackCreateMidiClip"),
    trackCreateAudioClip: notImplemented("trackCreateAudioClip"),
    trackInsertDevice: (
      handle: { id: bigint },
      deviceName: string,
      index: bigint,
      onResult: (handle: { id: bigint }) => void,
      _onError: (error: string) => void,
    ) => {
      const track = get<FakeTrack>(handle.id);
      const device = createFakeDevice(deviceName);
      const clampedIndex = Math.max(0, Math.min(Number(index), track.deviceIds.length));
      track.deviceIds.splice(clampedIndex, 0, device.id);
      void withLatency(undefined).then(() => onResult({ id: device.id }));
    },
    trackDeleteDevice: notImplemented("trackDeleteDevice"),
    trackDuplicateDevice: notImplemented("trackDuplicateDevice"),
    trackDeleteClip: notImplemented("trackDeleteClip"),
    trackClearClipsInRange: notImplemented("trackClearClipsInRange"),
    withinTransaction: <T>(fn: () => T): T => fn(),
    clipGetName: notImplemented("clipGetName"),
    clipSetName: notImplemented("clipSetName"),
    clipGetStartTime: notImplemented("clipGetStartTime"),
    clipGetEndTime: notImplemented("clipGetEndTime"),
    clipGetStartMarker: notImplemented("clipGetStartMarker"),
    clipGetEndMarker: notImplemented("clipGetEndMarker"),
    clipGetLooping: notImplemented("clipGetLooping"),
    clipSetLooping: notImplemented("clipSetLooping"),
    clipGetLoopStart: notImplemented("clipGetLoopStart"),
    clipGetLoopEnd: notImplemented("clipGetLoopEnd"),
    clipGetColor: notImplemented("clipGetColor"),
    clipSetColor: notImplemented("clipSetColor"),
    clipGetMuted: notImplemented("clipGetMuted"),
    clipSetMuted: notImplemented("clipSetMuted"),
    midiclipGetNotes: notImplemented("midiclipGetNotes"),
    midiclipSetNotes: notImplemented("midiclipSetNotes"),
    audioclipGetFilePath: notImplemented("audioclipGetFilePath"),
    audioclipGetWarping: notImplemented("audioclipGetWarping"),
    audioclipSetWarping: notImplemented("audioclipSetWarping"),
    audioclipGetWarpMode: notImplemented("audioclipGetWarpMode"),
    audioclipSetWarpMode: notImplemented("audioclipSetWarpMode"),
    audioclipGetWarpMarkers: notImplemented("audioclipGetWarpMarkers"),
    clipslotGetClip: notImplemented("clipslotGetClip"),
    clipslotDeleteClip: notImplemented("clipslotDeleteClip"),
    clipslotCreateMidiClip: notImplemented("clipslotCreateMidiClip"),
    clipslotCreateAudioClip: notImplemented("clipslotCreateAudioClip"),
    takelaneGetClips: notImplemented("takelaneGetClips"),
    takelaneGetName: notImplemented("takelaneGetName"),
    takelaneSetName: notImplemented("takelaneSetName"),
    takelaneCreateMidiClip: notImplemented("takelaneCreateMidiClip"),
    takelaneCreateAudioClip: notImplemented("takelaneCreateAudioClip"),
    deviceGetName: (handle: { id: bigint }) => get<FakeDevice>(handle.id).name,
    deviceGetParameters: (handle: { id: bigint }) => get<FakeDevice>(handle.id).parameterIds.map((id) => ({ id })),
    sampleGetFilePath: notImplemented("sampleGetFilePath"),
    simplerGetSample: notImplemented("simplerGetSample"),
    simplerReplaceSample: notImplemented("simplerReplaceSample"),
    chainGetDevices: notImplemented("chainGetDevices"),
    chainGetMixerDevice: notImplemented("chainGetMixerDevice"),
    chainInsertDevice: notImplemented("chainInsertDevice"),
    chainDeleteDevice: notImplemented("chainDeleteDevice"),
    chainDuplicateDevice: notImplemented("chainDuplicateDevice"),
    rackdeviceGetChains: notImplemented("rackdeviceGetChains"),
    rackdeviceInsertChain: notImplemented("rackdeviceInsertChain"),
    drumchainGetReceivingNote: notImplemented("drumchainGetReceivingNote"),
    drumchainSetReceivingNote: notImplemented("drumchainSetReceivingNote"),
    songGetScenes: () => [],
    sceneGetName: notImplemented("sceneGetName"),
    sceneSetName: notImplemented("sceneSetName"),
    sceneGetTempo: notImplemented("sceneGetTempo"),
    sceneGetSignatureNumerator: notImplemented("sceneGetSignatureNumerator"),
    sceneGetSignatureDenominator: notImplemented("sceneGetSignatureDenominator"),
    songGetCuePoints: () => [],
    songCreateScene: notImplemented("songCreateScene"),
    songCreateMidiTrack: notImplemented("songCreateMidiTrack"),
    songCreateAudioTrack: notImplemented("songCreateAudioTrack"),
    songDeleteTrack: notImplemented("songDeleteTrack"),
    songDeleteScene: notImplemented("songDeleteScene"),
    songDuplicateTrack: notImplemented("songDuplicateTrack"),
    songDuplicateScene: notImplemented("songDuplicateScene"),
    songCreateCuePoint: notImplemented("songCreateCuePoint"),
    songDeleteCuePoint: notImplemented("songDeleteCuePoint"),
    cuePointGetTime: notImplemented("cuePointGetTime"),
    cuePointGetName: notImplemented("cuePointGetName"),
    cuePointSetName: notImplemented("cuePointSetName"),
    deviceParameterGetName: (handle: { id: bigint }) => get<FakeParameter>(handle.id).name,
    deviceParameterGetInternalMin: (handle: { id: bigint }) => get<FakeParameter>(handle.id).min,
    deviceParameterGetInternalMax: (handle: { id: bigint }) => get<FakeParameter>(handle.id).max,
    deviceParameterGetIsQuantized: (handle: { id: bigint }) => get<FakeParameter>(handle.id).isQuantized,
    deviceParameterGetDefaultValue: (handle: { id: bigint }) => get<FakeParameter>(handle.id).defaultValue,
    deviceParameterGetValueItems: () => [],
    deviceParameterGetInternalValue: (handle: { id: bigint }, onResult: (value: number) => void) => {
      void withLatency(get<FakeParameter>(handle.id).value).then(onResult);
    },
    deviceParameterSetInternalValue: (
      handle: { id: bigint },
      value: number,
      onResult: () => void,
      _onError: (error: string) => void,
    ) => {
      get<FakeParameter>(handle.id).value = value;
      onResult();
    },
    mixerdeviceGetVolume: (handle: { id: bigint }) => ({ id: get<FakeMixer>(handle.id).volumeId }),
    mixerdeviceGetPanning: (handle: { id: bigint }) => ({ id: get<FakeMixer>(handle.id).panId }),
    mixerdeviceGetSends: () => [],
    chainmixerdeviceGetVolume: notImplemented("chainmixerdeviceGetVolume"),
    chainmixerdeviceGetPanning: notImplemented("chainmixerdeviceGetPanning"),
    chainmixerdeviceGetSends: notImplemented("chainmixerdeviceGetSends"),
  };

  const commands = {
    registerCommand: () => {},
    executeCommand: () => {},
  };
  const ui = {
    registerContextMenuAction: () => {},
    showModalDialog: notImplemented("showModalDialog"),
    showProgressDialog: notImplemented("showProgressDialog"),
  };
  const resources = {
    renderPreFxAudio: notImplemented("renderPreFxAudio"),
    importIntoProject: notImplemented("importIntoProject"),
  };

  const activation: ActivationContext = {
    hostApiVersion: "1.0.0",
    initializeExtensionHost: (_options: { apiVersion: string }) =>
      ({
        commands,
        dataModel,
        environment: {
          storageDirectory: tempDirectory,
          tempDirectory,
          language: "EN",
        },
        resources,
        ui,
      }) as never,
  };

  return { activation, tracks };
}
