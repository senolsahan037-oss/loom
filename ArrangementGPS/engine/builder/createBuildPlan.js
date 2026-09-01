import fs from "fs";
import path from "path";

const statusPath = path.resolve("engine/output/status.json");
const blueprintPath = path.resolve("engine/output/normalized_blueprint.json");
const outPath = path.resolve("engine/output/ableton_build_plan.json");

if (!fs.existsSync(blueprintPath)) {
  console.error("Missing normalized_blueprint.json. Run the engine first.");
  process.exit(1);
}

const blueprint = JSON.parse(fs.readFileSync(blueprintPath, "utf8"));
const status = fs.existsSync(statusPath) ? JSON.parse(fs.readFileSync(statusPath, "utf8")) : { jobs: [] };

// The old gate required every agent job to report "complete" before a build
// plan could exist. That made sense when ArrangementGPS was expected to
// generate the musical content itself, but it no longer does: the only job
// runner in the repo (engine/runner/runNext.js) writes `placeholder: true`
// stubs, and the real MIDI is written by Sensei inside Live, after the
// tracks exist. So the gate was blocking the entire chain on a step that
// could only ever be satisfied by fake files.
//
// What must actually be true before Ableton can be built is that the PLAN is
// complete -- sections to place locators at, and a track list to create. Both
// come from the blueprint, so that is what is checked here.
const missing = [];
if (!blueprint.project?.name) missing.push("project.name");
if (!Array.isArray(blueprint.scene?.sections) || blueprint.scene.sections.length === 0) missing.push("scene.sections");
if (!Array.isArray(blueprint.production_tree?.tracks) || blueprint.production_tree.tracks.length === 0) missing.push("production_tree.tracks");
if (!Array.isArray(blueprint.agent_dispatch) || blueprint.agent_dispatch.length === 0) missing.push("agent_dispatch");

if (missing.length > 0) {
  console.error(`Blueprint is incomplete, cannot build: ${missing.join(", ")}`);
  process.exit(1);
}

// Completed jobs are no longer required, but if one exists its output path is
// still worth carrying so a future real generator can be traced.
const completedByTrack = new Map(
  (status.jobs ?? [])
    .filter((job) => job.status === "complete")
    .map((job) => [job.target_track, job])
);

const tracks = blueprint.agent_dispatch.map((job) => {
  const completed = completedByTrack.get(job.target_track);
  return {
    track_id: job.target_track,
    group: job.target_group,
    agent: job.agent,
    source: completed?.output_path ?? job.expected_output?.path ?? null,
    output_type: job.expected_output?.type ?? "midi_clip",
    writes_to: job.writes_to ?? null,
    content_status: completed ? "generated" : "pending_sensei"
  };
});

const buildPlan = {
  created_at: new Date().toISOString(),
  status: "ready_for_ableton",
  project: blueprint.project ?? {},
  scene: blueprint.scene ?? {},
  production_tree: blueprint.production_tree ?? {},
  sound_intent: blueprint.sound_intent ?? {},
  render_policy: blueprint.render_policy ?? {},
  tracks,
  ableton: {
    create_groups: true,
    create_tracks: true,
    create_locators: true,
    place_outputs: true,
    save_project: true
  }
};

fs.writeFileSync(outPath, JSON.stringify(buildPlan, null, 2));

const pending = tracks.filter((track) => track.content_status === "pending_sensei").length;
console.log("Ableton build plan created.");
console.log(`Saved: ${outPath}`);
console.log(`Tracks: ${tracks.length}`);
console.log(`Awaiting MIDI from Sensei in Live: ${pending}`);
