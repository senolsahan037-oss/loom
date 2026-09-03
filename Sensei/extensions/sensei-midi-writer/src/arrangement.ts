// Arrangement-building logic, deliberately kept free of any runtime SDK
// import so it can be exercised headlessly (see tests/arrangement.test.ts).
// Everything Live-specific arrives through the narrow structural interfaces
// below, which the real SDK objects satisfy as-is, and through an injected
// generate function. The point is that nothing here needs a running Live to
// be proven correct -- only the SDK calls themselves do.
import type { NoteDescription } from "@ableton-extensions/sdk";

export type SenseiNote = { pitch: number; time: number; duration: number; velocity: number };
export type SenseiPayload = { schema_version?: string; notes: SenseiNote[]; clip_length?: number; provenance?: Record<string, unknown> };
// What the arrangement path tells Sensei about the section being written.
export type SectionEvidence = { density?: number };
export type GenerationOutcome = { role?: string; genre?: string | string[]; source?: unknown; target_root?: string; target_mode?: string };

export type ArrangementGpsBarRange = { start_bar: number; end_bar: number };
export type ArrangementGpsSection = { id?: string; name: string; start_bar: number; end_bar: number };
export type ArrangementGpsPlanTrack = {
  track_name: string;
  instrument_family: string | null;
  clip_start_bar?: number;
  clip_end_bar?: number;
  mute_regions?: ArrangementGpsBarRange[];
  section_activity?: Record<string, number>;
  // "drum" | "bass" | "chord", or null for a lane Sensei has no role for.
  // Absent (undefined) means an older plan that predates the field, which
  // must keep falling through to instrument verification rather than being
  // treated as unsupported.
  sensei_role?: string | null;
};
export type ArrangementGpsLastBuild = {
  built_at: string;
  project_name: string;
  action_list_path: string;
  target_root?: string | null;
  target_mode?: string | null;
  total_bars?: number;
  sections?: ArrangementGpsSection[];
  tracks: ArrangementGpsPlanTrack[];
};
export type ArrangementBuildResult = {
  track: string;
  section: string | null;
  status: "written" | "blocked" | "track_not_found" | "skipped";
  start_bar?: number;
  length_bars?: number;
  notes?: number;
  role?: string;
  genre?: string | string[];
  source?: unknown;
  activity?: number;
  velocity_scale?: number;
  // The section's activity as the 0..1 density Sensei was asked for.
  density?: number;
  reason?: string;
};

// The real SDK classes satisfy these structurally; the tests pass fakes.
export interface ClipLike {
  notes: NoteDescription[];
  name: string;
}
export interface ArrangementTrackLike {
  clearClipsInRange(startTime: number, endTime: number): Promise<void>;
  createMidiClip(startTime: number, duration: number): Promise<ClipLike>;
}
export interface CuePointLike {
  name: string;
  readonly time: number;
}
export interface SongLike {
  readonly cuePoints: readonly CuePointLike[];
  createCuePoint(time: number): Promise<CuePointLike>;
}
export interface TransactionRunner {
  withinTransaction(action: () => Promise<void>): Promise<unknown>;
}

// The SDK exposes no song time signature -- Song has tempo, rootNote and
// scaleName, but the only signature accessor in the whole API is on Scene.
// Bar->beat conversion therefore has to assume 4/4. Recorded as GAP-003 in
// Docs/MISSING_CONTROLS_LOG.md rather than guessed silently; every plan bar
// number goes through barToBeat so there is exactly one place to change.
export const BEATS_PER_BAR = 4;
// A tiled section clip can hold far more notes than a single 4-bar pattern;
// this is the arrangement-side equivalent of validatePayload's 2048 cap.
export const MAX_ARRANGEMENT_CLIP_NOTES = 4096;

export function barToBeat(bar: number) {
  return (bar - 1) * BEATS_PER_BAR;
}

// Whether a lane plays in a section at all is already decided upstream by
// mute_regions. The rest of the 0-100 activity number is used twice: as
// dynamics here (velocityScaleFor), and as density -- Sensei now selects a
// pattern that is already sparse or already busy for the section, which is
// what GAP-005 asked for. Notes are never dropped to thin a part; that would
// remove downbeats as readily as ghost notes.
export const MIN_VELOCITY_SCALE = 0.6;
export function densityFor(activity: number | undefined): number | undefined {
  if (activity === undefined || !Number.isFinite(activity)) return undefined;
  return Math.min(100, Math.max(0, activity)) / 100;
}

export function velocityScaleFor(activity: number | undefined): number {
  if (activity === undefined || !Number.isFinite(activity)) return 1;
  const clamped = Math.min(100, Math.max(0, activity));
  return MIN_VELOCITY_SCALE + (1 - MIN_VELOCITY_SCALE) * (clamped / 100);
}

export function scaleVelocities(notes: NoteDescription[], scale: number): NoteDescription[] {
  if (scale >= 1) return notes;
  return notes.map((note) => ({
    ...note,
    // NoteDescription.velocity is optional; an unspecified velocity means
    // "Live's default", and scaling something unspecified is meaningless, so
    // it is left alone. Velocity 0 would silence the note entirely, which is
    // a different musical statement than "quieter" -- floor at 1.
    velocity: note.velocity === undefined ? undefined : Math.max(1, Math.min(127, Math.round(note.velocity * scale))),
  }));
}

export function validatePayload(value: unknown): { notes: NoteDescription[]; clipLength: number } {
  if (!value || typeof value !== "object" || !Array.isArray((value as SenseiPayload).notes)) throw new Error("Payload must contain a notes array.");
  const notes = (value as SenseiPayload).notes;
  if (notes.length === 0 || notes.length > 2048) throw new Error("Note count must be between 1 and 2048.");
  const normalized = notes.map((note, index) => {
    for (const key of ["pitch", "time", "duration", "velocity"] as const) if (!Number.isFinite(note[key])) throw new Error(`Note ${index}: ${key} must be finite.`);
    if (!Number.isInteger(note.pitch) || note.pitch < 0 || note.pitch > 127) throw new Error(`Note ${index}: pitch must be 0–127.`);
    if (!Number.isInteger(note.velocity) || note.velocity < 1 || note.velocity > 127) throw new Error(`Note ${index}: velocity must be 1–127.`);
    if (note.time < 0 || note.duration <= 0) throw new Error(`Note ${index}: invalid timing.`);
    return { pitch: note.pitch, startTime: note.time, duration: note.duration, velocity: note.velocity };
  });
  const maximumEnd = Math.max(...normalized.map((note) => note.startTime + note.duration));
  const requestedLength = (value as SenseiPayload).clip_length;
  const clipLength = requestedLength === undefined ? Math.ceil(maximumEnd / 4) * 4 : requestedLength;
  if (!Number.isFinite(clipLength) || clipLength <= 0 || clipLength > 256 || maximumEnd > clipLength) throw new Error("Invalid clip_length.");
  return { notes: normalized, clipLength };
}

// Sensei generates a fixed-length pattern (4 bars by default); an arrangement
// section is 8 or 16 bars. loopEnd is read-only in the SDK (GAP-004), so the
// pattern is tiled explicitly instead of relying on Live's loop brace -- what
// gets written is then exactly what was computed here, which makes reading
// the clip's notes back a real verification rather than a guess.
export function tileNotes(notes: NoteDescription[], patternLength: number, targetLength: number): NoteDescription[] {
  if (!(patternLength > 0) || targetLength <= patternLength) return notes;
  const tiled: NoteDescription[] = [];
  for (let offset = 0; offset + patternLength <= targetLength + 1e-9; offset += patternLength) {
    for (const note of notes) {
      // A note that already overruns the pattern would collide with the next
      // repeat, so it is dropped rather than allowed to overlap.
      if (note.startTime + note.duration > patternLength + 1e-9) continue;
      tiled.push({ pitch: note.pitch, startTime: note.startTime + offset, duration: note.duration, velocity: note.velocity });
    }
  }
  return tiled.length > 0 ? tiled : notes;
}

export async function writeArrangementClip(
  runner: TransactionRunner,
  track: ArrangementTrackLike,
  startBeat: number,
  lengthBeats: number,
  payload: SenseiPayload,
  clipName: string,
  velocityScale = 1,
): Promise<number> {
  const { notes, clipLength } = validatePayload(payload);
  const tiled = scaleVelocities(tileNotes(notes, clipLength, lengthBeats), velocityScale);
  if (tiled.length > MAX_ARRANGEMENT_CLIP_NOTES) throw new Error(`arrangement_clip_too_dense: "${clipName}" resolved to ${tiled.length} notes.`);
  await runner.withinTransaction(async () => {
    // Rebuilding a section must be idempotent: clear the exact range first so
    // a second run replaces its own clip instead of stacking a new one on top.
    await track.clearClipsInRange(startBeat, startBeat + lengthBeats);
    const clip = await track.createMidiClip(startBeat, lengthBeats);
    clip.notes = tiled;
    clip.name = clipName;
  });
  return tiled.length;
}

// Locators are never deleted or moved here. A cue point already sitting on a
// section boundary is adopted (renamed to the section); anything the user
// placed elsewhere is left untouched.
export async function ensureLocators(song: SongLike, sections: ArrangementGpsSection[]): Promise<{ created: number; adopted: number }> {
  let created = 0;
  let adopted = 0;
  for (const section of sections) {
    const beat = barToBeat(section.start_bar);
    const existing = song.cuePoints.find((cuePoint) => Math.abs(cuePoint.time - beat) < 1e-6);
    if (existing) {
      if (existing.name !== section.name) existing.name = section.name;
      adopted++;
      continue;
    }
    const cuePoint = await song.createCuePoint(beat);
    cuePoint.name = section.name;
    created++;
  }
  return { created, adopted };
}

export function validateSections(sections: ArrangementGpsSection[]) {
  for (const section of sections) {
    if (!Number.isInteger(section.start_bar) || !Number.isInteger(section.end_bar) || section.start_bar < 1 || section.end_bar < section.start_bar) {
      throw new Error(`arrangementgps_section_invalid: "${section.name}" has an unusable bar range.`);
    }
  }
}

// The plan's clip_start_bar/clip_end_bar/mute_regions already encode which
// sections a lane is active in -- this only reads them, it never invents a
// section for a track.
export function sectionsForTrack(sections: ArrangementGpsSection[], planTrack: ArrangementGpsPlanTrack): ArrangementGpsSection[] {
  const startBar = planTrack.clip_start_bar ?? 1;
  const endBar = planTrack.clip_end_bar ?? Number.MAX_SAFE_INTEGER;
  const muteRegions = planTrack.mute_regions ?? [];
  return sections.filter((section) => {
    if (section.end_bar < startBar || section.start_bar > endBar) return false;
    return !muteRegions.some((region) => section.start_bar >= region.start_bar && section.end_bar <= region.end_bar);
  });
}

export async function buildArrangementForTrack(
  runner: TransactionRunner,
  track: ArrangementTrackLike,
  planTrack: ArrangementGpsPlanTrack,
  sections: ArrangementGpsSection[],
  hasVerifiedTarget: boolean,
  generate: (seed: number, excludeReferenceIds: string[], section: SectionEvidence) => Promise<{ payload: SenseiPayload; outcome: GenerationOutcome }>,
): Promise<ArrangementBuildResult[]> {
  const trackName = planTrack.track_name;
  const activeSections = sectionsForTrack(sections, planTrack);
  if (activeSections.length === 0) return [{ track: trackName, section: null, status: "skipped", reason: "no_active_sections" }];
  // Sensei generates for drum, bass and chord only. A melody/vocal/fx lane
  // is out of scope by design, not a failure -- reporting it as blocked made
  // a deliberate boundary look like a bug.
  if (planTrack.sensei_role === null) return [{ track: trackName, section: null, status: "skipped", reason: "role_unsupported" }];
  if (!hasVerifiedTarget) return [{ track: trackName, section: null, status: "blocked", reason: "no_recognized_instrument" }];

  const results: ArrangementBuildResult[] = [];
  const usedSources = new Set<string>();
  const recordSource = (source: unknown) => {
    if (typeof source === "string") usedSources.add(source);
    else if (Array.isArray(source)) for (const value of source) if (typeof value === "string") usedSources.add(value);
  };
  for (let index = 0; index < activeSections.length; index++) {
    const section = activeSections[index];
    const lengthBars = section.end_bar - section.start_bar + 1;
    try {
      const activity = section.id === undefined ? undefined : planTrack.section_activity?.[section.id];
      const velocityScale = velocityScaleFor(activity);
      const density = densityFor(activity);
      const { payload, outcome } = await generate(index + 1, [...usedSources], { density });
      const noteCount = await writeArrangementClip(runner, track, barToBeat(section.start_bar), lengthBars * BEATS_PER_BAR, payload, section.name, velocityScale);
      recordSource(outcome.source);
      results.push({ track: trackName, section: section.name, status: "written", start_bar: section.start_bar, length_bars: lengthBars, notes: noteCount, role: outcome.role, genre: outcome.genre, source: outcome.source, activity, velocity_scale: Number(velocityScale.toFixed(3)), density });
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      results.push({ track: trackName, section: section.name, status: "blocked", start_bar: section.start_bar, length_bars: lengthBars, reason });
      // Same posture as the Session batch: a failure here is track-level
      // (evidence/instrument), so the remaining sections would only repeat it.
      break;
    }
  }
  return results;
}
