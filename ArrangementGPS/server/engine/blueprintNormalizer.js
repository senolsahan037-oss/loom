const DEFAULT_SECTIONS = [
  { id: "intro", name: "Intro", start_bar: 1, end_bar: 8, energy: 20, density: "Low" },
  { id: "verse_1", name: "Verse 1", start_bar: 9, end_bar: 24, energy: 50, density: "Medium" },
  { id: "hook", name: "Hook", start_bar: 25, end_bar: 32, energy: 78, density: "High" },
  { id: "verse_2", name: "Verse 2", start_bar: 33, end_bar: 48, energy: 55, density: "Medium" },
  { id: "bridge", name: "Bridge", start_bar: 49, end_bar: 56, energy: 35, density: "Low" },
  { id: "final_hook", name: "Final Hook", start_bar: 57, end_bar: 72, energy: 92, density: "Very High" },
  { id: "outro", name: "Outro", start_bar: 73, end_bar: 80, energy: 18, density: "Low" }
];

const TRACKS = ["drums", "bass", "chords", "melody", "vocal", "fx"];
const TRACK_NAMES = {
  drums: "Drums",
  bass: "Bass",
  chords: "Chords",
  melody: "Melody",
  vocal: "Vocal",
  fx: "FX"
};
const TRACK_TYPES = {
  drums: "midi",
  bass: "midi",
  chords: "midi",
  melody: "audio_or_midi",
  vocal: "guide",
  fx: "audio"
};
const TRACK_ROLES = {
  drums: "Rhythm Foundation",
  bass: "Low-End Support",
  chords: "Harmonic Bed",
  melody: "Lead Identity",
  vocal: "Vocal Space",
  fx: "Atmosphere & Transitions"
};
const JOB_NAMES = {
  drums: "Drum Generator",
  bass: "Bass Generator",
  chords: "Chord Generator",
  melody: "Sample Generator",
  vocal: "Vocal Planner",
  fx: "FX Generator"
};
const JOB_IDS = {
  drums: "drum_generator",
  bass: "bass_generator",
  chords: "chord_generator",
  melody: "sample_generator",
  vocal: "vocal_planner",
  fx: "fx_generator"
};

const DEFAULT_SOUNDS = {
  drums: "Dusty Boom Bap Kit",
  bass: "Warm Analog Bass",
  chords: "Dusty Rhodes",
  melody: "Arabesk Lead",
  vocal: "Wide Hook Space",
  fx: "Tape Space"
};

const DEFAULT_INTENTS = {
  drums: "Controlled verses, stronger hooks, reduced bridge/outro.",
  bass: "Warm low-end support with stronger hook presence.",
  chords: "Dusty harmonic bed supporting emotional movement.",
  melody: "Sparse lead identity with hook focus.",
  vocal: "Wide vocal space with verse and hook emphasis.",
  fx: "Atmosphere and transitions supporting section changes."
};

const DEFAULT_ACTIVITY = {
  drums: { intro: 20, verse_1: 65, hook: 90, verse_2: 70, bridge: 30, final_hook: 95, outro: 15 },
  bass: { intro: 0, verse_1: 45, hook: 80, verse_2: 55, bridge: 15, final_hook: 85, outro: 10 },
  chords: { intro: 35, verse_1: 50, hook: 75, verse_2: 55, bridge: 60, final_hook: 85, outro: 30 },
  melody: { intro: 20, verse_1: 35, hook: 75, verse_2: 45, bridge: 50, final_hook: 80, outro: 25 },
  vocal: { intro: 0, verse_1: 70, hook: 85, verse_2: 75, bridge: 25, final_hook: 95, outro: 10 },
  fx: { intro: 45, verse_1: 25, hook: 60, verse_2: 30, bridge: 70, final_hook: 80, outro: 55 }
};

function getBpm(project, prompt) {
  const fromProject = Number(project?.bpm);
  if (fromProject > 40 && fromProject < 220) return fromProject;

  const match = String(prompt).match(/(\d{2,3})\s*bpm/i);
  if (match) return Number(match[1]);

  return 95;
}

function slugify(value) {
  return String(value ?? "arrangement")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "") || "arrangement";
}

function sanitizeMusicalText(value, fallback = "") {
  const text = String(value ?? "").trim() || fallback;

  return text
    .replace(/\b[\w.-]+\.(?:vst3?|clap|component|au|adv|adg|fxp|fxb|vital|h2p|srgpreset|talpreset)\b/gi, "producer-selected sound")
    .replace(/\b(?:preset|plugin|library|bank)\s*[:#-]?\s*[\w .-]+/gi, "producer-selected tone")
    .replace(/\b(?:Vital|Surge XT|ZebraHZ|Zebra2|Dexed|TAL-[\w-]+|u-he)\b/gi, "expressive synth")
    .replace(/\s+/g, " ")
    .trim();
}

function getGenreTags(project) {
  if (Array.isArray(project?.genre_tags) && project.genre_tags.length > 0) {
    return project.genre_tags.map((tag) => sanitizeMusicalText(tag)).filter(Boolean);
  }

  return [project?.genre, project?.subgenre].map((tag) => sanitizeMusicalText(tag)).filter(Boolean);
}

function normalizeProject(adapted) {
  const p = adapted.project ?? {};
  const name = sanitizeMusicalText(p.name, "Generated Arrangement");
  const genreTags = getGenreTags(p);

  return {
    id: p.id || slugify(name),
    name,
    genre: sanitizeMusicalText(p.genre, genreTags[0] || "Producer Brief"),
    subgenre: sanitizeMusicalText(p.subgenre, genreTags[1] || "Arrangement"),
    bpm: getBpm(p, adapted.user_prompt),
    key: sanitizeMusicalText(p.key, "D"),
    scale: p.scale || (String(p.key ?? "").toLowerCase().includes("major") ? "Major" : "Minor"),
    time_signature: p.time_signature || "4/4",
    groove_profile: sanitizeMusicalText(p.groove_profile, "Human Groove"),
    swing_amount: Number.isFinite(Number(p.swing_amount)) ? Number(p.swing_amount) : 55,
    mood: sanitizeMusicalText(p.mood, "Focused, musical, dynamic"),
    genre_tags: genreTags,
    total_bars: 80
  };
}


function clampActivity(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function hasUsefulActivity(activity) {
  const values = Object.values(activity ?? {}).map(Number).filter(Number.isFinite);
  if (values.length < 4) return false;
  const unique = new Set(values.map((v) => Math.round(v / 10) * 10));
  return unique.size >= 3;
}

function promptHints(adapted) {
  const text = [
    adapted.user_prompt,
    JSON.stringify(adapted.project ?? {}),
    JSON.stringify(adapted.sound_recommendations ?? {}),
    JSON.stringify(adapted.track_intents ?? {})
  ].join(" ").toLowerCase();

  return {
    dre: text.includes("dre") || text.includes("g-funk") || text.includes("west coast"),
    trap: text.includes("trap"),
    boomBap: text.includes("boom") || text.includes("bap"),
    arabesk: text.includes("arabesk"),
    cinematic: text.includes("cinematic") || text.includes("film")
  };
}

function buildTrackActivity(track, incomingActivity, adapted) {
  const hints = promptHints(adapted);
  const base = { ...DEFAULT_ACTIVITY[track] };

  // Genre/style shaping. LLM gives direction; this turns it into a usable scene.
  if (hints.dre) {
    if (track === "drums") return { intro: 25, verse_1: 70, hook: 88, verse_2: 72, bridge: 45, final_hook: 95, outro: 20 };
    if (track === "bass") return { intro: 10, verse_1: 78, hook: 92, verse_2: 80, bridge: 35, final_hook: 96, outro: 35 };
    if (track === "chords") return { intro: 35, verse_1: 62, hook: 80, verse_2: 62, bridge: 58, final_hook: 84, outro: 40 };
    if (track === "melody") return { intro: 28, verse_1: 38, hook: 82, verse_2: 45, bridge: 55, final_hook: 88, outro: 30 };
    if (track === "vocal") return { intro: 0, verse_1: 78, hook: 80, verse_2: 82, bridge: 25, final_hook: 90, outro: 15 };
    if (track === "fx") return { intro: 45, verse_1: 25, hook: 48, verse_2: 30, bridge: 70, final_hook: 82, outro: 60 };
  }

  if (hints.trap) {
    if (track === "drums") return { intro: 20, verse_1: 72, hook: 95, verse_2: 75, bridge: 35, final_hook: 98, outro: 20 };
    if (track === "bass") return { intro: 0, verse_1: 70, hook: 95, verse_2: 75, bridge: 25, final_hook: 98, outro: 10 };
  }

  // Use LLM activity only if it is musically varied enough.
  if (hasUsefulActivity(incomingActivity)) {
    return {
      intro: clampActivity(incomingActivity.intro ?? base.intro),
      verse_1: clampActivity(incomingActivity.verse_1 ?? base.verse_1),
      hook: clampActivity(incomingActivity.hook ?? base.hook),
      verse_2: clampActivity(incomingActivity.verse_2 ?? base.verse_2),
      bridge: clampActivity(incomingActivity.bridge ?? base.bridge),
      final_hook: clampActivity(incomingActivity.final_hook ?? base.final_hook),
      outro: clampActivity(incomingActivity.outro ?? base.outro)
    };
  }

  return base;
}


function normalizeScene(adapted) {
  const llmSections = adapted.scene?.sections;
  const sections = Array.isArray(llmSections) && llmSections.length >= 3
    ? DEFAULT_SECTIONS.map((s) => {
        const found = llmSections.find((x) => x.id === s.id || x.name === s.name);
        return {
          ...s,
          energy: Number(found?.energy) || s.energy,
          density: found?.density || s.density
        };
      })
    : DEFAULT_SECTIONS;

  const lanes = {};

  for (const track of TRACKS) {
    const incoming = adapted.scene?.lanes?.[track] ?? {};
    const incomingActivity = incoming.activity ?? {};
    const activity = buildTrackActivity(track, incomingActivity, adapted);

    const active_sections = Object.entries(activity)
      .filter(([, v]) => Number(v) > 0)
      .map(([k]) => k);

    const muted_sections = Object.entries(activity)
      .filter(([, v]) => Number(v) <= 0)
      .map(([k]) => k);

    lanes[track] = {
      active_sections,
      muted_sections,
      activity
    };
  }

  return {
    sections,
    lanes,
    event_markers: normalizeEvents(adapted)
  };
}

function normalizeTrackName(value) {
  const normalized = String(value ?? "").toLowerCase().replace(/^track_/, "");

  return TRACKS.includes(normalized) ? normalized : "";
}

function normalizeEvents(adapted) {
  const incoming = Array.isArray(adapted.event_intents) ? adapted.event_intents : [];

  const fallback = [
    { track: "drums", section: "hook", bar: 25, type: "hook_lift", label: "Hook Lift" },
    { track: "drums", section: "final_hook", bar: 57, type: "final_push", label: "Final Push" },
    { track: "bass", section: "hook", bar: 25, type: "hook_weight", label: "Hook Weight" },
    { track: "chords", section: "bridge", bar: 49, type: "texture_shift", label: "Texture Shift" },
    { track: "melody", section: "hook", bar: 25, type: "lead_focus", label: "Lead Focus" },
    { track: "vocal", section: "final_hook", bar: 57, type: "final_focus", label: "Final Hook Focus" },
    { track: "fx", section: "bridge", bar: 49, type: "bridge_space", label: "Bridge Space" },
    { track: "fx", section: "final_hook", bar: 57, type: "final_impact", label: "Final Impact" }
  ];

  const merged = [...incoming, ...fallback];

  return merged
    .map((e) => ({ ...e, track: normalizeTrackName(e?.track) }))
    .filter((e) => e && e.track && TRACKS.includes(e.track))
    .map((e) => ({
      track: e.track,
      section: e.section || "hook",
      bar: Number(e.bar) || 25,
      type: sanitizeMusicalText(e.type, "event"),
      label: sanitizeMusicalText(e.label, "Arrangement Event"),
      reason: sanitizeMusicalText(e.reason, "Supports the arrangement movement")
    }))
    .slice(0, 18);
}

function getActiveSections(activity) {
  return Object.entries(activity)
    .filter(([, value]) => Number(value) > 0)
    .map(([sectionId]) => sectionId);
}

function getMutedSections(activity) {
  return Object.entries(activity)
    .filter(([, value]) => Number(value) <= 0)
    .map(([sectionId]) => sectionId);
}

function getEnergyProfile(activity) {
  return Object.fromEntries(
    Object.entries(activity).map(([sectionId, value]) => [sectionId, clampActivity(value)])
  );
}

function getDensityProfile(activity) {
  const values = Object.values(activity).map(Number);
  const peak = Math.max(...values);

  if (peak >= 85) return "Verse: Medium / Hook: Very High";
  if (peak >= 70) return "Verse: Medium / Hook: High";
  return "Sparse / Supportive";
}

function getBarRange(sections, sectionIds) {
  const selected = sections.filter((section) => sectionIds.includes(section.id));
  const first = selected[0] ?? sections[0];
  const last = selected.at(-1) ?? sections.at(-1) ?? first;

  return `${first?.start_bar ?? 1}-${last?.end_bar ?? 80}`;
}

function createTrack(track, scene, sound, intent) {
  const activity = scene.lanes[track].activity;
  const activeSections = getActiveSections(activity);

  return {
    id: `track_${track}`,
    name: TRACK_NAMES[track],
    type: TRACK_TYPES[track],
    role: TRACK_ROLES[track],
    target_generator: JOB_IDS[track],
    active_sections: activeSections,
    muted_sections: getMutedSections(activity),
    density_profile: getDensityProfile(activity),
    energy_profile: getEnergyProfile(activity),
    groove_profile: track === "drums" ? "Humanized groove" : "Section-aware movement",
    variation_plan: "Build contrast between verse, hook, bridge, and final hook.",
    transition_plan: "Support the important section changes with musical lifts and resets.",
    automation_intent: sanitizeMusicalText(intent, DEFAULT_INTENTS[track]),
    sound_source_intent: sanitizeMusicalText(sound, DEFAULT_SOUNDS[track]),
    routing_intent: "Clean production routing with space for later generator output.",
    planning_metadata: {},
    generator_payload: {
      job_intent: sanitizeMusicalText(intent, DEFAULT_INTENTS[track]),
      bar_scope: getBarRange(scene.sections, activeSections),
      constraints: ["High-level production guidance only", "No MIDI notes or exact patterns in blueprint"]
    }
  };
}

function createJob(track, scene, sound, intent) {
  const activity = scene.lanes[track].activity;
  const activeSections = getActiveSections(activity);

  return {
    id: `job_${track}`,
    generator: JOB_NAMES[track],
    target_track: `track_${track}`,
    target_sections: activeSections.length > 0 ? activeSections : scene.sections.map((section) => section.id),
    priority: ["drums", "bass"].includes(track) ? "High" : "Medium",
    status: "Waiting",
    payload: {
      job_type: `Prepare ${TRACK_NAMES[track].toLowerCase()} production direction`,
      bar_range: getBarRange(scene.sections, activeSections),
      source_track_payload: sanitizeMusicalText(intent, DEFAULT_INTENTS[track]),
      intent: sanitizeMusicalText(intent, DEFAULT_INTENTS[track]),
      sound: sanitizeMusicalText(sound, DEFAULT_SOUNDS[track])
    }
  };
}

export function normalizeAiBlueprint(adapted) {
  const project = normalizeProject(adapted);
  const scene = normalizeScene(adapted);

  const sound_recommendations = {};
  const track_intents = {};

  for (const track of TRACKS) {
    sound_recommendations[track] =
      sanitizeMusicalText(adapted.sound_recommendations?.[track], DEFAULT_SOUNDS[track]);

    track_intents[track] =
      sanitizeMusicalText(adapted.track_intents?.[track], DEFAULT_INTENTS[track]);
  }

  const tracks = TRACKS.map((track) => createTrack(track, scene, sound_recommendations[track], track_intents[track]));
  const generator_jobs = TRACKS.map((track) => createJob(track, scene, sound_recommendations[track], track_intents[track]));

  return {
    project,
    daw: {
      target_daw: "Ableton Live",
      sample_rate: 48000,
      bit_depth: 24,
      global_quantization: "1 Bar",
      launch_mode: "Arrangement View",
      locator_markers: scene.sections.map((section) => ({
        section_id: section.id,
        name: section.name,
        bar: section.start_bar
      })),
      tempo_automation_allowed: false,
      return_tracks: [],
      master_chain_intent: "Balanced producer preview"
    },
    arrangement: {
      sections: scene.sections
    },
    scene,
    sound_recommendations,
    track_intents,
    event_intents: scene.event_markers,
    tracks,
    generator_jobs,
    export_targets: {
      ableton_builder: { enabled: true, payload_roots: ["project", "daw", "arrangement", "tracks"] },
      drum_generator: { enabled: false, payload_roots: [] },
      bass_generator: { enabled: false, payload_roots: [] },
      chord_generator: { enabled: false, payload_roots: [] },
      fx_generator: { enabled: false, payload_roots: [] },
      vocal_planner: { enabled: false, payload_roots: [] }
    },
    production_log: [
      { type: "complete", message: "Prompt received" },
      { type: "complete", message: "Creative brief adapted" },
      { type: "complete", message: "Blueprint normalized" },
      { type: "pending", message: "Waiting for local generators" }
    ],
    source: adapted.source || "llm_normalized"
  };
}
