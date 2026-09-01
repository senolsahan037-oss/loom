import fs from "fs";
import path from "path";

const sessionPlanPath = "engine/output/ableton_session_plan.json";
const sourceRoot = "engine/output";

if (!fs.existsSync(sessionPlanPath)) {
  console.error("Missing ableton_session_plan.json");
  process.exit(1);
}

const sessionPlan = JSON.parse(fs.readFileSync(sessionPlanPath, "utf8"));

const safeName = (sessionPlan.project?.name || "ArrangementGPS_Project")
  .replace(/[^a-z0-9]+/gi, "_")
  .replace(/^_+|_+$/g, "");

const buildDir = path.join("Builds", safeName);
const tracksDir = path.join(buildDir, "tracks");

fs.mkdirSync(tracksDir, { recursive: true });

const filesToCopy = [
  "ableton_session_plan.json",
  "ableton_build_manifest.json",
  "ableton_build_plan.json",
  "normalized_blueprint.json",
  "status.json"
];

for (const file of filesToCopy) {
  const src = path.join(sourceRoot, file);
  if (fs.existsSync(src)) {
    fs.copyFileSync(src, path.join(buildDir, file));
  }
}

const trackManifest = [];

for (const track of sessionPlan.tracks ?? []) {
  const trackFile = `${track.track_id.replaceAll(".", "_")}.json`;
  const outPath = path.join(tracksDir, trackFile);

  const payload = {
    track_id: track.track_id,
    group: track.group,
    ableton_name: track.ableton_name,
    writes_to: track.writes_to,
    clip_start_bar: track.clip_start_bar,
    clip_end_bar: track.clip_end_bar,
    mute_regions: track.mute_regions,
    section_activity: track.section_activity ?? {},
    source: track.source,
    output_type: track.output_type,
    instrument_family: track.instrument_family ?? null,
    sensei_role: track.sensei_role ?? null,
    device_chain: track.device_chain
  };

  fs.writeFileSync(outPath, JSON.stringify(payload, null, 2));

  trackManifest.push({
    track_id: track.track_id,
    file: `tracks/${trackFile}`,
    ableton_name: track.ableton_name,
    writes_to: track.writes_to
  });
}

const packageManifest = {
  created_at: new Date().toISOString(),
  package_type: "arrangementgps_ableton_project_package",
  project: sessionPlan.project,
  track_count: trackManifest.length,
  tracks: trackManifest,
  next_step: "Ableton Builder should read this package and create groups, tracks, locators and clips."
};

fs.writeFileSync(
  path.join(buildDir, "package_manifest.json"),
  JSON.stringify(packageManifest, null, 2)
);

console.log("Project package created:");
console.log(buildDir);
console.log(`Tracks: ${trackManifest.length}`);
