import fs from "fs";
import path from "path";

const sessionPlanPath = path.resolve("engine/output/ableton_session_plan.json");
const outPath = path.resolve("engine/output/ableton_build_manifest.json");

if (!fs.existsSync(sessionPlanPath)) {
  console.error("Missing ableton_session_plan.json");
  process.exit(1);
}

const sessionPlan = JSON.parse(fs.readFileSync(sessionPlanPath, "utf8"));

const manifest = {
  created_at: new Date().toISOString(),
  status: "stub_ready",
  source_plan: "engine/output/ableton_session_plan.json",
  project: sessionPlan.project,
  actions: [
    ...sessionPlan.groups.map((group) => ({
      type: "create_group_track",
      group_id: group.id,
      name: group.name,
      priority: group.priority
    })),
    ...sessionPlan.tracks.map((track) => ({
      type: "create_midi_track",
      track_id: track.track_id,
      group: track.group,
      name: track.ableton_name,
      source: track.source,
      clip_start_bar: track.clip_start_bar,
      clip_end_bar: track.clip_end_bar,
      writes_to: track.writes_to,
      device_chain: track.device_chain
    })),
    ...sessionPlan.locators.map((locator) => ({
      type: "create_locator",
      name: locator.name,
      start_bar: locator.start_bar,
      end_bar: locator.end_bar,
      start_time: locator.start_time,
      end_time: locator.end_time,
      energy: locator.energy
    }))
  ],
  render_policy: sessionPlan.render_policy,
  routing: sessionPlan.routing
};

fs.writeFileSync(outPath, JSON.stringify(manifest, null, 2));

console.log("Ableton Builder stub manifest created.");
console.log(`Saved: ${outPath}`);
console.log(`Actions: ${manifest.actions.length}`);
