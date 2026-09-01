import fs from "fs";
import os from "os";
import path from "path";

// Sensei's own instrument-genre catalog (Core Library included as of
// 2026-08-12) is the source of truth for "which real, loadable instrument
// carries which genre tag" -- reading it directly avoids ArrangementGPS
// maintaining a second, parallel genre-tagging system.
const SENSEI_IDENTITY_PATH = path.join(os.homedir(), "Desktop", "Loom", "Sensei", "data", "genre_identity", "ableton_preset_genre_identities.jsonl");
const ROLE_BY_SOURCE_GROUP = { drums: "drum", bass: "bass", chords: "chord" };

function stripPresetExtension(name) {
  return String(name || "").replace(/\.(adg|adv)$/i, "");
}

function buildGenreInstrumentIndex() {
  const index = new Map();
  if (!fs.existsSync(SENSEI_IDENTITY_PATH)) return index;
  const identities = fs
    .readFileSync(SENSEI_IDENTITY_PATH, "utf8")
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line))
    .sort((a, b) => String(a.path).localeCompare(String(b.path)));
  for (const identity of identities) {
    const role = identity.role;
    const genres = identity.native_genres || [];
    const name = stripPresetExtension(identity.name);
    if (!role || !name || genres.length === 0) continue;
    // "Drum Loop"-tagged kits are frequently built as a plain Instrument
    // Rack around a sliced loop rather than a real multi-pad Drum Rack --
    // confirmed via a live Sensei batch run (2026-08-14, "Tomorrow Kit")
    // where the SDK could find no per-pad note evidence at all, so Sensei
    // can never generate for them. Skip them here so a genre with only
    // this kind of candidate falls back to the static table's known-working
    // kit instead of a genre-matched one that will always fail downstream.
    if (role === "drum" && (identity.native_drums || []).includes("Drum Loop")) continue;
    for (const genre of genres) {
      const key = `${role}:${genre}`;
      if (!index.has(key)) index.set(key, []);
      index.get(key).push(name);
    }
  }
  return index;
}

const buildPlanPath = path.resolve("engine/output/ableton_build_plan.json");
const outPath = path.resolve("engine/output/ableton_session_plan.json");

if (!fs.existsSync(buildPlanPath)) {
  console.error("Missing ableton_build_plan.json");
  process.exit(1);
}

const buildPlan = JSON.parse(fs.readFileSync(buildPlanPath, "utf8"));

const GROUP_NAMES = {
  drums: "DRUMS",
  bass: "BASS",
  chords: "MUSIC",
  melody: "MUSIC",
  vocal: "VOCALS",
  fx: "FX"
};

const groups = [
  { id: "drums", name: "DRUMS", type: "group_track", priority: 1 },
  { id: "bass", name: "BASS", type: "group_track", priority: 2 },
  { id: "music", name: "MUSIC", type: "group_track", priority: 3 },
  { id: "vocal", name: "VOCALS", type: "group_track", priority: 4 },
  { id: "fx", name: "FX", type: "group_track", priority: 5 }
];

function resolveAbletonGroup(group) {
  if (group === "chords" || group === "melody") return "music";
  return group;
}

function resolveAbletonGroupName(group) {
  return GROUP_NAMES[group] || group.toUpperCase();
}

function resolveInstrumentFamily(sourceGroup, trackId, genre, genreIndex) {
  const role = ROLE_BY_SOURCE_GROUP[sourceGroup];
  if (role && genre) {
    const matches = genreIndex.get(`${role}:${genre}`);
    if (matches && matches.length > 0) return matches[0];
  }

  // Every fallback below is an exact, Sensei-catalog-confirmed preset name,
  // not a generic guess -- a vague single-word term (e.g. "Pad", "Strings")
  // matches whatever the browser search tree happens to hit first, which is
  // rarely the intended device and is never guaranteed to be something
  // Sensei can actually generate for. Verified against
  // ableton_preset_genre_identities.jsonl 2026-08-14.
  if (sourceGroup === "drums") return "Boom Bap Kit"; // role=drum, Core Library, no "Drum Loop" tag

  if (trackId === "bass.main") return "Basic Analog Bass"; // role=bass, Core Library
  if (trackId === "bass.sub") return "Hip-Hop Sub Bass"; // role=bass, Core Library

  if (trackId === "chords.keys") return "Electric Piano Daze"; // role=chord, Beat Tools
  if (trackId === "chords.pad") return "5ths Detuned Pad"; // role=chord, Core Library
  if (trackId === "chords.strings") return "Glass High Strings Pad"; // role=chord, Core Library

  // Sensei has no live-wired role for melody/lead/counter/texture at all
  // (only bass/chord/drum) -- loading anything here would open a device
  // Sensei can never generate for, so these tracks are left without an
  // instrument_family and stay empty, same as vocal/fx tracks already do.
  if (trackId === "melody.lead") return null;
  if (trackId === "melody.counter") return null;
  if (trackId === "melody.texture") return null;

  return null;
}


function getTrackActivity(trackId, group, scene) {
  const lane = scene?.lanes?.[group];
  return lane?.activity ?? {};
}

function getActiveBarRange(activity, sections) {
  const active = sections.filter((section) => {
    const value = Number(activity[section.id]);
    return Number.isFinite(value) && value > 20;
  });

  if (!active.length) {
    return { start: 1, end: 80 };
  }

  return {
    start: active[0].start_bar,
    end: active[active.length - 1].end_bar
  };
}

// The >20 presence threshold above answers "does this lane play here at
// all". It throws away the rest of the number: a lane at 30 and a lane at
// 100 both come out fully on. Carrying the raw per-section value through
// lets the arrangement builder act on intensity as well as presence.
function getSectionActivity(activity, sections) {
  const out = {};
  for (const section of sections) {
    const value = Number(activity[section.id]);
    if (Number.isFinite(value)) out[section.id] = value;
  }
  return out;
}

function getMuteRegions(activity, sections) {
  return sections
    .filter((section) => {
      const value = Number(activity[section.id]);
      return Number.isFinite(value) && value <= 20;
    })
    .map((section) => ({
      start_bar: section.start_bar,
      end_bar: section.end_bar,
      reason: "Inactive in arrangement scene"
    }));
}

const sections = buildPlan.scene?.sections ?? [];
const genreIndex = buildGenreInstrumentIndex();
const projectGenre = buildPlan.project?.genre ?? null;

const tracks = buildPlan.tracks.map((track) => {
  const activity = getTrackActivity(track.track_id, track.group, buildPlan.scene);
  const range = getActiveBarRange(activity, sections);

  return {
    track_id: track.track_id,
    group: resolveAbletonGroup(track.group),
    source_group: track.group,
    name: track.track_id.split(".").slice(1).join(" "),
    display_name: track.writes_to ? track.writes_to.split(".").slice(-1)[0] : track.track_id,
    ableton_name: track.writes_to ? track.writes_to.split(".").slice(-1)[0] : track.track_id,
    short_name: track.track_id.split(".").slice(-1)[0].toUpperCase(),
    source: track.source,
    output_type: track.output_type,
    instrument_family: resolveInstrumentFamily(track.group, track.track_id, projectGenre, genreIndex),
    // Sensei generates for exactly three roles (see core/live_context_resolver.py's
    // _INSTRUMENT_ROLES plus drum). melody/vocal/fx have no role at all, and
    // saying so in the plan is what lets the arrangement builder report them
    // as deliberately out of scope instead of as a failure.
    sensei_role: ROLE_BY_SOURCE_GROUP[track.group] ?? null,
    ableton_group_name: resolveAbletonGroupName(track.group),
    writes_to: `Ableton.Track.${resolveAbletonGroupName(track.group)}.${track.writes_to ? track.writes_to.split(".").slice(-1)[0] : track.track_id}`,
    clip_start_bar: range.start,
    clip_end_bar: range.end,
    mute_regions: getMuteRegions(activity, sections),
    section_activity: getSectionActivity(activity, sections),
    device_chain: []
  };
});

function barToTime(bar, bpm, beatsPerBar = 4) {
  const seconds = Math.round((bar - 1) * (60 / bpm) * beatsPerBar);
  const minutes = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return `${minutes}:${rest}`;
}

const bpm = Number(buildPlan.project?.bpm) || 95;

const locators = buildPlan.scene.sections.map((section) => ({
  // section_activity is keyed by section id, so the id has to survive into
  // the locator list or the two can never be joined up again.
  id: section.id,
  name: section.name,
  start_bar: section.start_bar,
  end_bar: section.end_bar,
  start_time: barToTime(section.start_bar, bpm),
  end_time: barToTime(section.end_bar + 1, bpm),
  energy: section.energy
}));

const sessionPlan = {
  created_at: new Date().toISOString(),
  project: buildPlan.project,
  groups,
  tracks,
  locators,
  render_policy: buildPlan.render_policy ?? {},
  routing: {
    create_group_routing: true,
    route_children_to_parent_group: true
  }
};

fs.writeFileSync(outPath, JSON.stringify(sessionPlan, null, 2));

console.log("Ableton session plan created.");
console.log(`Saved: ${outPath}`);
console.log(`Groups: ${groups.length}`);
console.log(`Tracks: ${tracks.length}`);
console.log(`Locators: ${locators.length}`);
