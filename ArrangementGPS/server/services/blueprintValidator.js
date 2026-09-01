const TRACK_DEFAULTS = {
  Drums: {
    id: "track_drums",
    type: "midi",
    role: "Rhythm Foundation",
    target_generator: "drum_generator",
  },
  Bass: {
    id: "track_bass",
    type: "midi",
    role: "Low-End Support",
    target_generator: "bass_generator",
  },
  Chords: {
    id: "track_chords",
    type: "midi",
    role: "Harmonic Bed",
    target_generator: "chord_generator",
  },
  Melody: {
    id: "track_melody",
    type: "audio_or_midi",
    role: "Lead Identity",
    target_generator: "sample_generator",
  },
  Vocal: {
    id: "track_vocal",
    type: "guide",
    role: "Vocal Space",
    target_generator: "vocal_planner",
  },
  FX: {
    id: "track_fx",
    type: "audio",
    role: "Atmosphere & Transitions",
    target_generator: "fx_generator",
  },
};

const REQUIRED_TRACK_NAMES = ["Drums", "Bass", "Chords", "Melody", "Vocal", "FX"];

const JOB_GENERATORS = {
  Drums: "Drum Generator",
  Bass: "Bass Generator",
  Chords: "Chord Generator",
  Melody: "Sample Generator",
  Vocal: "Vocal Planner",
  FX: "FX Generator",
};

const DEFAULT_80_BAR_SECTIONS = [
  { id: "intro", name: "Intro", start_bar: 1, end_bar: 8, energy: 25, density: "Low" },
  { id: "verse_1", name: "Verse 1", start_bar: 9, end_bar: 24, energy: 55, density: "Medium" },
  { id: "hook", name: "Hook", start_bar: 25, end_bar: 32, energy: 82, density: "High" },
  { id: "verse_2", name: "Verse 2", start_bar: 33, end_bar: 48, energy: 62, density: "Medium" },
  { id: "bridge", name: "Bridge", start_bar: 49, end_bar: 56, energy: 42, density: "Low" },
  { id: "final_hook", name: "Final Hook", start_bar: 57, end_bar: 72, energy: 92, density: "High" },
  { id: "outro", name: "Outro", start_bar: 73, end_bar: 80, energy: 28, density: "Low" },
];

const DEFAULT_EVENT_INTENTS = [
  { track: "drums", section: "verse_1", bar: 9, type: "groove_entry", label: "Groove Entry" },
  { track: "drums", section: "hook", bar: 25, type: "hook_lift", label: "Hook Lift" },
  { track: "drums", section: "bridge", bar: 49, type: "bridge_reset", label: "Bridge Reset" },
  { track: "drums", section: "final_hook", bar: 57, type: "final_push", label: "Final Hook Push" },
  { track: "bass", section: "verse_1", bar: 9, type: "bass_entry", label: "Bass Entry" },
  { track: "bass", section: "hook", bar: 25, type: "hook_weight", label: "Hook Weight" },
  { track: "bass", section: "bridge", bar: 49, type: "bridge_pullback", label: "Bridge Pullback" },
  { track: "bass", section: "final_hook", bar: 57, type: "final_return", label: "Final Return" },
  { track: "chords", section: "intro", bar: 1, type: "harmonic_entry", label: "Harmonic Entry" },
  { track: "chords", section: "hook", bar: 25, type: "hook_expansion", label: "Hook Expansion" },
  { track: "chords", section: "bridge", bar: 49, type: "texture_shift", label: "Texture Shift" },
  { track: "chords", section: "final_hook", bar: 57, type: "final_layer", label: "Final Layer" },
  { track: "melody", section: "intro", bar: 1, type: "motif_entry", label: "Motif Entry" },
  { track: "melody", section: "hook", bar: 25, type: "hook_lead", label: "Hook Lead" },
  { track: "melody", section: "bridge", bar: 49, type: "bridge_variation", label: "Bridge Variation" },
  { track: "melody", section: "outro", bar: 73, type: "outro_motif", label: "Outro Motif" },
  { track: "vocal", section: "verse_1", bar: 9, type: "verse_space", label: "Verse Space" },
  { track: "vocal", section: "hook", bar: 25, type: "hook_presence", label: "Hook Presence" },
  { track: "vocal", section: "bridge", bar: 49, type: "bridge_silence", label: "Bridge Silence" },
  { track: "vocal", section: "final_hook", bar: 57, type: "final_focus", label: "Final Hook Focus" },
  { track: "fx", section: "intro", bar: 1, type: "atmosphere_entry", label: "Atmosphere Entry" },
  { track: "fx", section: "hook", bar: 25, type: "transition_lift", label: "Transition Lift" },
  { track: "fx", section: "bridge", bar: 49, type: "bridge_space", label: "Bridge Space" },
  { track: "fx", section: "final_hook", bar: 57, type: "final_impact", label: "Final Impact" },
];

export function parseJsonResponse(text) {
  const trimmed = String(text ?? "").trim();
  const withoutFence = trimmed
    .replace(/^```(?:json)?/i, "")
    .replace(/```$/i, "")
    .trim();

  return JSON.parse(withoutFence);
}

function slugify(value) {
  return String(value ?? "arrangement")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "") || "arrangement";
}

function normalizeNumber(value, fallback) {
  const nextValue = Number(value);

  return Number.isFinite(nextValue) ? nextValue : fallback;
}

function normalizeSection(section, index) {
  const startBar = normalizeNumber(section?.start_bar, index * 8 + 1);
  const endBar = normalizeNumber(section?.end_bar, startBar + 7);
  const name = section?.name || `Section ${index + 1}`;

  return {
    id: section?.id || slugify(name),
    name,
    start_bar: startBar,
    end_bar: Math.max(endBar, startBar),
    energy: normalizeNumber(section?.energy, 50),
    density: section?.density || "Medium",
    intent: section?.intent || "Support the arrangement flow",
    transition_in: section?.transition_in || "Clean entry",
    transition_out: section?.transition_out || "Prepared transition",
  };
}

function normalizeSectionsForTotalBars(blueprint, totalBars) {
  if (totalBars === 80) {
    const sourceSections = Array.isArray(blueprint.arrangement?.sections)
      ? blueprint.arrangement.sections
      : [];

    return DEFAULT_80_BAR_SECTIONS.map((defaultSection) => {
      const sourceSection = sourceSections.find(
        (section) =>
          section?.id === defaultSection.id ||
          String(section?.name ?? "").toLowerCase() === defaultSection.name.toLowerCase()
      );

      return {
        ...defaultSection,
        energy: normalizeNumber(sourceSection?.energy, defaultSection.energy),
        density: sourceSection?.density || defaultSection.density,
        intent: sourceSection?.intent || "Support the arrangement flow",
        transition_in: sourceSection?.transition_in || "Clean entry",
        transition_out: sourceSection?.transition_out || "Prepared transition",
      };
    });
  }

  return blueprint.arrangement.sections.map(normalizeSection);
}

function normalizeTrack(track, sections) {
  const trackName = track?.name || "Track";
  const defaults = TRACK_DEFAULTS[trackName] ?? {};
  const sectionIds = sections.map((section) => section.id);
  const activeSections = Array.isArray(track?.active_sections)
    ? track.active_sections.filter((sectionId) => sectionIds.includes(sectionId))
    : sectionIds;
  const mutedSections = Array.isArray(track?.muted_sections)
    ? track.muted_sections.filter((sectionId) => sectionIds.includes(sectionId))
    : sectionIds.filter((sectionId) => !activeSections.includes(sectionId));

  return {
    id: track?.id || defaults.id || `track_${slugify(trackName)}`,
    name: trackName,
    type: track?.type || defaults.type || "audio_or_midi",
    role: track?.role || defaults.role || "Production Element",
    target_generator: track?.target_generator || defaults.target_generator || "sample_generator",
    active_sections: activeSections,
    muted_sections: mutedSections,
    density_profile: track?.density_profile || "Medium",
    energy_profile: sectionIds.reduce((profile, sectionId) => {
      profile[sectionId] = normalizeNumber(track?.energy_profile?.[sectionId], 50);
      return profile;
    }, {}),
    groove_profile: track?.groove_profile || "Medium",
    variation_plan: track?.variation_plan || "Progressive",
    transition_plan: track?.transition_plan || "Section-aware transitions",
    automation_intent: track?.automation_intent || "Subtle movement",
    sound_source_intent: track?.sound_source_intent || "Producer-selected sound",
    routing_intent: track?.routing_intent || "Clean session signal flow",
    planning_metadata:
      track?.planning_metadata && typeof track.planning_metadata === "object"
        ? track.planning_metadata
        : {},
    generator_payload:
      track?.generator_payload && typeof track.generator_payload === "object"
        ? track.generator_payload
        : {
            job_intent: "Prepare high-level production part",
            bar_scope: "Full arrangement",
            constraints: [],
          },
  };
}

function normalizeJob(job, sections) {
  const sectionIds = sections.map((section) => section.id);

  return {
    id: job?.id || `job_${slugify(job?.generator || job?.target_track || "generator")}`,
    generator: job?.generator || "Sample Generator",
    target_track: job?.target_track || "",
    target_sections: Array.isArray(job?.target_sections)
      ? job.target_sections.filter((sectionId) => sectionIds.includes(sectionId))
      : sectionIds,
    priority: job?.priority || "Medium",
    status: job?.status || "Waiting",
    payload: {
      job_type: job?.payload?.job_type || "Prepare production direction",
      bar_range: job?.payload?.bar_range || "Full arrangement",
      source_track_payload: job?.payload?.source_track_payload || "",
    },
  };
}

function createDefaultJob(track, sections) {
  const sectionIds = sections.map((section) => section.id);
  const startBar = sections[0]?.start_bar ?? 1;
  const endBar = sections.at(-1)?.end_bar ?? startBar;

  return normalizeJob(
    {
      id: `job_${slugify(track.name)}`,
      generator: JOB_GENERATORS[track.name] || "Sample Generator",
      target_track: track.id,
      target_sections: track.active_sections?.length ? track.active_sections : sectionIds,
      priority: "Medium",
      status: "Waiting",
      payload: {
        job_type: "Prepare production direction",
        bar_range: `${startBar}-${endBar}`,
        source_track_payload: track.generator_payload?.job_intent || "",
      },
    },
    sections
  );
}

function assertBlueprintShape(blueprint) {
  if (!blueprint || typeof blueprint !== "object") {
    throw new Error("Ollama returned a non-object blueprint.");
  }

  if (!Array.isArray(blueprint.arrangement?.sections) || blueprint.arrangement.sections.length === 0) {
    throw new Error("Blueprint is missing arrangement sections.");
  }

  if (!Array.isArray(blueprint.tracks) || blueprint.tracks.length === 0) {
    throw new Error("Blueprint is missing tracks.");
  }

  return blueprint;
}

function normalizeEventIntent(event, sections) {
  const sectionId = event?.section || event?.section_id;
  const section = sections.find((nextSection) => nextSection.id === sectionId);

  if (!event?.track || !section) return null;

  const bar = normalizeNumber(event.bar, section.start_bar);

  return {
    track: String(event.track).toLowerCase(),
    section: section.id,
    bar: Math.min(Math.max(bar, section.start_bar), section.end_bar),
    type: event.type || "arrangement_event",
    label: event.label || "Arrangement Event",
  };
}

function normalizeEventIntents(blueprint, sections) {
  const sourceEvents = Array.isArray(blueprint.event_intents) ? blueprint.event_intents : [];
  const normalizedEvents = sourceEvents
    .map((event) => normalizeEventIntent(event, sections))
    .filter(Boolean);
  const limitedEvents = [];
  const countsByTrack = new Map();

  for (const event of normalizedEvents.length > 0 ? normalizedEvents : DEFAULT_EVENT_INTENTS) {
    const count = countsByTrack.get(event.track) ?? 0;

    if (count >= 4) continue;

    countsByTrack.set(event.track, count + 1);
    limitedEvents.push(event);
  }

  return limitedEvents;
}

export function normalizeBlueprint(rawBlueprint) {
  const blueprint = assertBlueprintShape(rawBlueprint);
  const projectName = blueprint.project?.name || "Untitled Arrangement";
  const totalBars = normalizeNumber(blueprint.project?.total_bars, 80);
  const sections = normalizeSectionsForTotalBars(blueprint, totalBars);
  const normalizedEventIntents = normalizeEventIntents(blueprint, sections);
  const normalizedTracks = REQUIRED_TRACK_NAMES.map((trackName) => {
    const sourceTrack = blueprint.tracks.find(
      (track) => String(track?.name ?? "").toLowerCase() === trackName.toLowerCase()
    );

    return normalizeTrack(sourceTrack ?? { name: trackName }, sections);
  });
  const normalizedJobs = Array.isArray(blueprint.generator_jobs)
    ? blueprint.generator_jobs.map((job) => normalizeJob(job, sections))
    : [];
  const jobsWithFallbacks = normalizedTracks.map((track) => {
    const existingJob = normalizedJobs.find((job) => job.target_track === track.id);

    return existingJob ?? createDefaultJob(track, sections);
  });
  return {
    project: {
      id: blueprint.project?.id || slugify(projectName),
      name: projectName,
      bpm: normalizeNumber(blueprint.project?.bpm, 95),
      key: blueprint.project?.key || "D",
      scale: blueprint.project?.scale || "Minor",
      time_signature: blueprint.project?.time_signature || "4/4",
      total_bars: totalBars,
      groove_profile: blueprint.project?.groove_profile || "Medium Swing",
      swing_amount: normalizeNumber(blueprint.project?.swing_amount, 55),
      mood: blueprint.project?.mood || "Focused",
      genre_tags: Array.isArray(blueprint.project?.genre_tags) ? blueprint.project.genre_tags : [],
    },
    daw: {
      target_daw: "Ableton Live",
      sample_rate: normalizeNumber(blueprint.daw?.sample_rate, 48000),
      bit_depth: normalizeNumber(blueprint.daw?.bit_depth, 24),
      global_quantization: blueprint.daw?.global_quantization || "1 Bar",
      launch_mode: blueprint.daw?.launch_mode || "Arrangement View",
      locator_markers: Array.isArray(blueprint.daw?.locator_markers)
        ? blueprint.daw.locator_markers
        : sections.map((section) => ({
            section_id: section.id,
            name: section.name,
            bar: section.start_bar,
          })),
      tempo_automation_allowed: Boolean(blueprint.daw?.tempo_automation_allowed),
      return_tracks: Array.isArray(blueprint.daw?.return_tracks) ? blueprint.daw.return_tracks : [],
      master_chain_intent: blueprint.daw?.master_chain_intent || "Balanced producer preview",
    },
    arrangement: {
      sections,
    },
    event_intents: normalizedEventIntents,
    tracks: normalizedTracks,
    generator_jobs: jobsWithFallbacks,
    export_targets: {
      ableton_builder: { enabled: true, payload_roots: ["project", "daw", "arrangement", "tracks"] },
      drum_generator: { enabled: false, payload_roots: [] },
      bass_generator: { enabled: false, payload_roots: [] },
      chord_generator: { enabled: false, payload_roots: [] },
      fx_generator: { enabled: false, payload_roots: [] },
      vocal_planner: { enabled: false, payload_roots: [] },
      ...(blueprint.export_targets ?? {}),
    },
    production_log: Array.isArray(blueprint.production_log) && blueprint.production_log.length > 0
      ? blueprint.production_log
      : [
          { type: "complete", message: "Prompt received" },
          { type: "complete", message: "Blueprint generated" },
          { type: "pending", message: "Waiting for local generators" },
        ],
  };
}
