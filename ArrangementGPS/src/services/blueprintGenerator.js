export const initialBlueprint = {
  project: {
    id: "pending_project",
    name: "Untitled Arrangement",
    bpm: 0,
    key: "Pending",
    scale: "Pending",
    time_signature: "4/4",
    total_bars: 0,
    groove_profile: "Pending",
    swing_amount: 0,
    mood: "Pending",
    genre_tags: [],
  },
  daw: {
    target_daw: "Ableton Live",
    sample_rate: 48000,
    bit_depth: 24,
    global_quantization: "1 Bar",
    launch_mode: "Arrangement View",
    locator_markers: [],
    tempo_automation_allowed: false,
    return_tracks: [],
    master_chain_intent: "Pending",
  },
  arrangement: {
    sections: [],
  },
  scene: {
    sections: [],
    lanes: {
      drums: { activity: {} },
      bass: { activity: {} },
      chords: { activity: {} },
      melody: { activity: {} },
      vocal: { activity: {} },
      fx: { activity: {} },
    },
    event_markers: [],
  },
  sound_recommendations: {},
  track_intents: {},
  event_intents: [],
  tracks: [],
  generator_jobs: [],
  export_targets: {
    ableton_builder: { enabled: true, payload_roots: ["project", "daw", "arrangement", "tracks"] },
    drum_generator: { enabled: false, payload_roots: [] },
    bass_generator: { enabled: false, payload_roots: [] },
    chord_generator: { enabled: false, payload_roots: [] },
    fx_generator: { enabled: false, payload_roots: [] },
    vocal_planner: { enabled: false, payload_roots: [] },
  },
  production_log: [{ type: "pending", message: "Waiting for arrangement prompt" }],
  developer_metadata: {
    selected_library: {
      drums: {},
      bass: {},
      chords: {},
      melody: {},
      fx: {},
    },
  },
};

export async function generateBlueprintFromPrompt(prompt) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 130000);
  const response = await fetch("http://localhost:3001/api/generate-blueprint", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    signal: controller.signal,
    body: JSON.stringify({ prompt: prompt.trim() }),
  }).catch((error) => {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("Blueprint generation timed out.");
    }

    throw error;
  }).finally(() => {
    clearTimeout(timeout);
  });

  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(payload.error || `Blueprint generation failed with status ${response.status}.`);
  }

  if (!payload.success || !payload.blueprint) {
    throw new Error(payload.error || "Backend returned an invalid blueprint response.");
  }

  return payload.blueprint;
}
